import { Injectable } from '@angular/core';

/**
 * Previsión del tiempo para el lugar y la hora de un plan.
 * Fuente: Open-Meteo (gratis, sin clave, combina modelos europeos y de AEMET).
 * Da previsión por horas hasta 16 días vista; más allá todavía no hay datos.
 */
export interface Prevision {
  estado: 'ok' | 'lejos' | 'pasado' | 'sin-datos';
  emoji?: string;
  texto?: string;
  temperatura?: number;
  probLluvia?: number;
  viento?: number;
  /** Se espera lluvia o tormenta a esa hora: se muestra el aviso. */
  lluvia?: boolean;
}

interface HorasApi {
  time: string[];
  temperature_2m: number[];
  precipitation_probability: (number | null)[];
  precipitation: (number | null)[];
  weather_code: number[];
  wind_speed_10m: number[];
}

const DIAS_PREVISION = 16;
const ZONA = 'Europe/Madrid';

/** Código del tiempo (WMO) → emoticono y texto. */
function describir(codigo: number): { emoji: string; texto: string; lluvia: boolean } {
  if (codigo === 0) return { emoji: '☀️', texto: 'Despejado', lluvia: false };
  if (codigo <= 2) return { emoji: '🌤️', texto: 'Poco nuboso', lluvia: false };
  if (codigo === 3) return { emoji: '☁️', texto: 'Nublado', lluvia: false };
  if (codigo === 45 || codigo === 48) return { emoji: '🌫️', texto: 'Niebla', lluvia: false };
  if (codigo >= 51 && codigo <= 57) return { emoji: '🌦️', texto: 'Llovizna', lluvia: true };
  if (codigo >= 61 && codigo <= 67) return { emoji: '🌧️', texto: 'Lluvia', lluvia: true };
  if (codigo >= 71 && codigo <= 77) return { emoji: '❄️', texto: 'Nieve', lluvia: true };
  if (codigo >= 80 && codigo <= 82) return { emoji: '🌧️', texto: 'Chubascos', lluvia: true };
  if (codigo === 85 || codigo === 86) return { emoji: '🌨️', texto: 'Chubascos de nieve', lluvia: true };
  if (codigo >= 95) return { emoji: '⛈️', texto: 'Tormenta', lluvia: true };
  return { emoji: '🌤️', texto: 'Variable', lluvia: false };
}

/** 'AAAA-MM-DDTHH:00' en hora de España (así vienen las horas de Open-Meteo). */
function horaLocal(d: Date): string {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: ZONA,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(d)
      .map((x) => [x.type, x.value])
  );
  return `${p['year']}-${p['month']}-${p['day']}T${p['hour']}:00`;
}

@Injectable({ providedIn: 'root' })
export class TiempoService {
  /** Una descarga por zona (los planes de un mismo pueblo comparten la previsión). */
  private cache = new Map<string, Promise<HorasApi | null>>();

  private horas(lat: number, lng: number): Promise<HorasApi | null> {
    const clave = `${lat.toFixed(2)},${lng.toFixed(2)}`;
    if (!this.cache.has(clave)) {
      const url =
        'https://api.open-meteo.com/v1/forecast' +
        `?latitude=${lat.toFixed(3)}&longitude=${lng.toFixed(3)}` +
        '&hourly=temperature_2m,precipitation_probability,precipitation,weather_code,wind_speed_10m' +
        `&timezone=${encodeURIComponent(ZONA)}&forecast_days=${DIAS_PREVISION}`;
      const peticion = fetch(url)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => (d?.hourly as HorasApi) || null)
        .catch(() => null);
      this.cache.set(clave, peticion);
      // Si falla, se podrá reintentar más tarde
      peticion.then((h) => {
        if (!h) this.cache.delete(clave);
      });
    }
    return this.cache.get(clave)!;
  }

  async prevision(lat: number | null | undefined, lng: number | null | undefined, inicio: string | null | undefined): Promise<Prevision> {
    if (lat == null || lng == null || !inicio) return { estado: 'sin-datos' };
    const fecha = new Date(inicio);
    if (isNaN(fecha.getTime())) return { estado: 'sin-datos' };
    const ahora = Date.now();
    if (fecha.getTime() < ahora - 3 * 3600000) return { estado: 'pasado' };
    if (fecha.getTime() > ahora + (DIAS_PREVISION - 1) * 86400000) return { estado: 'lejos' };

    const h = await this.horas(Number(lat), Number(lng));
    if (!h) return { estado: 'sin-datos' };
    const i = h.time.indexOf(horaLocal(fecha));
    if (i < 0) return { estado: 'sin-datos' };

    const codigo = h.weather_code[i];
    const d = describir(codigo);
    const prob = h.precipitation_probability[i] ?? 0;
    const mm = h.precipitation[i] ?? 0;
    const viento = Math.round(h.wind_speed_10m[i] ?? 0);
    const ventoso = viento >= 35 && !d.lluvia;
    return {
      estado: 'ok',
      emoji: ventoso ? '💨' : d.emoji,
      texto: ventoso ? `Viento (${viento} km/h)` : d.texto,
      temperatura: Math.round(h.temperature_2m[i]),
      probLluvia: prob,
      viento,
      lluvia: d.lluvia || prob >= 50 || mm >= 1,
    };
  }
}
