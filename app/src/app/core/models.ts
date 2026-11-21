export type RolUsuario = 'visitante' | 'usuario' | 'publicador' | 'moderador';
export type EstadoEvento =
  | 'borrador_ia'
  | 'pendiente_moderacion'
  | 'abierto'
  | 'completo'
  | 'inscripcion_cerrada'
  | 'en_curso'
  | 'finalizado'
  | 'pospuesto'
  | 'cancelado'
  | 'rechazado';

export type OrigenEvento = 'web' | 'telegram' | 'facebook' | 'moderador';
export type EstadoInscripcion = 'confirmada' | 'lista_de_espera' | 'cancelada';
export type TipoTransporte = 'ninguno' | 'ofrece' | 'busca';
export type TipoMensaje = 'pregunta' | 'respuesta' | 'anuncio_fijado' | 'asistente_ia';
export type EstadoIncidencia = 'abierta' | 'resuelta';
export type EstadoIngesta = 'nueva' | 'extraida' | 'esperando_confirmacion' | 'confirmada' | 'descartada' | 'spam' | 'error';

export interface Municipio {
  id: number;
  nombre: string;
  lat: number;
  lng: number;
}

export interface Categoria {
  slug: string;
  nombre: string;
  icono: string;
}

export interface PreferenciasUsuario {
  categorias: string[];
  municipios: string[];
  audio: boolean;
}

export interface Profile {
  id: string;
  full_name: string | null;
  email: string | null;
  rol: RolUsuario;
  telegram_chat_id: number | null;
  telefono: string | null;
  telegram_linked: boolean;
  link_code: string;
  redes_sociales?: Record<string, string>;
  edad: number | null;
  genero: string | null;
  direccion: string | null;
  municipio_id: number | null;
  preferencias: PreferenciasUsuario;
  notificaciones_activas: boolean;
  reputation_score: number;
  promocion_pendiente: boolean;
  promocion_motivo?: string | null;
  promocion_revisada_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface Entidad {
  id: string;
  owner_user_id: string | null;
  municipio_id: number | null;
  nombre: string;
  facebook_page_url: string | null;
  auto_approve: boolean;
}

export interface Evento {
  id: string;
  municipio_id: number;
  entidad_organizadora_id?: string | null;
  created_by?: string;
  title: string;
  category: string;
  starts_at: string;
  ends_at?: string | null;
  inscripcion_hasta?: string | null;
  duration_minutes?: number | null;
  location_name: string | null;
  lat?: number | null;
  lng?: number | null;
  min_age?: number | null;
  max_age?: number | null;
  description: string | null;
  price: number;
  max_capacity: number | null;
  confirmed_count: number;
  poster_url: string | null;
  status: EstadoEvento;
  is_accessible: boolean;
  sequence: number;
  origen?: OrigenEvento;
  source_url?: string | null;
  ai_confidence?: number | null;
  ai_flags?: any;
  moderation_note?: string | null;
  created_at?: string;
  municipios?: { nombre: string };
  categorias?: { nombre: string; icono: string };
}

export interface EventoExtraido {
  title: string | null;
  category: string | null;
  starts_at: string | null;
  ends_at: string | null;
  inscripcion_hasta?: string | null;
  duration_minutes: number | null;
  municipio: string | null;
  location_name: string | null;
  price: number | null;
  max_capacity: number | null;
  min_age: number | null;
  max_age: number | null;
  description: string | null;
  is_accessible: boolean;
  confidence: number;
  missing: string[];
}

export interface Inscripcion {
  id: string;
  user_id: string;
  event_id: string;
  status: EstadoInscripcion;
  transporte: TipoTransporte;
  transporte_plazas: number;
  transporte_desde_municipio_id: number | null;
  asistio?: boolean;
  created_at?: string;
  eventos?: Evento;
}

export interface Mensaje {
  id: string;
  event_id: string;
  user_id: string | null;
  parent_id?: string | null;
  tipo: TipoMensaje;
  contenido: string;
  created_at: string;
  autor_nombre?: string;
}

export interface Resena {
  id: string;
  event_id: string;
  user_id: string;
  estrellas: number;
  comentario: string | null;
  foto_url: string | null;
}

export interface Incidencia {
  id: string;
  event_id: string;
  user_id: string;
  comentario: string;
  estado: EstadoIncidencia;
  eventos?: { title: string };
}

export interface Ingesta {
  id: string;
  origen: OrigenEvento;
  source_url: string | null;
  entidad_id: string | null;
  user_id: string | null;
  raw_text: string | null;
  image_url: string | null;
  extracted: EventoExtraido | null;
  missing: string[] | null;
  spam_score: number | null;
  spam_reason: string | null;
  estado: EstadoIngesta;
  event_id: string | null;
  created_at: string;
}

export type MensajeEvento = Mensaje;