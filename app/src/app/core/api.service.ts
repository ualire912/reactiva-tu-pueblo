import { Injectable, inject } from '@angular/core';
import { environment } from '../../environments/environment';
import { SupabaseService } from './supabase.service';
import { EventoExtraido } from './models';

/** Error del servidor con su código HTTP (401, 403, 409…), para dar mensajes claros. */
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
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
      if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
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

  avisarEvento(eventId: string, tipo: 'aprobado' | 'rechazado' | 'cambio_hora' | 'cancelacion'): Promise<{ enviados: number }> {
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

  getIaConsumo(): Promise<{ llamadas: number; tokens: number }> {
    return this.peticion('/ia/consumo', { auth: false });
  }
}
