import { Component, computed, inject, input } from '@angular/core';
import { RouterModule } from '@angular/router';
import { IonIcon } from '@ionic/angular';
import { addIcons } from 'ionicons';
import {
  accessibility,
  arrowForward,
  bicycleOutline,
  calendarOutline,
  colorPaletteOutline,
  constructOutline,
  heartOutline,
  location,
  musicalNotesOutline,
  people,
  restaurantOutline,
  sparklesOutline,
  star,
  timeOutline,
  walkOutline,
} from 'ionicons/icons';
import { SupabaseService } from '../../core/supabase.service';
import { TiempoEventoComponent } from '../tiempo-evento/tiempo-evento.component';
import { EventoVista } from '../../core/eventos.service';
import {
  estiloCategoria,
  fechaLarga,
  haTerminado,
  iconoCategoriaLinea,
  partesFecha,
  plazasLibres,
  porcentajeOcupacion,
  textoPlazas,
  textoPrecio,
  textoValoracion,
  ubicacionCompleta,
} from '../../core/ui';

/** Tarjeta de evento para la cuadrícula: cartel, datos clave y llamada a apuntarse. */
@Component({
  selector: 'app-evento-card',
  standalone: true,
  imports: [RouterModule, IonIcon, TiempoEventoComponent],
  templateUrl: './evento-card.component.html',
})
export class EventoCardComponent {
  private sb = inject(SupabaseService);

  ev = input.required<EventoVista>();

  constructor() {
    addIcons({
      accessibility,
      arrowForward,
      bicycleOutline,
      calendarOutline,
      colorPaletteOutline,
      constructOutline,
      heartOutline,
      location,
      musicalNotesOutline,
      people,
      restaurantOutline,
      sparklesOutline,
      star,
      timeOutline,
      walkOutline,
    });
  }

  categoria = computed(() => {
    this.sb.catalogos();
    return this.sb.categorias.find((c) => c.slug === this.ev().category);
  });
  categoriaNombre = computed(() => this.categoria()?.nombre || 'Actividad');
  iconoLinea = computed(() => iconoCategoriaLinea(this.ev().category));
  estilo = computed(() => estiloCategoria(this.ev().category));

  fecha = computed(() => partesFecha(this.ev().starts_at));
  fechaCompleta = computed(() => fechaLarga(this.ev().starts_at));
  precio = computed(() => textoPrecio(this.ev().price));
  gratis = computed(() => !Number(this.ev().price));
  ubicacion = computed(() => ubicacionCompleta(this.ev().location_name, this.ev().pueblo));
  terminado = computed(() => this.ev().status !== 'cancelado' && haTerminado(this.ev()));
  plazas = computed(() => textoPlazas(this.ev()));
  ocupacion = computed(() => porcentajeOcupacion(this.ev()));
  libres = computed(() => plazasLibres(this.ev()));
  valoracion = computed(() => textoValoracion(this.ev().organizador_media));

  /** Cinta inferior del cartel (los finalizados llevan el sello rojo central). */
  cinta = computed<{ texto: string; clase: string } | null>(() => {
    const ev = this.ev();
    if (this.terminado()) return null;
    switch (ev.status) {
      case 'cancelado':
        return { texto: 'Cancelado', clase: 'cinta-cancelado' };
      case 'pospuesto':
        return { texto: 'Aplazado', clase: 'cinta-aplazado' };
      case 'completo':
        return { texto: 'Completo · lista de espera', clase: 'cinta-completo' };
      case 'inscripcion_cerrada':
        return { texto: 'Inscripción cerrada', clase: 'cinta-cerrada' };
      case 'en_curso':
        return { texto: 'En curso ahora', clase: 'cinta-curso' };
    }
    const libres = this.libres();
    if (libres !== null && libres > 0 && libres <= 3) return { texto: '¡Últimas plazas!', clase: 'cinta-ultimas' };
    return null;
  });

  cta = computed(() => {
    if (this.terminado()) return 'Ver fotos y opiniones';
    switch (this.ev().status) {
      case 'abierto':
        return 'Apúntate';
      case 'completo':
        return 'Lista de espera';
      default:
        return 'Ver plan';
    }
  });

  ctaDestacada = computed(() => !this.terminado() && (this.ev().status === 'abierto' || this.ev().status === 'completo'));

  etiquetaValoracion = computed(() => {
    const ev = this.ev();
    if (ev.organizador_media == null) return 'Organizador todavía sin valoraciones';
    return `Valoración del organizador: ${this.valoracion()} de 5, con ${ev.organizador_resenas} ${ev.organizador_resenas === 1 ? 'opinión' : 'opiniones'}`;
  });
}
