/**
 * Utilidades de presentación compartidas por toda la app:
 * colores por pueblo, etiquetas de estado, fechas en hora de España y textos.
 */
import { EstadoEvento, Evento } from './models';

export const ZONA = 'Europe/Madrid';

// ---------------------------------------------------------------------------
// Colores por pueblo (todos con contraste ≥ 4.5:1 sobre texto blanco)
// Si en el futuro hay muchos pueblos, los que no estén aquí usan COLOR_PUEBLO_DEFECTO.
// ---------------------------------------------------------------------------
export const COLOR_PUEBLO_DEFECTO = '#5e6866';

const COLORES_PUEBLO: Record<string, string> = {
  'lucainena de las torres': '#4e4386', // violeta
  velefique: '#2a5a86', // azul
  senes: '#94461a', // ocre
  'uleila del campo': '#8a3b5c', // frambuesa
  tahal: '#2b6f68', // verde azulado
};

/** Telegram en modo "hablar con una persona del equipo" (el bot pasa el mensaje a los moderadores). */
export function enlaceHablarConPersona(bot = 'ReactivaPuebloBot'): string {
  return `https://t.me/${bot}?start=persona`;
}

export function normalizar(texto: string | null | undefined): string {
  return (texto || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .trim();
}

// ---------------------------------------------------------------------------
// Chat público: insultos y palabrotas que no se publican
// (misma lista que el servidor, api/ia.py, y la BD, supabase/migracion_chat.sql)
// ---------------------------------------------------------------------------
const INSULTOS_CHAT =
  /\b(gilipollas?|imbeciles?|idiotas?|estupid[oa]s?|subnormal(es)?|retrasad[oa]s?|mierdas?|put[ao]s?|putada|cabron(es|a|as)?|cabrona?zo|hij[oa]s? de puta|maricon(es)?|bolleras?|zorras?|pollas?|pollon|follar|follamos|follando|capull[oa]s?|pendej[oa]s?|mamon(es|a|as)?|me cago en|cago en|joder|jodete|jodeos|panchit[oa]s?|sudac[oa]s?|negrat[ao]s?)\b/;

/** Minúsculas, sin tildes, sin letras repetidas de más ("pollaaaa" → "polla") y sin trucos (gilip0llas). */
function normalizarFiltro(texto: string): string {
  const cambios: Record<string, string> = { '0': 'o', '1': 'i', '3': 'e', '4': 'a', '@': 'a', $: 's' };
  return normalizar(texto)
    .replace(/[0134@$]/g, (c) => cambios[c])
    .replace(/(.)\1{2,}/g, '$1');
}

export function esOfensivo(texto: string | null | undefined): boolean {
  const t = texto || '';
  return INSULTOS_CHAT.test(normalizarFiltro(t)) || /coñ[oa]/i.test(t);
}

/**
 * Modo daltonismo (Ajustes): paleta Okabe-Ito oscurecida para que el texto blanco
 * siga leyéndose. Azul, bermellón, verde azulado, púrpura y gris se distinguen
 * con protanopía, deuteranopía y tritanopía.
 */
const COLORES_PUEBLO_DALTONISMO: Record<string, string> = {
  'lucainena de las torres': '#6a3d7a',
  velefique: '#005a8c',
  senes: '#a8480a',
  'uleila del campo': '#3b3b3b',
  tahal: '#00785a',
};

export function colorPueblo(nombre: string | null | undefined): string {
  const daltonismo = typeof document !== 'undefined' && document.documentElement.classList.contains('modo-daltonismo');
  return (daltonismo ? COLORES_PUEBLO_DALTONISMO : COLORES_PUEBLO)[normalizar(nombre)] || COLOR_PUEBLO_DEFECTO;
}

// ---------------------------------------------------------------------------
// Estados
// ---------------------------------------------------------------------------
export const ESTADOS_VISIBLES: EstadoEvento[] = [
  'abierto',
  'completo',
  'inscripcion_cerrada',
  'en_curso',
  'pospuesto',
  'cancelado',
  'finalizado',
];

const ETIQUETAS_ESTADO: Record<string, string> = {
  borrador_ia: 'Borrador',
  pendiente_moderacion: 'Pendiente de revisión',
  abierto: 'Abierto',
  completo: 'Completo · lista de espera',
  inscripcion_cerrada: 'Inscripción cerrada',
  en_curso: 'En curso',
  finalizado: 'Finalizado',
  pospuesto: 'Aplazado',
  cancelado: 'Cancelado',
  rechazado: 'Rechazado',
};

export function etiquetaEstado(status: string | null | undefined): string {
  return ETIQUETAS_ESTADO[status || ''] || 'Sin estado';
}

/** Clase CSS para la cinta de estado de las tarjetas. */
export function claseEstado(status: string | null | undefined): string {
  return 'estado-' + (status || 'desconocido');
}

/** Color de Ionic para badges de estado. */
export function colorEstado(status: string | null | undefined): string {
  switch (status) {
    case 'abierto':
      return 'success';
    case 'completo':
    case 'pospuesto':
    case 'pendiente_moderacion':
    case 'borrador_ia':
      return 'warning';
    case 'en_curso':
      return 'tertiary';
    case 'finalizado':
    case 'cancelado':
    case 'rechazado':
      return 'danger';
    default:
      return 'medium';
  }
}

// ---------------------------------------------------------------------------
// Ingestas (panel)
// ---------------------------------------------------------------------------
const ETIQUETAS_INGESTA: Record<string, string> = {
  nueva: 'Nueva',
  extraida: 'Datos leídos',
  esperando_confirmacion: 'Esperando respuesta del organizador',
  confirmada: 'Confirmada',
  descartada: 'Descartada',
  spam: 'Publicidad no deseada',
  error: 'No se pudo leer',
};
export function etiquetaIngesta(estado: string | null | undefined): string {
  return ETIQUETAS_INGESTA[estado || ''] || 'Sin estado';
}

const ETIQUETAS_ORIGEN: Record<string, string> = {
  web: 'Web',
  telegram: 'Telegram',
  facebook: 'Facebook',
  moderador: 'Moderación',
};
export function etiquetaOrigen(origen: string | null | undefined): string {
  return ETIQUETAS_ORIGEN[origen || ''] || 'Web';
}

/** Traduce los nombres de campo que devuelve la IA (missing) a lenguaje normal. */
const NOMBRES_CAMPO: Record<string, string> = {
  title: 'título',
  category: 'tipo de actividad',
  starts_at: 'fecha y hora',
  ends_at: 'hora de fin',
  duration_minutes: 'duración',
  municipio: 'pueblo',
  municipio_id: 'pueblo',
  location_name: 'lugar',
  price: 'precio',
  max_capacity: 'plazas',
  min_age: 'edad mínima',
  max_age: 'edad máxima',
  description: 'descripción',
  is_accessible: 'accesibilidad',
  inscripcion_hasta: 'fecha límite de inscripción',
};
export function nombreCampo(campo: string): string {
  return NOMBRES_CAMPO[campo] || campo.replace(/_/g, ' ');
}
export function listaCampos(campos: string[] | null | undefined): string {
  return (campos || []).map(nombreCampo).join(', ');
}

// ---------------------------------------------------------------------------
// Fechas (siempre en hora de España)
// ---------------------------------------------------------------------------
const fmtLargo = new Intl.DateTimeFormat('es-ES', {
  timeZone: ZONA,
  weekday: 'long',
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});
const fmtCorto = new Intl.DateTimeFormat('es-ES', {
  timeZone: ZONA,
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});
const fmtHora = new Intl.DateTimeFormat('es-ES', { timeZone: ZONA, hour: '2-digit', minute: '2-digit' });
const fmtDiaSemana = new Intl.DateTimeFormat('es-ES', { timeZone: ZONA, weekday: 'short' });
const fmtDia = new Intl.DateTimeFormat('es-ES', { timeZone: ZONA, day: 'numeric' });
const fmtMes = new Intl.DateTimeFormat('es-ES', { timeZone: ZONA, month: 'short' });
const fmtAnio = new Intl.DateTimeFormat('es-ES', { timeZone: ZONA, year: 'numeric' });
const fmtYmd = new Intl.DateTimeFormat('en-CA', { timeZone: ZONA, year: 'numeric', month: '2-digit', day: '2-digit' });
const fmtDow = new Intl.DateTimeFormat('en-US', { timeZone: ZONA, weekday: 'short' });

function valida(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? null : d;
}

export function fechaLarga(iso: string | null | undefined): string {
  const d = valida(iso);
  return d ? fmtLargo.format(d) : '';
}
export function fechaCorta(iso: string | null | undefined): string {
  const d = valida(iso);
  return d ? fmtCorto.format(d) : '';
}
export function partesFecha(iso: string | null | undefined): { dia: string; num: string; mes: string; anio: string; hora: string } {
  const d = valida(iso);
  if (!d) return { dia: '', num: '', mes: '', anio: '', hora: '' };
  return {
    dia: fmtDiaSemana.format(d).replace('.', ''),
    num: fmtDia.format(d),
    mes: fmtMes.format(d).replace('.', ''),
    anio: fmtAnio.format(d),
    hora: fmtHora.format(d),
  };
}

/** 'YYYY-MM-DD' del día en España. */
export function ymdMadrid(d: Date): string {
  return fmtYmd.format(d);
}

/** Día de la semana en España: 0 = domingo … 6 = sábado. */
export function diaSemanaMadrid(d: Date): number {
  const mapa: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  return mapa[fmtDow.format(d)] ?? d.getDay();
}

/** Fecha ISO → valor para <input type="datetime-local"> en hora local del navegador. */
export function isoALocalInput(iso: string | null | undefined): string {
  const d = valida(iso);
  if (!d) return '';
  const offset = d.getTimezoneOffset() * 60000;
  return new Date(d.getTime() - offset).toISOString().slice(0, 16);
}

// ---------------------------------------------------------------------------
// Textos de evento
// ---------------------------------------------------------------------------
export function textoPrecio(price: number | null | undefined): string {
  const p = Number(price) || 0;
  if (p === 0) return 'Gratis';
  return `${p.toLocaleString('es-ES', { maximumFractionDigits: 2 })} €`;
}

/** "Plaza de la Iglesia, Velefique" — sin repetir el pueblo si ya aparece. */
export function ubicacionCompleta(lugar: string | null | undefined, pueblo: string | null | undefined): string {
  const l = (lugar || '').trim();
  const p = (pueblo || '').trim();
  if (!l || normalizar(l) === 'por confirmar') return p ? `${p} · lugar por confirmar` : 'Lugar por confirmar';
  if (!p || normalizar(l).includes(normalizar(p))) return l;
  return `${l}, ${p}`;
}

export function plazasLibres(ev: Pick<Evento, 'max_capacity' | 'confirmed_count'>): number | null {
  if (!ev.max_capacity) return null;
  return Math.max(0, ev.max_capacity - (ev.confirmed_count || 0));
}

export function textoPlazas(ev: Pick<Evento, 'max_capacity' | 'confirmed_count'>): string {
  const libres = plazasLibres(ev);
  if (libres === null) return 'Sin límite de plazas';
  if (libres === 0) return 'Plazas agotadas';
  if (libres === 1) return '¡Queda 1 plaza!';
  return `${libres} plazas libres de ${ev.max_capacity}`;
}

export function porcentajeOcupacion(ev: Pick<Evento, 'max_capacity' | 'confirmed_count'>): number {
  if (!ev.max_capacity) return 0;
  return Math.min(100, Math.round(((ev.confirmed_count || 0) / ev.max_capacity) * 100));
}

export function textoValoracion(media: number | null | undefined): string {
  if (media == null) return '';
  return media.toLocaleString('es-ES', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

export function haTerminado(ev: Pick<Evento, 'status' | 'starts_at' | 'ends_at'>, ahora = new Date()): boolean {
  if (ev.status === 'finalizado') return true;
  if (ev.status === 'cancelado') return false;
  const fin = ev.ends_at ? new Date(ev.ends_at) : new Date(new Date(ev.starts_at).getTime() + 2 * 3600000);
  return fin < ahora;
}

// ---------------------------------------------------------------------------
// Iconos por categoría (registrados en app.component.ts)
// ---------------------------------------------------------------------------
const ICONOS_CATEGORIA: Record<string, string> = {
  senderismo: 'walk',
  deporte: 'bicycle',
  cultura: 'color-palette',
  gastronomia: 'restaurant',
  fiestas: 'musical-notes',
  taller: 'construct',
  voluntariado: 'heart',
  otro: 'sparkles',
};
export function iconoCategoria(slug: string | null | undefined): string {
  return ICONOS_CATEGORIA[slug || ''] || 'sparkles';
}

/** Icono de línea (para las cabeceras de color de las tarjetas). */
export function iconoCategoriaLinea(slug: string | null | undefined): string {
  return iconoCategoria(slug) + '-outline';
}

/**
 * Paleta «Plaza al sol»: cada tipo de actividad tiene un fondo suave y una tinta oscura
 * del mismo tono. Todos los fondos comparten luminosidad, así que combinan entre sí,
 * y la tinta sobre su fondo cumple contraste AA.
 */
const ESTILOS_CATEGORIA: Record<string, { fondo: string; tinta: string }> = {
  senderismo: { fondo: '#ddeeea', tinta: '#1b4844' },
  deporte: { fondo: '#e4efd6', tinta: '#3e5a1e' },
  cultura: { fondo: '#e9e4f6', tinta: '#4e4386' },
  gastronomia: { fondo: '#fce5d3', tinta: '#94461a' },
  fiestas: { fondo: '#dceaf6', tinta: '#2a5a86' },
  taller: { fondo: '#fdf0d2', tinta: '#6b4a00' },
  voluntariado: { fondo: '#f6e1e8', tinta: '#8a3b5c' },
  otro: { fondo: '#f4efe6', tinta: '#4a4540' },
};
export function estiloCategoria(slug: string | null | undefined): { fondo: string; tinta: string } {
  return ESTILOS_CATEGORIA[slug || ''] || ESTILOS_CATEGORIA['otro'];
}
