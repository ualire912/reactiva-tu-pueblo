import { Injectable } from '@angular/core';

export interface ResultadoGeo {
  lat: number;
  lng: number;
  etiqueta: string;
  /** false si no se encontró el lugar exacto y se usó el centro del pueblo. */
  exacto: boolean;
}

/**
 * Busca la latitud y longitud de un lugar con OpenStreetMap (Nominatim).
 * Gratis y sin clave. Se usa poco (al escribir el lugar en el formulario).
 */
@Injectable({ providedIn: 'root' })
export class GeocodingService {
  private cache = new Map<string, Omit<ResultadoGeo, 'exacto'> | null>();

  async buscar(lugar: string, pueblo: string | null | undefined): Promise<ResultadoGeo | null> {
    const l = (lugar || '').trim();
    const p = (pueblo || '').trim();
    if (!l && !p) return null;

    // 1º lugar + pueblo; 2º solo el pueblo (si el lugar no se encuentra)
    const intentos = [
      [l, p, 'Almería', 'España'].filter(Boolean).join(', '),
      ...(l && p ? [[p, 'Almería', 'España'].join(', ')] : []),
    ];

    for (let i = 0; i < intentos.length; i++) {
      const r = await this.consultar(intentos[i]);
      if (r) return { ...r, exacto: i === 0 && !!l };
    }
    return null;
  }

  private async consultar(q: string): Promise<Omit<ResultadoGeo, 'exacto'> | null> {
    if (this.cache.has(q)) return this.cache.get(q) ?? null;

    const url =
      'https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=es&accept-language=es&q=' +
      encodeURIComponent(q);

    const control = new AbortController();
    const temporizador = setTimeout(() => control.abort(), 10000);
    try {
      const res = await fetch(url, { headers: { Accept: 'application/json' }, signal: control.signal });
      if (!res.ok) return null;
      const datos = (await res.json()) as { lat: string; lon: string; display_name: string }[];
      const primero = datos?.[0];
      const resultado = primero
        ? { lat: Number(primero.lat), lng: Number(primero.lon), etiqueta: primero.display_name }
        : null;
      this.cache.set(q, resultado);
      return resultado;
    } catch {
      return null;
    } finally {
      clearTimeout(temporizador);
    }
  }
}
