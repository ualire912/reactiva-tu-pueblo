import { Component, OnDestroy, OnInit, ViewChild, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterModule } from '@angular/router';
import {
  IonHeader,
  IonToolbar,
  IonTitle,
  IonButtons,
  IonButton,
  IonIcon,
  IonContent,
  IonFooter,
  IonRefresher,
  IonRefresherContent,
} from '@ionic/angular';
import { addIcons } from 'ionicons';
import { addCircle, chevronBack, chevronForward, logInOutline, searchOutline } from 'ionicons/icons';
import { Subscription } from 'rxjs';
import { SupabaseService } from '../../../core/supabase.service';
import { AvisosService } from '../../../core/avisos.service';
import {
  EventoVista,
  EventosService,
  FILTROS_INICIALES,
  FiltroFecha,
  Filtros,
  OPCIONES_FECHA,
  filtrarEventos,
} from '../../../core/eventos.service';
import { FiltrosEventosComponent } from '../../../components/filtros-eventos/filtros-eventos.component';
import { EventoCardComponent } from '../../../components/evento-card/evento-card.component';

@Component({
  selector: 'app-agenda',
  templateUrl: './agenda.page.html',
  standalone: true,
  imports: [
    RouterModule,
    IonHeader,
    IonToolbar,
    IonTitle,
    IonButtons,
    IonButton,
    IonIcon,
    IonContent,
    IonFooter,
    IonRefresher,
    IonRefresherContent,
    FiltrosEventosComponent,
    EventoCardComponent,
  ],
})
export class AgendaPage implements OnInit, OnDestroy {
  sb = inject(SupabaseService);
  private eventosSvc = inject(EventosService);
  private avisos = inject(AvisosService);
  private route = inject(ActivatedRoute);

  @ViewChild(IonContent) contenido?: IonContent;

  readonly tamanos = [6, 12, 24, 48];
  readonly esqueletos = [1, 2, 3, 4, 5, 6];

  eventos = signal<EventoVista[]>([]);
  cargando = signal(true);
  filtros = signal<Filtros>({ ...FILTROS_INICIALES });
  coords = signal<{ lat: number; lng: number } | null>(null);
  porPagina = signal(12);
  paginaActual = signal(1);
  haySesion = signal(false);

  filtrados = computed(() => filtrarEventos(this.eventos(), this.filtros(), this.coords()));

  /** Cuántos planes hay de cada actividad con el resto de filtros aplicados (para los botones de color). */
  conteoCategorias = computed(() => {
    const base = filtrarEventos(this.eventos(), { ...this.filtros(), categoria: null }, this.coords());
    const conteo: Record<string, number> = {};
    for (const ev of base) conteo[ev.category] = (conteo[ev.category] || 0) + 1;
    return conteo;
  });
  totalPaginas = computed(() => Math.max(1, Math.ceil(this.filtrados().length / this.porPagina())));
  paginaVisible = computed(() => Math.min(this.paginaActual(), this.totalPaginas()));
  pagina = computed(() => {
    const p = this.paginaVisible();
    const inicio = (p - 1) * this.porPagina();
    return this.filtrados().slice(inicio, inicio + this.porPagina());
  });
  rango = computed(() => {
    const total = this.filtrados().length;
    if (!total) return '';
    const p = this.paginaVisible();
    const desde = (p - 1) * this.porPagina() + 1;
    const hasta = Math.min(total, p * this.porPagina());
    return `${desde}–${hasta} de ${total}`;
  });

  private realtimeChannel: any;
  private subs: Subscription[] = [];
  private recarga?: ReturnType<typeof setTimeout>;

  constructor() {
    addIcons({ addCircle, chevronBack, chevronForward, logInOutline, searchOutline });
  }

  async ngOnInit() {
    // Filtros que llegan desde la página de inicio (?pueblo=2&categoria=senderismo&fecha=hoy)
    this.subs.push(
      this.route.queryParamMap.subscribe((q) => {
        if (!q.keys.length) return;
        const fecha = q.get('fecha') as FiltroFecha | null;
        this.cambiarFiltros({
          ...FILTROS_INICIALES,
          texto: q.get('q') || '',
          municipio: q.get('pueblo') ? Number(q.get('pueblo')) : null,
          categoria: q.get('categoria') || null,
          fecha: fecha && OPCIONES_FECHA.some((o) => o.valor === fecha) ? fecha : 'proximos',
          accesible: q.get('accesible') === '1',
        });
      }),
      this.sb.session$.subscribe((s) => this.haySesion.set(!!s))
    );

    await this.cargarEventos();

    this.realtimeChannel = this.sb.client
      .channel('eventos-agenda-realtime')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'eventos' }, () => {
        clearTimeout(this.recarga);
        this.recarga = setTimeout(() => this.cargarEventos(), 600);
      })
      .subscribe();
  }

  ngOnDestroy() {
    this.subs.forEach((s) => s.unsubscribe());
    clearTimeout(this.recarga);
    if (this.realtimeChannel) this.sb.client.removeChannel(this.realtimeChannel);
  }

  async ionViewWillEnter() {
    if (this.eventos().length) await this.cargarEventos();
  }

  async cargarEventos(event?: any) {
    if (!this.eventos().length) this.cargando.set(true);
    try {
      this.eventos.set(await this.eventosSvc.cargarVisibles());
    } catch (err) {
      this.avisos.error(err, 'No hemos podido cargar los planes. Inténtalo de nuevo en un momento.');
    } finally {
      this.cargando.set(false);
      event?.target?.complete();
    }
  }

  cambiarFiltros(f: Filtros) {
    this.filtros.set(f);
    this.irAPagina(1);
  }

  cambiarTamano(valor: string) {
    this.porPagina.set(Number(valor) || 12);
    this.irAPagina(1);
  }

  irAPagina(n: number) {
    const destino = Math.min(Math.max(1, n), this.totalPaginas());
    this.paginaActual.set(destino);
    this.contenido?.scrollToTop(250);
  }
}
