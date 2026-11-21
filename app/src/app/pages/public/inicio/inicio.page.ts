import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { Router, RouterModule } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { IonContent, IonHeader, IonToolbar, IonTitle, IonButtons, IonButton, IonIcon } from '@ionic/angular';
import { addIcons } from 'ionicons';
import {
  accessibility,
  addCircle,
  arrowForward,
  calendar,
  logInOutline,
  map,
  paperPlane,
  person,
  shieldCheckmark,
  sunny,
  today,
  calendarNumber,
  time,
  search,
  cameraOutline,
  createOutline,
} from 'ionicons/icons';
import { SupabaseService } from '../../../core/supabase.service';
import { EventoVista, EventosService, FILTROS_INICIALES, filtrarEventos } from '../../../core/eventos.service';
import { EventoCardComponent } from '../../../components/evento-card/evento-card.component';
import { colorPueblo, estiloCategoria, iconoCategoria } from '../../../core/ui';

interface Acceso {
  titulo: string;
  texto: string;
  icono: string;
  ruta: string;
  color: string;
}

@Component({
  selector: 'app-inicio',
  templateUrl: './inicio.page.html',
  standalone: true,
  imports: [RouterModule, IonContent, IonHeader, IonToolbar, IonTitle, IonButtons, IonButton, IonIcon, EventoCardComponent],
})
export class InicioPage implements OnInit {
  sb = inject(SupabaseService);
  private eventosSvc = inject(EventosService);
  private router = inject(Router);

  private perfil = toSignal(this.sb.profile$, { initialValue: null });
  private sesion = toSignal(this.sb.session$, { initialValue: null });

  eventos = signal<EventoVista[]>([]);
  cargando = signal(true);

  proximos = computed(() => filtrarEventos(this.eventos(), FILTROS_INICIALES));
  destacados = computed(() => this.proximos().slice(0, 4));
  planesFinde = computed(() => filtrarEventos(this.eventos(), { ...FILTROS_INICIALES, fecha: 'finde' }).length);
  estiloCategoria = estiloCategoria;
  nombre = computed(() => (this.perfil()?.full_name || '').split(' ')[0]);
  haySesion = computed(() => !!this.sesion());
  esModerador = computed(() => this.perfil()?.rol === 'moderador');

  pueblos = computed(() => {
    this.sb.catalogos();
    const proximos = this.proximos();
    return this.sb.municipios.map((m) => ({
      ...m,
      color: colorPueblo(m.nombre),
      planes: proximos.filter((e) => e.municipio_id === m.id).length,
    }));
  });

  categorias = computed(() => {
    this.sb.catalogos();
    return this.sb.categorias;
  });

  accesos = computed<Acceso[]>(() => {
    const lista: Acceso[] = [
      { titulo: 'Agenda', texto: 'Todos los planes por fecha', icono: 'calendar', ruta: '/agenda', color: '#2b6f68' },
      { titulo: 'Mapa', texto: 'Planes cerca de ti', icono: 'map', ruta: '/mapa', color: '#2a5a86' },
    ];
    if (this.haySesion()) {
      lista.push(
        { titulo: 'Mis planes', texto: 'A los que me he apuntado', icono: 'time', ruta: '/perfil', color: '#4e4386' },
        { titulo: 'Avisos por Telegram', texto: 'Recibe planes en el móvil', icono: 'paper-plane', ruta: '/perfil', color: '#2b6f68' },
        { titulo: 'Mi perfil', texto: 'Mis gustos y mi pueblo', icono: 'person', ruta: '/perfil', color: '#8a3b5c' }
      );
    } else {
      lista.push({ titulo: 'Entrar o registrarme', texto: 'Para apuntarte y recibir avisos', icono: 'log-in-outline', ruta: '/login', color: '#4e4386' });
    }
    if (this.esModerador()) {
      lista.push({ titulo: 'Panel de moderación', texto: 'Revisar y gestionar', icono: 'shield-checkmark', ruta: '/panel', color: '#24312f' });
    }
    return lista;
  });

  iconoCategoria = iconoCategoria;

  readonly atajos: { texto: string; icono: string; params: Record<string, string> }[] = [
    { texto: 'Hoy', icono: 'today', params: { fecha: 'hoy' } },
    { texto: 'Este finde', icono: 'sunny', params: { fecha: 'finde' } },
    { texto: 'Próximos 7 días', icono: 'calendar-number', params: { fecha: 'semana' } },
    { texto: 'Accesibles', icono: 'accessibility', params: { accesible: '1' } },
  ];

  constructor() {
    addIcons({
      accessibility,
      addCircle,
      arrowForward,
      calendar,
      logInOutline,
      map,
      paperPlane,
      person,
      shieldCheckmark,
      sunny,
      today,
      calendarNumber,
      time,
      search,
      cameraOutline,
      createOutline,
    });
  }

  async ngOnInit() {
    await this.cargar();
  }

  async ionViewWillEnter() {
    if (this.eventos().length) await this.cargar();
  }

  async cargar() {
    try {
      this.eventos.set(await this.eventosSvc.cargarVisibles());
    } catch {
      // La página sigue siendo útil sin la lista de planes
    } finally {
      this.cargando.set(false);
    }
  }

  /** Abre la agenda con lo escrito en el buscador de la portada. */
  buscar(evento: Event, texto: string) {
    evento.preventDefault();
    const q = texto.trim();
    this.irAgenda(q ? { q } : { fecha: 'proximos' });
  }

  /** Abre la agenda con un filtro ya aplicado. */
  irAgenda(params: Record<string, string | number>) {
    this.router.navigate(['/agenda'], { queryParams: { ...params, v: Date.now() } });
  }
}
