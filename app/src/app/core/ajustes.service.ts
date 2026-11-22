import { Injectable, signal } from '@angular/core';

/**
 * Ajustes de accesibilidad y comodidad. Se guardan en este dispositivo
 * (no hace falta cuenta) y se aplican al momento a toda la web.
 */
export interface Ajustes {
  /** Tamaño de todo (texto incluido), en %: 100 = normal. */
  tamano: number;
  tema: 'auto' | 'claro' | 'oscuro';
  /** Letra Atkinson Hyperlegible, pensada para baja visión. */
  legible: boolean;
  /** Colores distinguibles con daltonismo (paleta Okabe-Ito). */
  daltonismo: boolean;
  /** Mostrar siempre el texto junto a los botones que solo tienen icono. */
  etiquetas: boolean;
  /** Sin fotos que cambian solas ni sonidos que empiezan solos. */
  sinAutoplay: boolean;
  sonidos: boolean;
  vibracion: boolean;
  /** Lectura en voz alta: velocidad (0,5 – 1,5) y voz elegida. */
  vozVelocidad: number;
  vozNombre: string;
  /** Idioma de la web: es, en, fr, de, ro, ar (la IA traduce los textos). */
  idioma: string;
}

export const AJUSTES_POR_DEFECTO: Ajustes = {
  tamano: 100,
  tema: 'claro',
  legible: false,
  daltonismo: false,
  etiquetas: false,
  sinAutoplay: false,
  sonidos: false,
  vibracion: true,
  vozVelocidad: 0.95,
  vozNombre: '',
  idioma: 'es',
};

const CLAVE = 'reactiva-ajustes';
const FUENTE_LEGIBLE = 'https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap';

@Injectable({ providedIn: 'root' })
export class AjustesService {
  ajustes = signal<Ajustes>(leer());
  private oscuroSistema = window.matchMedia?.('(prefers-color-scheme: dark)');

  constructor() {
    this.aplicar();
    // En "automático", cambia solo cuando el móvil o el ordenador pasan a modo oscuro
    this.oscuroSistema?.addEventListener?.('change', () => this.aplicar());
  }

  get valor(): Ajustes {
    return this.ajustes();
  }

  cambiar(cambios: Partial<Ajustes>) {
    this.ajustes.set({ ...this.ajustes(), ...cambios });
    try {
      localStorage.setItem(CLAVE, JSON.stringify(this.ajustes()));
    } catch {
      /* sin almacenamiento: se aplica igual, pero no se recuerda */
    }
    this.aplicar();
  }

  restablecer() {
    try {
      localStorage.removeItem(CLAVE);
    } catch {
      /* nada */
    }
    this.ajustes.set({ ...AJUSTES_POR_DEFECTO });
    this.aplicar();
  }

  /** Pasa los ajustes a la página (clases en <html> y tamaño). */
  aplicar() {
    const a = this.ajustes();
    const html = document.documentElement;
    const oscuro = a.tema === 'oscuro' || (a.tema === 'auto' && !!this.oscuroSistema?.matches);
    html.classList.toggle('tema-oscuro', oscuro);
    html.classList.toggle('letra-legible', a.legible);
    html.classList.toggle('modo-daltonismo', a.daltonismo);
    html.classList.toggle('mostrar-etiquetas', a.etiquetas);
    html.style.setProperty('--rp-escala', String(a.tamano / 100));
    html.classList.toggle('escalado', a.tamano !== 100);
    html.lang = a.idioma || 'es';
    html.dir = a.idioma === 'ar' ? 'rtl' : 'ltr';
    if (a.legible && !document.getElementById('fuente-legible')) {
      const link = document.createElement('link');
      link.id = 'fuente-legible';
      link.rel = 'stylesheet';
      link.href = FUENTE_LEGIBLE;
      document.head.appendChild(link);
    }
  }

  // ---------- Sonido y vibración al confirmar o avisar ----------
  private audio?: AudioContext;

  /** 'ok' = tono agudo corto; 'error' = dos tonos graves. Solo si están activados. */
  respuesta(tipo: 'ok' | 'aviso' | 'error') {
    const a = this.ajustes();
    if (a.vibracion && 'vibrate' in navigator) {
      try {
        navigator.vibrate(tipo === 'error' ? [70, 50, 70] : 40);
      } catch {
        /* nada */
      }
    }
    if (!a.sonidos) return;
    try {
      this.audio ??= new AudioContext();
      const tonos = tipo === 'ok' ? [880] : tipo === 'aviso' ? [660] : [330, 260];
      tonos.forEach((f, i) => {
        const osc = this.audio!.createOscillator();
        const vol = this.audio!.createGain();
        const t = this.audio!.currentTime + i * 0.16;
        osc.frequency.value = f;
        vol.gain.setValueAtTime(0.12, t);
        vol.gain.exponentialRampToValueAtTime(0.001, t + 0.14);
        osc.connect(vol).connect(this.audio!.destination);
        osc.start(t);
        osc.stop(t + 0.15);
      });
    } catch {
      /* sin audio */
    }
  }

  // ---------- Lectura en voz alta ----------
  vocesEspanol(): SpeechSynthesisVoice[] {
    if (!('speechSynthesis' in window)) return [];
    return window.speechSynthesis.getVoices().filter((v) => v.lang.toLowerCase().startsWith('es'));
  }

  leer(texto: string) {
    if (!('speechSynthesis' in window) || !texto) return;
    window.speechSynthesis.cancel();
    const a = this.ajustes();
    const u = new SpeechSynthesisUtterance(texto);
    u.lang = 'es-ES';
    u.rate = a.vozVelocidad;
    const voz = this.vocesEspanol().find((v) => v.name === a.vozNombre);
    if (voz) u.voice = voz;
    window.speechSynthesis.speak(u);
  }

  callar() {
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  }
}

function leer(): Ajustes {
  try {
    const guardado = JSON.parse(localStorage.getItem(CLAVE) || 'null');
    return { ...AJUSTES_POR_DEFECTO, ...(guardado || {}) };
  } catch {
    return { ...AJUSTES_POR_DEFECTO };
  }
}
