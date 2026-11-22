import { Injectable, inject } from '@angular/core';
import { ApiService } from './api.service';

/**
 * Varios idiomas: traduce los textos que hay en pantalla (y los que van apareciendo:
 * planes, chat, avisos...) con la IA del servidor (POST /traducir). La web está escrita
 * en español; cada frase se traduce una vez y se recuerda en este dispositivo.
 * Para no traducir algo, se le pone translate="no" o la clase "notranslate".
 */
export const IDIOMAS_WEB: { codigo: string; nombre: string }[] = [
  { codigo: 'es', nombre: 'Español' },
  { codigo: 'en', nombre: 'English' },
  { codigo: 'fr', nombre: 'Français' },
  { codigo: 'de', nombre: 'Deutsch' },
  { codigo: 'ro', nombre: 'Română' },
  { codigo: 'ar', nombre: 'العربية' },
];

const ATRIBUTOS = ['placeholder', 'aria-label', 'title'];
const SALTAR = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEXTAREA', 'CODE', 'svg', 'SVG']);
const CLAVE_CACHE = 'reactiva-traducciones-';

@Injectable({ providedIn: 'root' })
export class TraductorService {
  private api = inject(ApiService);
  private idioma = 'es';
  private cache = new Map<string, string>();
  private pendientes = new Set<string>();
  private esperando = new Map<string, ((t: string) => void)[]>();
  private temporizador?: ReturnType<typeof setTimeout>;
  private observador?: MutationObserver;
  /** Lo que hemos escrito nosotros (para no volver a traducir nuestra propia traducción). */
  private puesto = new WeakMap<Node, string>();
  private puestoAttr = new WeakMap<Element, Record<string, string>>();

  activar(idioma: string) {
    if (idioma === this.idioma) return;
    if (idioma === 'es') {
      // Volver al español: lo más limpio es recargar (los textos originales vuelven solos)
      if (this.idioma !== 'es') location.reload();
      return;
    }
    this.idioma = idioma;
    this.cargarCache();
    this.recorrer(document.body);
    this.observador?.disconnect();
    this.observador = new MutationObserver((cambios) => {
      for (const c of cambios) {
        if (c.type === 'childList') c.addedNodes.forEach((n) => this.recorrer(n));
        else if (c.type === 'characterData') this.textoNodo(c.target as Text);
        else if (c.type === 'attributes' && c.attributeName) this.atributo(c.target as Element, c.attributeName);
      }
    });
    this.observador.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATRIBUTOS });
  }

  // ---------- Recorrer la página ----------
  private recorrer(raiz: Node) {
    if (raiz.nodeType === Node.TEXT_NODE) {
      this.textoNodo(raiz as Text);
      return;
    }
    if (!(raiz instanceof Element) || this.excluido(raiz)) return;
    const caminante = document.createTreeWalker(raiz, NodeFilter.SHOW_TEXT);
    let nodo: Node | null;
    while ((nodo = caminante.nextNode())) this.textoNodo(nodo as Text);
    for (const el of [raiz, ...Array.from(raiz.querySelectorAll('[placeholder],[aria-label],[title]'))]) {
      for (const a of ATRIBUTOS) if (el.hasAttribute(a)) this.atributo(el, a);
    }
  }

  private excluido(el: Element | null): boolean {
    for (let e = el; e; e = e.parentElement) {
      if (SALTAR.has(e.tagName) || e.getAttribute('translate') === 'no' || e.classList?.contains('notranslate')) return true;
    }
    return false;
  }

  private traducible(t: string): boolean {
    const s = t.trim();
    return s.length >= 2 && /[a-záéíóúñü]{2}/i.test(s) && !/^https?:\/\//.test(s);
  }

  private textoNodo(nodo: Text) {
    const valor = nodo.nodeValue || '';
    if (this.puesto.get(nodo) === valor || !this.traducible(valor) || this.excluido(nodo.parentElement)) return;
    const original = valor.trim();
    this.pedir(original, (t) => {
      if (nodo.nodeValue !== valor) return; // Angular ya lo cambió: se traducirá lo nuevo
      const nuevo = valor.replace(original, t);
      this.puesto.set(nodo, nuevo);
      nodo.nodeValue = nuevo;
    });
  }

  private atributo(el: Element, nombre: string) {
    const valor = el.getAttribute(nombre) || '';
    const puestos = this.puestoAttr.get(el) || {};
    if (puestos[nombre] === valor || !this.traducible(valor) || this.excluido(el)) return;
    this.pedir(valor.trim(), (t) => {
      if (el.getAttribute(nombre) !== valor) return;
      this.puestoAttr.set(el, { ...(this.puestoAttr.get(el) || {}), [nombre]: t });
      el.setAttribute(nombre, t);
    });
  }

  // ---------- Pedir traducciones (en lotes) ----------
  private pedir(texto: string, listo: (t: string) => void) {
    const ya = this.cache.get(texto);
    if (ya !== undefined) {
      listo(ya);
      return;
    }
    const lista = this.esperando.get(texto) || [];
    lista.push(listo);
    this.esperando.set(texto, lista);
    this.pendientes.add(texto);
    clearTimeout(this.temporizador);
    this.temporizador = setTimeout(() => this.enviar(), 250);
  }

  private async enviar() {
    const idioma = this.idioma;
    const textos = Array.from(this.pendientes).slice(0, 150);
    textos.forEach((t) => this.pendientes.delete(t));
    if (!textos.length) return;
    try {
      const { traducciones } = await this.api.traducir(idioma, textos);
      textos.forEach((t, i) => {
        const tr = traducciones?.[i] || t;
        this.cache.set(t, tr);
        (this.esperando.get(t) || []).forEach((f) => f(tr));
        this.esperando.delete(t);
      });
      this.guardarCache();
    } catch {
      textos.forEach((t) => this.esperando.delete(t)); // sin servidor: se queda en español
    }
    if (this.pendientes.size) this.temporizador = setTimeout(() => this.enviar(), 50);
  }

  private cargarCache() {
    try {
      const datos = JSON.parse(localStorage.getItem(CLAVE_CACHE + this.idioma) || '{}');
      this.cache = new Map(Object.entries(datos));
    } catch {
      this.cache = new Map();
    }
  }

  private guardarCache() {
    try {
      const entradas = Array.from(this.cache.entries()).slice(-4000);
      localStorage.setItem(CLAVE_CACHE + this.idioma, JSON.stringify(Object.fromEntries(entradas)));
    } catch {
      /* sin almacenamiento: se vuelve a pedir la próxima vez */
    }
  }
}
