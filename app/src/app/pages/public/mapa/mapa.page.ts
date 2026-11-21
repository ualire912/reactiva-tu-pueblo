import { Component, OnDestroy, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterModule } from '@angular/router';
import {
  IonHeader,
  IonToolbar,
  IonTitle,
  IonButtons,
  IonButton,
  IonContent,
  IonGrid,
  IonRow,
  IonCol,
  IonItem,
  IonSelect,
  IonSelectOption,
  IonChip,
  IonIcon,
  IonLabel,
  ToastController
} from '@ionic/angular';
import { addIcons } from 'ionicons';
import {
  navigateOutline,
  calendarOutline,
  listOutline
} from 'ionicons/icons';
import * as L from 'leaflet';
import { SupabaseService } from '../../../core/supabase.service';
import { Evento } from '../../../core/models';

@Component({
  selector: 'app-mapa',
  templateUrl: './mapa.page.html',
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
    IonContent,
    IonGrid,
    IonRow,
    IonCol,
    IonItem,
    IonSelect,
    IonSelectOption,
    IonChip,
    IonIcon,
    IonLabel
  ]
})
export class MapaPage implements OnDestroy {
  public sb = inject(SupabaseService);
  private router = inject(Router);
  private toastCtrl = inject(ToastController);

  private map?: L.Map;
  private markersLayer = L.layerGroup();
  private userMarker?: L.Marker;

  eventosTodos: Evento[] = [];
  eventosFiltrados: Evento[] = [];

  // Filtros
  filtroMunicipio: number | 'todos' = 'todos';
  filtroCategoria = 'todas';
  filtroFecha: 'todos' | 'hoy' | 'finde' | 'semana' = 'todos';

  private readonly coloresCategoria: Record<string, string> = {
    senderismo: '#16a34a',
    deporte: '#ea580c',
    cultura: '#9333ea',
    gastronomia: '#dc2626',
    fiestas: '#db2777',
    taller: '#0284c7',
    voluntariado: '#0d9488',
    otro: '#4b5563'
  };

  constructor() {
    addIcons({ navigateOutline, calendarOutline, listOutline });
  }

  async ionViewDidEnter() {
    this.initMap();
    await this.cargarEventos();
    setTimeout(() => {
      this.map?.invalidateSize();
    }, 150);
  }

  ionViewWillLeave() {
    this.destruirMapa();
  }

  ngOnDestroy() {
    this.destruirMapa();
  }

  private initMap() {
    if (this.map) return;

    this.map = L.map('mapa-comarca', {
      center: [37.15, -2.30],
      zoom: 11
    });

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18,
      attribution: '&copy; OpenStreetMap'
    }).addTo(this.map);

    this.markersLayer.addTo(this.map);

    // Captura clics en el botón "Ver ficha" dentro de los popups de Leaflet
    this.map.getContainer().addEventListener('click', (e: Event) => {
      const target = e.target as HTMLElement;
      const btn = target.closest('[data-event-id]') as HTMLElement | null;
      if (btn) {
        const eventId = btn.getAttribute('data-event-id');
        if (eventId) {
          this.router.navigate(['/evento', eventId]);
        }
      }
    });
  }

  private destruirMapa() {
    if (this.map) {
      this.map.remove();
      this.map = undefined;
    }
  }

  async cargarEventos() {
    try {
      const { data, error } = await this.sb.client
        .from('eventos')
        .select('*, municipios(nombre), categorias(nombre, icono)')
        .in('status', ['abierto', 'pospuesto'])
        .gte('starts_at', new Date().toISOString())
        .order('starts_at', { ascending: true });

      if (!error && data) {
        this.eventosTodos = data as Evento[];
      }
    } catch {
      // Sin claves aún, mantiene mapa limpio sin fallar
    } finally {
      this.aplicarFiltros();
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

    const en7Dias = new Date(ahora);
    en7Dias.setDate(ahora.getDate() + 7);

    this.eventosFiltrados = this.eventosTodos.filter((ev) => {
      if (this.filtroMunicipio !== 'todos' && ev.municipio_id !== Number(this.filtroMunicipio)) {
        return false;
      }
      if (this.filtroCategoria !== 'todas' && ev.category !== this.filtroCategoria) {
        return false;
      }
      const fechaEv = new Date(ev.starts_at);
      if (this.filtroFecha === 'hoy' && fechaEv > finHoy) return false;
      if (this.filtroFecha === 'semana' && fechaEv > en7Dias) return false;
      if (this.filtroFecha === 'finde') {
        const dia = fechaEv.getDay();
        if ((dia !== 0 && dia !== 6) || fechaEv > en7Dias) return false;
      }
      return true;
    });

    this.pintarMarcadores();
  }

  private pintarMarcadores() {
    if (!this.map) return;
    this.markersLayer.clearLayers();

    for (const ev of this.eventosFiltrados) {
      if (ev.lat == null || ev.lng == null) continue;

      const color = this.coloresCategoria[ev.category] || '#2563eb';
      const inicial = (ev.category || 'E').charAt(0).toUpperCase();

      const icon = L.divIcon({
        className: '',
        html: `<div class="custom-pin" style="background-color: ${color};"><span>${inicial}</span></div>`,
        iconSize: [34, 34],
        iconAnchor: [17, 34],
        popupAnchor: [0, -34]
      });

      const fechaTexto = this.formatearFecha(ev.starts_at);
      const pueblo = ev.municipios?.nombre || 'Comarca';
      const precio = ev.price === 0 ? 'Gratis' : `${ev.price} €`;

      const popupHtml = `
        <div style="min-width: 180px; font-family: sans-serif;">
          <strong style="font-size: 15px; display: block; margin-bottom: 4px;">${ev.title}</strong>
          <div style="font-size: 13px; color: #444; margin-bottom: 2px;">📍 ${pueblo}</div>
          <div style="font-size: 13px; color: #444; margin-bottom: 6px;">🗓 ${fechaTexto} · <b>${precio}</b></div>
          <button
            type="button"
            data-event-id="${ev.id}"
            style="width: 100%; padding: 8px; background: #2563eb; color: #fff; border: none; border-radius: 6px; font-weight: 600; cursor: pointer;">
            Ver ficha
          </button>
        </div>
      `;

      L.marker([ev.lat, ev.lng], { icon })
        .bindPopup(popupHtml)
        .addTo(this.markersLayer);
    }
  }

  centrarCercaDeMi() {
    if (!('geolocation' in navigator)) {
      this.mostrarAviso('Tu navegador no soporta ubicación');
      return;
    }

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { latitude, longitude } = pos.coords;
        if (!this.map) return;

        if (this.userMarker) {
          this.userMarker.remove();
        }

        const userIcon = L.divIcon({
          className: '',
          html: '<div class="user-pin" title="Tu ubicación"></div>',
          iconSize: [18, 18],
          iconAnchor: [9, 9]
        });

        this.userMarker = L.marker([latitude, longitude], { icon: userIcon })
          .bindPopup('Estás aquí')
          .addTo(this.map);

        this.map.setView([latitude, longitude], 13);
        this.mostrarAviso('Mapa centrado en tu ubicación');
      },
      () => this.mostrarAviso('Activa el permiso de ubicación en tu navegador')
    );
  }

  private formatearFecha(iso: string): string {
    return new Intl.DateTimeFormat('es-ES', {
      timeZone: 'Europe/Madrid',
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit'
    }).format(new Date(iso));
  }

  private async mostrarAviso(message: string) {
    const toast = await this.toastCtrl.create({ message, duration: 2200 });
    await toast.present();
  }
}