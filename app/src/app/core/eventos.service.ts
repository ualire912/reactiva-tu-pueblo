import { Injectable, inject } from '@angular/core';
import { SupabaseService } from './supabase.service';
import { Evento } from './models';
import { ESTADOS_VISIBLES, colorPueblo, diaSemanaMadrid, haTerminado, normalizar, ymdMadrid } from './ui';

/** Evento listo para pintar: con pueblo, color y organizador con su valoración. */
export interface EventoVista extends Evento {
  pueblo: string;
  color: string;
  organizador_nombre: string;
  organizador_media: number | null;
  organizador_resenas: number;
  /** Valoración de ESTE plan (media de sus opiniones) y cuántas tiene. */
  media_evento: number | null;
  resenas_evento: number;
  /** La opinión más útil para enseñar en la tarjeta (buena nota y con texto). */
  resena_destacada: { estrellas: number; comentario: string } | null;
}

export type FiltroFecha = 'proximos' | 'hoy' | 'finde' | 'semana' | 'pasados' | 'cancelados' | 'todas';

export interface Filtros {
  /** Texto libre del buscador (título, pueblo, organizador, lugar, descripción). */
  texto?: string;
  municipio: number | null;
  categoria: string | null;
  fecha: FiltroFecha;
  accesible: boolean;
  cerca: boolean;
}

export const FILTROS_INICIALES: Filtros = {
  texto: '',
  municipio: null,
  categoria: null,
  fecha: 'proximos',
  accesible: false,
  cerca: false,
};

export const OPCIONES_FECHA: { valor: FiltroFecha; texto: string }[] = [
  { valor: 'proximos', texto: 'Eventos próximos' },
  { valor: 'hoy', texto: 'Hoy' },
  { valor: 'finde', texto: 'Este finde' },
  { valor: 'semana', texto: 'Próximos 7 días' },
  { valor: 'pasados', texto: 'Pasados' },
  { valor: 'cancelados', texto: 'Cancelados' },
  { valor: 'todas', texto: 'Todas las fechas' },
];

@Injectable({ providedIn: 'root' })
export class EventosService {
  private sb = inject(SupabaseService);

  /** Todos los eventos visibles (no solo "abierto"), enriquecidos. */
  async cargarVisibles(): Promise<EventoVista[]> {
    const { data, error } = await this.sb.client
      .from('eventos')
      .select('*, municipios(nombre)')
      .in('status', ESTADOS_VISIBLES)
      .order('starts_at', { ascending: true });

    if (error) throw error;
    return this.enriquecer((data || []) as Evento[]);
  }

  /**
   * Añade pueblo, color y organizador (+ media de estrellas de todos sus eventos).
   * `referencia` = eventos usados para calcular la media (por defecto, los mismos).
   */
  async enriquecer(eventos: Evento[], referencia: Evento[] = eventos): Promise<EventoVista[]> {
    const todos = [...referencia, ...eventos];
    const entidadIds = [...new Set(todos.map((e) => e.entidad_organizadora_id).filter((x): x is string => !!x))];
    const autorIds = [...new Set(todos.map((e) => e.created_by).filter((x): x is string => !!x))];

    const vacio = { data: [] as any[] };
    const consultas: PromiseLike<any>[] = [
      entidadIds.length ? this.sb.client.from('entidades').select('id, nombre').in('id', entidadIds) : Promise.resolve(vacio),
      autorIds.length ? this.sb.client.from('perfiles_publicos').select('id, full_name').in('id', autorIds) : Promise.resolve(vacio),
      this.sb.client.from('resenas').select('event_id, estrellas, comentario').limit(5000),
    ];
    const [entRes, perfRes, resRes]: any[] = await Promise.all(consultas).catch(() => [vacio, vacio, vacio]);

    const nombreEntidad = new Map<string, string>((entRes?.data || []).map((x: any) => [x.id, x.nombre]));
    const nombrePerfil = new Map<string, string>((perfRes?.data || []).map((x: any) => [x.id, x.full_name || '']));

    const claveOrg = (e: Evento): string | null =>
      e.entidad_organizadora_id ? 'e:' + e.entidad_organizadora_id : e.created_by ? 'u:' + e.created_by : null;

    const orgDeEvento = new Map<string, string>();
    for (const e of todos) {
      const k = claveOrg(e);
      if (k) orgDeEvento.set(e.id, k);
    }

    // Por plan: media, número de opiniones y la opinión destacada
    const porEvento = new Map<string, { total: number; n: number; destacada: { estrellas: number; comentario: string } | null }>();
    for (const r of (resRes?.data || []) as { event_id: string; estrellas: number; comentario: string | null }[]) {
      const acc = porEvento.get(r.event_id) || { total: 0, n: 0, destacada: null };
      acc.total += Number(r.estrellas) || 0;
      acc.n += 1;
      const texto = (r.comentario || '').trim();
      if (texto.length >= 8) {
        const mejor = acc.destacada;
        // Gana la de más estrellas; a igualdad, la más explicada (hasta 160 letras)
        if (!mejor || r.estrellas > mejor.estrellas || (r.estrellas === mejor.estrellas && Math.min(texto.length, 160) > Math.min(mejor.comentario.length, 160))) {
          acc.destacada = { estrellas: r.estrellas, comentario: texto };
        }
      }
      porEvento.set(r.event_id, acc);
    }

    const suma = new Map<string, { total: number; n: number }>();
    for (const r of (resRes?.data || []) as { event_id: string; estrellas: number }[]) {
      const k = orgDeEvento.get(r.event_id);
      if (!k) continue;
      const acc = suma.get(k) || { total: 0, n: 0 };
      acc.total += Number(r.estrellas) || 0;
      acc.n += 1;
      suma.set(k, acc);
    }

    return eventos.map((e) => {
      const pueblo = e.municipios?.nombre || this.sb.municipios.find((m) => m.id === e.municipio_id)?.nombre || 'Comarca';
      const k = claveOrg(e);
      const acc = k ? suma.get(k) : undefined;
      const nombre =
        (e.entidad_organizadora_id && nombreEntidad.get(e.entidad_organizadora_id)) ||
        (e.created_by && nombrePerfil.get(e.created_by)) ||
        'Vecinos de la comarca';
      return {
        ...e,
        pueblo,
        color: colorPueblo(pueblo),
        organizador_nombre: nombre,
        organizador_media: acc && acc.n ? acc.total / acc.n : null,
        organizador_resenas: acc?.n || 0,
        media_evento: porEvento.get(e.id)?.n ? porEvento.get(e.id)!.total / porEvento.get(e.id)!.n : null,
        resenas_evento: porEvento.get(e.id)?.n || 0,
        resena_destacada: porEvento.get(e.id)?.destacada || null,
      };
    });
  }
}

// ---------------------------------------------------------------------------
// Filtrado (común a Agenda, Mapa e Inicio)
// ---------------------------------------------------------------------------

/** Busca cada palabra (sin tildes ni mayúsculas) en los textos visibles del plan. */
export function coincideBusqueda(ev: EventoVista, palabras: string[]): boolean {
  const texto = normalizar(
    [ev.title, ev.pueblo, ev.organizador_nombre, ev.location_name, ev.category, ev.description].filter(Boolean).join(' ')
  );
  return palabras.every((p) => texto.includes(p));
}

export function filtrarEventos(
  eventos: EventoVista[],
  f: Filtros,
  coords: { lat: number; lng: number } | null = null,
  ahora = new Date()
): EventoVista[] {
  const hoy = ymdMadrid(ahora);
  const en7 = new Date(ahora.getTime() + 7 * 86400000);
  const diasFinde = diasDelFinde(ahora);

  const palabras = normalizar(f.texto).split(/\s+/).filter(Boolean);

  let lista = eventos.filter((ev) => {
    if (palabras.length && !coincideBusqueda(ev, palabras)) return false;
    if (f.municipio != null && ev.municipio_id !== f.municipio) return false;
    if (f.categoria && ev.category !== f.categoria) return false;
    if (f.accesible && !ev.is_accessible) return false;

    const inicio = new Date(ev.starts_at);
    const terminado = haTerminado(ev, ahora);
    const cancelado = ev.status === 'cancelado';

    switch (f.fecha) {
      case 'proximos':
        return !cancelado && !terminado;
      case 'hoy':
        return !cancelado && ymdMadrid(inicio) === hoy;
      case 'finde':
        return !cancelado && !terminado && diasFinde.includes(ymdMadrid(inicio));
      case 'semana':
        return !cancelado && !terminado && inicio <= en7;
      case 'pasados':
        return !cancelado && terminado;
      case 'cancelados':
        return cancelado;
      default:
        return true;
    }
  });

  if (f.cerca && coords) {
    lista = [...lista].sort(
      (a, b) =>
        distanciaKm(coords.lat, coords.lng, a.lat ?? 37.1, a.lng ?? -2.3) -
        distanciaKm(coords.lat, coords.lng, b.lat ?? 37.1, b.lng ?? -2.3)
    );
  } else {
    const desc = f.fecha === 'pasados' || f.fecha === 'cancelados';
    lista = [...lista].sort((a, b) => {
      const d = new Date(a.starts_at).getTime() - new Date(b.starts_at).getTime();
      return desc ? -d : d;
    });
  }
  return lista;
}

/** Días (YYYY-MM-DD) del fin de semana actual o próximo: viernes, sábado y domingo. */
function diasDelFinde(ahora: Date): string[] {
  const dow = diaSemanaMadrid(ahora); // 0 dom … 6 sáb
  const hastaViernes = dow === 0 ? -2 : dow === 6 ? -1 : 5 - dow;
  const dias: string[] = [];
  for (let i = 0; i < 3; i++) {
    dias.push(ymdMadrid(new Date(ahora.getTime() + (hastaViernes + i) * 86400000)));
  }
  return dias;
}

export function distanciaKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}
