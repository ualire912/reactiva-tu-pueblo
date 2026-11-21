import { Injectable, inject } from '@angular/core';
import { environment } from '../../environments/environment';
import { SupabaseService } from './supabase.service';
import { EventoExtraido } from './models';

@Injectable({
  providedIn: 'root'
})
export class ApiService {
  private sb = inject(SupabaseService);
  private baseUrl = environment.apiUrl;

  private async getHeaders(auth = true): Promise<Record<string, string>> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json'
    };
    if (auth) {
      const { data } = await this.sb.client.auth.getSession();
      const token = data.session?.access_token;
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }
    }
    return headers;
  }

  async health(): Promise<any> {
    const res = await fetch(`${this.baseUrl}/health`);
    return res.json();
  }

  async extraerEvento(payload: { text?: string; image_url?: string }): Promise<EventoExtraido> {
    const res = await fetch(`${this.baseUrl}/ia/extraer`, {
      method: 'POST',
      headers: await this.getHeaders(true),
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error('Error al extraer datos con IA');
    return res.json();
  }

  async avisarEvento(eventId: string, tipo: 'aprobado' | 'rechazado' | 'cambio_hora' | 'cancelacion'): Promise<any> {
    const res = await fetch(`${this.baseUrl}/eventos/${eventId}/avisar`, {
      method: 'POST',
      headers: await this.getHeaders(true),
      body: JSON.stringify({ tipo })
    });
    return res.json();
  }

  getIcsUrl(eventId: string): string {
    return `${this.baseUrl}/eventos/${eventId}.ics`;
  }

  async chatIa(eventId: string, pregunta: string): Promise<{ respuesta: string }> {
    const res = await fetch(`${this.baseUrl}/eventos/${eventId}/chat-ia`, {
      method: 'POST',
      headers: await this.getHeaders(true),
      body: JSON.stringify({ pregunta })
    });
    if (!res.ok) throw new Error('Error al consultar al asistente');
    return res.json();
  }

  // Endpoints del panel / Demo (A2) — Ahora todos llevan token Bearer
  async ingestaFacebook(payload: { source_url: string; page_url: string; text: string; image_url?: string }): Promise<any> {
    const res = await fetch(`${this.baseUrl}/ingesta/facebook`, {
      method: 'POST',
      headers: await this.getHeaders(true),
      body: JSON.stringify(payload)
    });
    return res.json();
  }

  async enviarRecomendaciones(userId?: string): Promise<any> {
    const res = await fetch(`${this.baseUrl}/recomendaciones/enviar`, {
      method: 'POST',
      headers: await this.getHeaders(true),
      body: JSON.stringify(userId ? { user_id: userId } : {})
    });
    return res.json();
  }

  async pedirResenas(eventId: string): Promise<any> {
    const res = await fetch(`${this.baseUrl}/resenas/pedir/${eventId}`, {
      method: 'POST',
      headers: await this.getHeaders(true)
    });
    return res.json();
  }

  async revisarPromociones(): Promise<{ promovidos: any[]; a_revisar: any[] }> {
    const res = await fetch(`${this.baseUrl}/moderacion/promociones/revisar`, {
      method: 'POST',
      headers: await this.getHeaders(true)
    });
    return res.json();
  }

  async decidirPromocion(userId: string, accion: 'promover' | 'descartar' | 'revocar'): Promise<any> {
    const res = await fetch(`${this.baseUrl}/moderacion/promociones/decidir`, {
      method: 'POST',
      headers: await this.getHeaders(true),
      body: JSON.stringify({ user_id: userId, accion })
    });
    if (!res.ok) throw new Error('Error al decidir promoción');
    return res.json();
  }

  async getIaConsumo(): Promise<{ llamadas: number; tokens: number }> {
    const res = await fetch(`${this.baseUrl}/ia/consumo`);
    return res.json();
  }
}