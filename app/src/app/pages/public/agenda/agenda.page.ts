import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import {
  IonHeader,
  IonToolbar,
  IonTitle,
  IonButtons,
  IonButton,
  IonIcon,
  IonContent,
  IonRefresher,
  IonRefresherContent,
  IonGrid,
  IonRow,
  IonCol,
  IonItem,
  IonSelect,
  IonSelectOption,
  IonChip,
  IonLabel,
  IonCard,
  IonCardHeader,
  IonCardTitle,
  IonCardSubtitle,
  IonCardContent,
  IonBadge,
  IonSpinner,
  IonNote
} from '@ionic/angular';
import { addIcons } from 'ionicons';
import {
  locationOutline,
  volumeHighOutline,
  contrastOutline,
  textOutline,
  calendarOutline,
  accessibilityOutline,
  peopleOutline
} from 'ionicons/icons';
import { SupabaseService } from '../../../core/supabase.service';
import { Evento } from '../../../core/models';

@Component({
  selector: 'app-agenda',
  templateUrl: './agenda.page.html',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterModule,
    IonHeader,
    IonToolbar,
    IonTitle,
    IonButtons,
    IonButton,
    IonIcon,
    IonContent,
    IonRefresher,
    IonRefresherContent,
    IonGrid,
    IonRow,
    IonCol,
    IonItem,
    IonSelect,
    IonSelectOption,
    IonChip,
    IonLabel,
    IonCard,
    IonCardHeader,
    IonCardTitle,
    IonCardSubtitle,
    IonCardContent,
    IonBadge,
    IonSpinner,
    IonNote
  ]
})
export class AgendaPage implements OnInit, OnDestroy {
  public sb = inject(SupabaseService);

  eventos: Evento[] = [];
  eventosFiltrados: Evento[] = [];
  cargando = false;

  filtroMunicipio: number | 'todos' = 'todos';
  filtroCategoria: string = 'todas';
  filtroFecha: 'todos' | 'hoy' | 'finde' | 'semana' = 'todos';
  soloAccesibles = false;
  ordenCercaniaActivo = false;
  userCoords: { lat: number; lng: number } | null = null;

  tamanoLetra: 'normal' | 'lg' | 'xl' = 'normal';
  altoContraste = false;
  private realtimeChannel: any;

  constructor() {
    addIcons({
      locationOutline,
      volumeHighOutline,
      contrastOutline,
      textOutline,
      calendarOutline,
      accessibilityOutline,
      peopleOutline
    });
  }

  async ngOnInit() {
    await this.cargarEventos();
    this.realtimeChannel = this.sb.client
      .channel('eventos-agenda-realtime')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'eventos' }, () => {
        this.cargarEventos();
      })
      .subscribe();
  }

  ngOnDestroy() {
    if (this.realtimeChannel) {
      this.sb.client.removeChannel(this.realtimeChannel);
    }
  }

  async ionViewWillEnter() {
    await this.cargarEventos();
  }

  async cargarEventos(event?: any) {
    this.cargando = !event;
    try {
      const { data } = await this.sb.client
        .from('eventos')
        .select('*, municipios(nombre)')
        .in('status', [
          'abierto',
          'completo',
          'inscripcion_cerrada',
          'en_curso',
          'pospuesto',
          'cancelado',
          'finalizado'
        ])
        .order('starts_at', { ascending: true });

      if (data) {
        this.eventos = data as Evento[];
        this.aplicarFiltros();
      }
    } finally {
      this.cargando = false;
      if (event) {
        event.target.complete();
      }
    }
  }

  seleccionarCategoria(slug: string) {
    this.filtroCategoria = this.filtroCategoria === slug ? 'todas' : slug;
    this.aplicarFiltros();
  }

  aplicarFiltros() {
    const ahora = new Date();
    const finHoy = new Date(ahora);
    finHoy.setHours(23, 59, 59, 999);

    const finSemana = new Date(ahora);
    finSemana.setDate(ahora.getDate() + 7);

    let lista = this.eventos.filter((ev) => {
      if (this.filtroMunicipio !== 'todos' && ev.municipio_id !== Number(this.filtroMunicipio)) {
        return false;
      }
      if (this.filtroCategoria !== 'todas' && ev.category !== this.filtroCategoria) {
        return false;
      }
      if (this.soloAccesibles && !ev.is_accessible) {
        return false;
      }

      const fechaEv = new Date(ev.starts_at);
      if (this.filtroFecha === 'hoy') {
        return fechaEv >= ahora && fechaEv <= finHoy;
      }
      if (this.filtroFecha === 'semana') {
        return fechaEv >= ahora && fechaEv <= finSemana;
      }
      if (this.filtroFecha === 'finde') {
        const dia = fechaEv.getDay();
        return fechaEv >= ahora && fechaEv <= finSemana && (dia === 0 || dia === 5 || dia === 6);
      }

      return true;
    });

    if (this.ordenCercaniaActivo && this.userCoords) {
      lista = [...lista].sort((a, b) => {
        const distA = this.distanciaKm(this.userCoords!.lat, this.userCoords!.lng, a.lat || 37.1, a.lng || -2.25);
        const distB = this.distanciaKm(this.userCoords!.lat, this.userCoords!.lng, b.lat || 37.1, b.lng || -2.25);
        return distA - distB;
      });
    }

    this.eventosFiltrados = lista;
  }

  ordenarCercaDeMi() {
    if (this.ordenCercaniaActivo) {
      this.ordenCercaniaActivo = false;
      this.aplicarFiltros();
      return;
    }
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition((pos) => {
      this.userCoords = { lat: pos.coords.latitude, lng: pos.coords.longitude };
      this.ordenCercaniaActivo = true;
      this.aplicarFiltros();
    });
  }

  textoPlazas(ev: Evento): string {
    if (!ev.max_capacity) return 'Entrada libre';
    const libres = ev.max_capacity - (ev.confirmed_count || 0);
    return libres > 0 ? `${libres} de ${ev.max_capacity} plazas libres` : 'Lista de espera';
  }

  etiquetaEstado(status: string): string {
    const mapa: Record<string, string> = {
      abierto: 'Abierto',
      completo: 'Completo (lista de espera)',
      inscripcion_cerrada: 'Inscripción cerrada',
      en_curso: 'En curso',
      finalizado: 'Finalizado',
      pospuesto: 'Aplazado',
      cancelado: 'Cancelado'
    };
    return mapa[status] || status;
  }

  colorEstado(status: string): string {
    if (status === 'abierto') return 'success';
    if (status === 'completo' || status === 'en_curso') return 'warning';
    if (status === 'cancelado' || status === 'inscripcion_cerrada') return 'danger';
    return 'medium';
  }

  formatearFecha(iso: string): string {
    return new Intl.DateTimeFormat('es-ES', {
      timeZone: 'Europe/Madrid',
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit'
    }).format(new Date(iso));
  }

  leerEnVozAlta(ev: Evento) {
    if (!('speechSynthesis' in window)) return;
    window.speechSynthesis.cancel();
    const texto = `${ev.title}, en ${ev.municipios?.nombre || 'la comarca'}, el ${this.formatearFecha(ev.starts_at)}. ${ev.price === 0 ? 'Entrada gratuita' : 'Precio ' + ev.price + ' euros'}. ${ev.description || ''}`;
    const utter = new SpeechSynthesisUtterance(texto);
    utter.lang = 'es-ES';
    window.speechSynthesis.speak(utter);
  }

  cambiarTamanoLetra() {
    document.documentElement.classList.remove('font-lg', 'font-xl');
    if (this.tamanoLetra === 'normal') {
      this.tamanoLetra = 'lg';
      document.documentElement.classList.add('font-lg');
    } else if (this.tamanoLetra === 'lg') {
      this.tamanoLetra = 'xl';
      document.documentElement.classList.add('font-xl');
    } else {
      this.tamanoLetra = 'normal';
    }
  }

  toggleContraste() {
    this.altoContraste = !this.altoContraste;
    document.body.classList.toggle('high-contrast', this.altoContraste);
  }

  private distanciaKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
    const R = 6371;
    const dLat = ((lat2 - lat1) * Math.PI) / 180;
    const dLon = ((lon2 - lon1) * Math.PI) / 180;
    const a =
      Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }
}