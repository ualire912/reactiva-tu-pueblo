import { Injectable } from '@angular/core';

export interface ResultadoGeo {
  lat: number;
  lng: number;
  etiqueta: string;
  /** false si no se encontró el lugar exacto y se usó el centro del pueblo. */
  exacto: boolean;
}

type Anillo = number[][];

const ABREVIATURAS: [RegExp, string][] = [
  [/\bc\/\s*/gi, 'calle '],
  [/\bcl\.?\s+/gi, 'calle '],
  [/\bavda?\.?\s+/gi, 'avenida '],
  [/\bav\.\s*/gi, 'avenida '],
  [/\bpza?\.?\s+/gi, 'plaza '],
  [/\bpl\.\s*/gi, 'plaza '],
  [/\bctra\.?\s+/gi, 'carretera '],
  [/\bpje\.?\s+/gi, 'pasaje '],
  [/\bbda\.?\s+/gi, 'barriada '],
];
const VACIAS = new Set(['de', 'del', 'la', 'el', 'los', 'las', 'en', 'y', 'a', 'al', 'junto', 'frente', 'lugar', 'por', 'confirmar', 'municipal', 'calle', 'plaza', 'avenida']);

const norm = (t: string) =>
  (t || '')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .trim();

/**
 * Del texto del lugar ("Plaza Mayor", "C/ Almería 5", "la iglesia") a latitud y longitud,
 * SIEMPRE dentro del término municipal del pueblo (contornos de OpenStreetMap del mapa):
 *   1. OpenStreetMap (Nominatim), acotado a la zona del pueblo.
 *   2. Photon (tolera faltas y nombres aproximados), si coinciden las palabras importantes.
 *   3. Si no: el centro del pueblo (exacto = false).
 * La misma lógica está en el servidor (api/geo.py) para los planes de Telegram y Facebook.
 */
@Injectable({ providedIn: 'root' })
export class GeocodingService {
  private cache = new Map<string, ResultadoGeo | null>();
  private contornos?: Promise<Map<string, Anillo[]>>;

  async buscar(lugar: string, pueblo: string | null | undefined): Promise<ResultadoGeo | null> {
    const p = (pueblo || '').trim();
    const texto = this.limpiar(lugar || '', p);
    const clave = `${texto}|${p}`;
    if (this.cache.has(clave)) return this.cache.get(clave) ?? null;

    const anillos = (await this.cargarContornos()).get(norm(p));
    let resultado: ResultadoGeo | null = null;
    if (texto && anillos && !['por confirmar', 'lugar por confirmar'].includes(norm(texto))) {
      const caja = this.caja(anillos);
      resultado = (await this.nominatim(texto, p, caja, anillos)) || (await this.photon(texto, p, caja, anillos));
    }
    if (!resultado && p) {
      // No se encuentra el sitio exacto: el centro del pueblo (el pin no cae en otro pueblo)
      const centro = await this.nominatimLibre(`${p}, Almería, España`);
      if (centro) resultado = { ...centro, exacto: false };
    }
    this.cache.set(clave, resultado);
    return resultado;
  }

  /** 'C/ Almería, 5 (Senés)' → 'calle Almería'. */
  private limpiar(lugar: string, pueblo: string): string {
    let t = lugar.trim();
    for (const [patron, cambio] of ABREVIATURAS) t = t.replace(patron, cambio);
    t = t.replace(/\b(s\/n|sin numero|nº?\s*\d+|n\.?\s*\d+|\d+)\b/gi, ' ');
    if (pueblo) t = t.replace(new RegExp(pueblo.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), ' ');
    t = t.replace(/\b(almer[ií]a|espa[nñ]a)\b/gi, ' ');
    return t.replace(/[\s,;()]+/g, ' ').replace(/^[\s.,-]+|[\s.,-]+$/g, '');
  }

  private palabras(t: string): Set<string> {
    return new Set((norm(t).match(/[a-z0-9]+/g) || []).filter((p) => p.length > 2 && !VACIAS.has(p)));
  }

  private async cargarContornos(): Promise<Map<string, Anillo[]>> {
    this.contornos ??= fetch('assets/mapa/limites.geojson')
      .then((r) => (r.ok ? r.json() : null))
      .then((g) => {
        const mapa = new Map<string, Anillo[]>();
        for (const f of g?.features || []) {
          if (f.properties?.tipo !== 'pueblo') continue;
          const geo = f.geometry;
          const anillos: Anillo[] = geo.type === 'Polygon' ? [geo.coordinates[0]] : geo.coordinates.map((p: Anillo[]) => p[0]);
          mapa.set(norm(f.properties.nombre), anillos);
        }
        return mapa;
      })
      .catch(() => new Map());
    return this.contornos;
  }

  private caja(anillos: Anillo[]): [number, number, number, number] {
    const xs = anillos.flat().map((c) => c[0]);
    const ys = anillos.flat().map((c) => c[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }

  /** ¿El punto cae dentro del término municipal? */
  private dentro(lat: number, lng: number, anillos: Anillo[]): boolean {
    return anillos.some((anillo) => {
      let dentro = false;
      for (let i = 0, j = anillo.length - 1; i < anillo.length; j = i++) {
        const [xi, yi] = anillo[i];
        const [xj, yj] = anillo[j];
        if (yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi || 1e-12) + xi) dentro = !dentro;
      }
      return dentro;
    });
  }

  private async pedir(url: string): Promise<any> {
    const control = new AbortController();
    const temporizador = setTimeout(() => control.abort(), 10000);
    try {
      const res = await fetch(url, { headers: { Accept: 'application/json' }, signal: control.signal });
      return res.ok ? await res.json() : null;
    } catch {
      return null;
    } finally {
      clearTimeout(temporizador);
    }
  }

  private async nominatim(texto: string, pueblo: string, [o, s, e, n]: number[], anillos: Anillo[]): Promise<ResultadoGeo | null> {
    const url =
      'https://nominatim.openstreetmap.org/search?format=jsonv2&limit=3&countrycodes=es&accept-language=es&bounded=1' +
      `&viewbox=${o},${n},${e},${s}&q=${encodeURIComponent(`${texto}, ${pueblo}`)}`;
    const datos = ((await this.pedir(url)) || []) as { lat: string; lon: string; display_name: string }[];
    for (const d of datos) {
      const lat = Number(d.lat);
      const lng = Number(d.lon);
      if (this.dentro(lat, lng, anillos)) return { lat, lng, etiqueta: d.display_name, exacto: true };
    }
    return null;
  }

  private async photon(texto: string, pueblo: string, [o, s, e, n]: number[], anillos: Anillo[]): Promise<ResultadoGeo | null> {
    const url = `https://photon.komoot.io/api/?limit=5&bbox=${o},${s},${e},${n}&q=${encodeURIComponent(`${texto} ${pueblo}`)}`;
    const datos = await this.pedir(url);
    const buscadas = this.palabras(texto);
    if (!buscadas.size) return null;
    for (const f of datos?.features || []) {
      const props = f.properties || {};
      const nombre = `${props.name || ''} ${props.street || ''}`;
      const [lng, lat] = f.geometry?.coordinates || [];
      const encontradas = this.palabras(nombre);
      // Todas las palabras importantes ("salón cultural" no es "asociación cultural")
      if ([...buscadas].every((p) => encontradas.has(p)) && this.dentro(lat, lng, anillos)) {
        return { lat, lng, etiqueta: [props.name, props.street, props.city].filter(Boolean).join(', '), exacto: true };
      }
    }
    return null;
  }

  private async nominatimLibre(q: string): Promise<Omit<ResultadoGeo, 'exacto'> | null> {
    const url = 'https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=es&accept-language=es&q=' + encodeURIComponent(q);
    const d = (((await this.pedir(url)) || []) as { lat: string; lon: string; display_name: string }[])[0];
    return d ? { lat: Number(d.lat), lng: Number(d.lon), etiqueta: d.display_name } : null;
  }
}
