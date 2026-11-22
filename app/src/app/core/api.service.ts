import { Injectable, inject } from '@angular/core';
import { environment } from '../../environments/environment';
import { SupabaseService } from './supabase.service';
import { EventoExtraido } from './models';

/** Error del servidor con su código HTTP (401, 403, 409…), para dar mensajes claros. */
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

export interface EstadoAgente {
  apify_configurado: boolean;
  ia_configurada: boolean;
  corriendo: boolean;
  inicio: string | null;
  fin: string | null;
  motivo: string | null;
  resultado: Record<string, number> | null;
  error: string | null;
  cada_minutos: number;
  proxima_en_minutos: number;
  /** Lo que ha hecho la última búsqueda, publicación a publicación. */
  detalle?: { resultado: string; donde: string; fecha: string; texto: string; url: string | null }[];
}

// El servidor gratuito de Render puede tardar ~50 s en despertar
const TIEMPO_MAXIMO_MS = 65000;

@Injectable({
  providedIn: 'root',
})
export class ApiService {
  private sb = inject(SupabaseService);
  private baseUrl = environment.apiUrl;

  private async getHeaders(auth = true): Promise<Record<string, string>> {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (auth) {
      const { data } = await this.sb.client.auth.getSession();
      const token = data.session?.access_token;
      if (token) headers['Authorization'] = `Bearer ${token}`;
    }
    return headers;
  }

  private async peticion<T>(ruta: string, opciones: { method?: string; body?: unknown; auth?: boolean } = {}): Promise<T> {
    const control = new AbortController();
    const temporizador = setTimeout(() => control.abort(), TIEMPO_MAXIMO_MS);
    try {
      const res = await fetch(`${this.baseUrl}${ruta}`, {
        method: opciones.method || 'GET',
        headers: await this.getHeaders(opciones.auth ?? true),
        body: opciones.body !== undefined ? JSON.stringify(opciones.body) : undefined,
        signal: control.signal,
      });
      if (!res.ok) {
        // El servidor explica el motivo en "detail" (ya en español): se usa si viene
        const detalle = await res.json().then((d) => (typeof d?.detail === 'string' ? d.detail : '')).catch(() => '');
        throw new ApiError(res.status, detalle || `HTTP ${res.status}`);
      }
      const texto = await res.text();
      return (texto ? JSON.parse(texto) : {}) as T;
    } finally {
      clearTimeout(temporizador);
    }
  }

  health(): Promise<{ ok: boolean }> {
    return this.peticion('/health', { auth: false });
  }

  extraerEvento(payload: { text?: string; image_url?: string }): Promise<EventoExtraido> {
    return this.peticion('/ia/extraer', { method: 'POST', body: payload });
  }

  avisarEvento(eventId: string, tipo: 'aprobado' | 'rechazado' | 'cambio_hora' | 'cancelacion' | 'aplazado'): Promise<{ enviados: number }> {
    return this.peticion(`/eventos/${eventId}/avisar`, { method: 'POST', body: { tipo } });
  }

  getIcsUrl(eventId: string): string {
    return `${this.baseUrl}/eventos/${eventId}.ics`;
  }

  chatIa(eventId: string, pregunta: string): Promise<{ respuesta: string; bloqueado?: boolean }> {
    return this.peticion(`/eventos/${eventId}/chat-ia`, { method: 'POST', body: { pregunta } });
  }

  // Panel → Demo (con el token del moderador)
  ingestaFacebook(payload: { source_url: string; page_url: string; text: string; image_url?: string }): Promise<{ ingesta_id: string; estado: string }> {
    return this.peticion('/ingesta/facebook', { method: 'POST', body: payload });
  }

  enviarRecomendaciones(userId?: string): Promise<{ enviados: number }> {
    return this.peticion('/recomendaciones/enviar', { method: 'POST', body: userId ? { user_id: userId } : {} });
  }

  /** Cambia la valoración que YA hizo el usuario (estrellas y, si se pasa, el comentario). */
  cambiarResena(eventId: string, estrellas: number, comentario?: string | null): Promise<any> {
    return this.peticion(`/resenas/${eventId}`, { method: 'PUT', body: { estrellas, comentario } });
  }

  pedirResenas(eventId: string): Promise<{ enviados: number }> {
    return this.peticion(`/resenas/pedir/${eventId}`, { method: 'POST' });
  }

  revisarPromociones(): Promise<{ promovidos: any[]; a_revisar: any[] }> {
    return this.peticion('/moderacion/promociones/revisar', { method: 'POST' });
  }

  decidirPromocion(userId: string, accion: 'promover' | 'descartar' | 'revocar'): Promise<{ ok: boolean; rol: string }> {
    return this.peticion('/moderacion/promociones/decidir', { method: 'POST', body: { user_id: userId, accion } });
  }

  /** Detectados por IA → plan pendiente, con su organizador real (no el moderador). */
  crearPlanDeIngesta(ingestaId: string, forzar = false): Promise<{ id: string; status: string }> {
    return this.peticion(`/moderacion/ingestas/${ingestaId}/crear-plan${forzar ? '?forzar=true' : ''}`, { method: 'POST' });
  }

  /** Búsqueda de eventos en Facebook: qué hizo la última vez y si está configurada. */
  estadoAgente(): Promise<EstadoAgente> {
    return this.peticion('/moderacion/agente');
  }

  /** Lanza la búsqueda al momento (texto vacío = el nombre de los 5 pueblos). */
  buscarAhora(texto?: string, guardar = false): Promise<{ lanzado: boolean; consultas: string[] }> {
    return this.peticion('/moderacion/agente/buscar', { method: 'POST', body: { texto: texto || null, guardar } });
  }

  /** Varios idiomas: traduce textos de la web (sin sesión). */
  traducir(idioma: string, textos: string[]): Promise<{ traducciones: string[] }> {
    return this.peticion('/traducir', { method: 'POST', body: { idioma, textos }, auth: false });
  }

  /** Borra la cuenta de quien tiene la sesión abierta. */
  borrarCuenta(): Promise<{ ok: boolean }> {
    return this.peticion('/auth/cuenta', { method: 'DELETE' });
  }

  // Acceso: crear cuenta con teléfono y entrar con Telegram (sin sesión)
  registroTelefono(datos: { telefono: string; password: string; nombre: string; email?: string | null }): Promise<{ email_acceso: string }> {
    return this.peticion('/auth/registro', { method: 'POST', body: datos, auth: false });
  }

  telegramIniciar(): Promise<{ codigo: string; enlace: string }> {
    return this.peticion('/auth/telegram/iniciar', { method: 'POST', auth: false });
  }

  telegramEstado(codigo: string): Promise<{ listo: boolean; token_hash?: string }> {
    return this.peticion(`/auth/telegram/estado/${encodeURIComponent(codigo)}`, { auth: false });
  }

  getIaConsumo(): Promise<{ llamadas: number; tokens: number }> {
    return this.peticion('/ia/consumo', { auth: false });
  }
}
