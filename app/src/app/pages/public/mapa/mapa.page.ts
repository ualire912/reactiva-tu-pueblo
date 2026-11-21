import { Component, OnDestroy, computed, effect, inject, signal } from '@angular/core';
import { Router, RouterModule } from '@angular/router';
import { IonHeader, IonToolbar, IonTitle, IonButtons, IonButton, IonContent, IonIcon } from '@ionic/angular';
import { addIcons } from 'ionicons';
import { listOutline, locate, mapOutline } from 'ionicons/icons';
import * as L from 'leaflet';
import { SupabaseService } from '../../../core/supabase.service';
import { AvisosService } from '../../../core/avisos.service';
import { EventoVista, EventosService, FILTROS_INICIALES, Filtros, filtrarEventos } from '../../../core/eventos.service';
import { FiltrosEventosComponent } from '../../../components/filtros-eventos/filtros-eventos.component';
import { colorPueblo, estiloCategoria, fechaCorta, haTerminado, iconoCategoria, normalizar, partesFecha, textoPrecio, ubicacionCompleta } from '../../../core/ui';

@Component({
  selector: 'app-mapa',
  templateUrl: './mapa.page.html',
  standalone: true,
  imports: [RouterModule, IonHeader, IonToolbar, IonTitle, IonButtons, IonButton, IonContent, IonIcon, FiltrosEventosComponent],
})
export class MapaPage implements OnDestroy {
  sb = inject(SupabaseService);
  private eventosSvc = inject(EventosService);
  private avisos = inject(AvisosService);
  private router = inject(Router);

  private map?: L.Map;
  private markersLayer = L.layerGroup();
  private limitesLayer?: L.GeoJSON;
  /** Contornos de la provincia y de los 5 pueblos (OpenStreetMap, guardados en assets). */
  private static limites?: Promise<any>;
  private userMarker?: L.Marker;

  eventos = signal<EventoVista[]>([]);
  filtros = signal<Filtros>({ ...FILTROS_INICIALES });
  vista = signal<'mapa' | 'lista'>('mapa');
  filtrados = computed(() => filtrarEventos(this.eventos(), this.filtros()));

  // Helpers para la plantilla
  fechaCorta = fechaCorta;
  textoPrecio = textoPrecio;
  ubicacionCompleta = ubicacionCompleta;
  partesFecha = partesFecha;
  estiloCategoria = estiloCategoria;

  nombreCategoria(slug: string | null | undefined): string {
    return this.sb.categorias.find((c) => c.slug === slug)?.nombre || 'Actividad';
  }

  constructor() {
    addIcons({ listOutline, locate, mapOutline });
    effect(() => {
      const lista = this.filtrados();
      this.pintarMarcadores(lista);
    });
  }

  async ionViewDidEnter() {
    this.initMap();
    await this.cargarEventos();
    setTimeout(() => this.map?.invalidateSize(), 150);
  }

  ionViewWillLeave() {
    this.destruirMapa();
  }

  ngOnDestroy() {
    this.destruirMapa();
  }

  cambiarVista(v: 'mapa' | 'lista') {
    this.vista.set(v);
    if (v === 'mapa') setTimeout(() => this.map?.invalidateSize(), 50);
  }

  private initMap() {
    if (this.map) return;
    this.map = L.map('mapa-comarca', { center: [37.15, -2.3], zoom: 11 });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18,
      attribution: '&copy; OpenStreetMap',
    }).addTo(this.map);
    this.pintarLimites();
    this.markersLayer.addTo(this.map);

    // Botón "Ver ficha" dentro de los globos del mapa
    this.map.getContainer().addEventListener('click', (e: Event) => {
      const btn = (e.target as HTMLElement).closest('[data-event-id]') as HTMLElement | null;
      const id = btn?.getAttribute('data-event-id');
      if (id) this.router.navigate(['/evento', id]);
    });
    this.pintarMarcadores(this.filtrados());
  }

  private destruirMapa() {
    if (this.map) {
      this.map.remove();
      this.map = undefined;
    }
  }

  async cargarEventos() {
    try {
      this.eventos.set(await this.eventosSvc.cargarVisibles());
    } catch (err) {
      this.avisos.error(err, 'No hemos podido cargar el mapa de planes.');
    }
  }

  /** Contorno de la provincia de Almería y de cada pueblo, con su color. Clic en un pueblo = filtrar. */
  private async pintarLimites() {
    MapaPage.limites ??= fetch('assets/mapa/limites.geojson').then((r) => (r.ok ? r.json() : null)).catch(() => null);
    const datos = await MapaPage.limites;
    if (!datos || !this.map) return;

    this.limitesLayer = L.geoJSON(datos, {
      style: (f) =>
        f?.properties?.tipo === 'provincia'
          ? { color: '#24312f', weight: 2, dashArray: '6 6', fillOpacity: 0, interactive: false }
          : { color: colorPueblo(f?.properties?.nombre), weight: 2.5, fillColor: colorPueblo(f?.properties?.nombre), fillOpacity: 0.12 },
      onEachFeature: (f, capa) => {
        const nombre = f.properties?.nombre as string;
        if (f.properties?.tipo === 'provincia') return;
        capa.bindTooltip(nombre, { sticky: true, className: 'tooltip-pueblo' });
        capa.on('mouseover', () => (capa as L.Path).setStyle({ fillOpacity: 0.25, weight: 3.5 }));
        capa.on('mouseout', () => (capa as L.Path).setStyle({ fillOpacity: 0.12, weight: 2.5 }));
        capa.on('click', () => {
          const m = this.sb.municipios.find((x) => normalizar(x.nombre) === normalizar(nombre));
          if (m) this.filtros.set({ ...this.filtros(), municipio: this.filtros().municipio === m.id ? null : m.id });
        });
      },
    }).addTo(this.map);
    this.limitesLayer.bringToBack();

    // Encuadre: los 5 pueblos (la provincia entera queda de fondo al alejar)
    const pueblos = L.geoJSON(datos.features.filter((f: any) => f.properties?.tipo === 'pueblo'));
    this.map.fitBounds(pueblos.getBounds(), { padding: [20, 20] });
  }

  private escapar(t: string | null | undefined): string {
    const mapa: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return (t || '').replace(/[&<>"']/g, (c) => mapa[c] || c);
  }

  private pintarMarcadores(lista: EventoVista[]) {
    if (!this.map) return;
    this.markersLayer.clearLayers();

    for (const ev of lista) {
      if (ev.lat == null || ev.lng == null) continue;
      const terminado = ev.status !== 'cancelado' && haTerminado(ev);
      const color = terminado || ev.status === 'cancelado' ? '#6f7977' : ev.color;

      const icon = L.divIcon({
        className: '',
        html: `<div class="custom-pin" style="background-color:${color}"><span><ion-icon name="${iconoCategoria(ev.category)}" aria-hidden="true"></ion-icon></span></div>`,
        iconSize: [34, 34],
        iconAnchor: [17, 34],
        popupAnchor: [0, -34],
      });

      const precio = textoPrecio(ev.price);
      const estado = terminado
        ? '<span class="popup-estado">Finalizado</span>'
        : ev.status === 'cancelado'
          ? '<span class="popup-estado">Cancelado</span>'
          : '';

      const popupHtml = `
        <div class="popup-evento">
          <span class="popup-pueblo" style="--pueblo:${ev.color}"><span class="punto"></span>${this.escapar(ev.pueblo)}</span>
          ${estado}
          <strong class="popup-titulo">${this.escapar(ev.title)}</strong>
          <div class="popup-linea">${this.escapar(fechaCorta(ev.starts_at))}</div>
          <div class="popup-linea">${this.escapar(ubicacionCompleta(ev.location_name, ev.pueblo))}</div>
          <div class="popup-linea">Organiza ${this.escapar(ev.organizador_nombre)} · <b class="popup-precio">${precio}</b></div>
          <button type="button" data-event-id="${ev.id}" class="popup-boton">Ver plan</button>
        </div>`;

      L.marker([ev.lat, ev.lng], { icon, title: `${ev.title} (${this.nombreCategoria(ev.category)})`, alt: ev.title }).bindPopup(popupHtml).addTo(this.markersLayer);
    }
  }

  centrarCercaDeMi() {
    if (!('geolocation' in navigator)) {
      this.avisos.aviso('Tu dispositivo no permite conocer tu ubicación.');
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { latitude, longitude } = pos.coords;
        if (!this.map) return;
        this.userMarker?.remove();
        const userIcon = L.divIcon({
          className: '',
          html: '<div class="user-pin" title="Tu ubicación"></div>',
          iconSize: [18, 18],
          iconAnchor: [9, 9],
        });
        this.userMarker = L.marker([latitude, longitude], { icon: userIcon }).bindPopup('Estás aquí').addTo(this.map);
        this.map.setView([latitude, longitude], 13);
        this.avisos.ok('Mapa centrado en tu ubicación');
      },
      () => this.avisos.aviso('Para centrar el mapa, permite el acceso a tu ubicación.')
    );
  }
}
