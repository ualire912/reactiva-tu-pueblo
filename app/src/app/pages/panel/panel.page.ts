import { Component, OnInit, inject, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { AlertController } from '@ionic/angular';
import { addIcons } from 'ionicons';
import {
  checkmarkCircleOutline,
  closeCircleOutline,
  createOutline,
  refreshOutline,
  sparklesOutline,
  warningOutline,
  peopleOutline,
  statsChartOutline,
  playCircleOutline,
  timeOutline,
  shieldCheckmarkOutline,
  documentTextOutline,
  menuOutline,
  search,
  close,
  chevronBack,
  chevronForward,
  calendarOutline,
  locationOutline,
  cashOutline,
  alertCircleOutline,
  carOutline,
  megaphoneOutline,
  notificationsOutline,
  starOutline,
  trendingUpOutline,
  trendingDownOutline,
  chevronDownOutline,
  personAddOutline,
  personRemoveOutline
} from 'ionicons/icons';
import { SupabaseService } from '../../core/supabase.service';
import { ApiService } from '../../core/api.service';
import { AvisosService } from '../../core/avisos.service';
import { GeocodingService } from '../../core/geocoding.service';
import {
  colorEstado,
  colorPueblo,
  etiquetaEstado,
  etiquetaIngesta,
  etiquetaOrigen,
  fechaCorta,
  isoALocalInput,
  estiloCategoria,
  listaCampos,
  normalizar,
  textoPrecio,
  textoValoracion,
} from '../../core/ui';
import {
  Evento,
  Ingesta,
  Incidencia,
  Entidad
} from '../../core/models';
import {
  IonHeader,
  IonToolbar,
  IonTitle,
  IonContent,
  IonButtons,
  IonButton,
  IonIcon,
  IonLabel,
  IonList,
  IonItem,
  IonToggle,
  IonGrid,
  IonRow,
  IonCol,
  IonCard,
  IonCardHeader,
  IonCardTitle,
  IonCardSubtitle,
  IonCardContent,
  IonNote,
  IonSelect,
  IonSelectOption,
  IonBadge,
  IonChip,
  IonInput,
  IonTextarea,
  IonSpinner,
  IonModal
} from '@ionic/angular';

@Component({
  selector: 'app-panel',
  templateUrl: './panel.page.html',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterModule,
    IonHeader,
    IonToolbar,
    IonTitle,
    IonContent,
    IonButtons,
    IonButton,
    IonIcon,
    IonLabel,
    IonList,
    IonItem,
    IonToggle,
    IonGrid,
    IonRow,
    IonCol,
    IonCard,
    IonCardHeader,
    IonCardTitle,
    IonCardSubtitle,
    IonCardContent,
    IonNote,
    IonSelect,
    IonSelectOption,
    IonBadge,
    IonChip,
    IonInput,
    IonTextarea,
    IonSpinner,
    IonModal
  ]
})
export class PanelPage implements OnInit {
  public sb = inject(SupabaseService);
  private api = inject(ApiService);
  private avisos = inject(AvisosService);
  private geo = inject(GeocodingService);
  private alertCtrl = inject(AlertController);
  private cdr = inject(ChangeDetectorRef);

  pestana: 'pendientes' | 'ia' | 'incidencias' | 'publicadores' | 'estadisticas' | 'demo' = 'pendientes';

  readonly secciones: { id: PanelPage['pestana']; texto: string; icono: string; color: string; fondo: string }[] = [
    { id: 'pendientes', texto: 'Pendientes', icono: 'time-outline', color: '#6b4a00', fondo: '#fdf0d2' },
    { id: 'ia', texto: 'Detectados por IA', icono: 'sparkles-outline', color: '#4e4386', fondo: '#e9e4f6' },
    { id: 'incidencias', texto: 'Incidencias', icono: 'warning-outline', color: '#94461a', fondo: '#fce5d3' },
    { id: 'publicadores', texto: 'Publicadores', icono: 'people-outline', color: '#2a5a86', fondo: '#dceaf6' },
    { id: 'estadisticas', texto: 'Estadísticas', icono: 'stats-chart-outline', color: '#1b4844', fondo: '#ddeeea' },
    { id: 'demo', texto: 'Demo', icono: 'play-circle-outline', color: '#4a4540', fondo: '#f4efe6' },
  ];

  // Helpers para la plantilla
  etiquetaEstado = etiquetaEstado;
  colorEstado = colorEstado;
  etiquetaIngesta = etiquetaIngesta;
  etiquetaOrigen = etiquetaOrigen;
  listaCampos = listaCampos;
  fechaCorta = fechaCorta;
  colorPueblo = colorPueblo;
  textoPrecio = textoPrecio;

  contador(id: PanelPage['pestana']): number | null {
    if (id === 'pendientes') return this.pendientes.length;
    if (id === 'ia') return this.ingestas.filter((i) => !['confirmada', 'descartada'].includes(i.estado)).length;
    if (id === 'incidencias') return this.incidencias.filter((i) => i.estado === 'abierta').length;
    if (id === 'publicadores') return this.candidatos.length;
    return null;
  }

  irA(id: PanelPage['pestana']) {
    this.pestana = id;
    this.busqueda = '';
    this.cdr.detectChanges();
  }

  estiloCategoria = estiloCategoria;

  nombreCategoria(slug: string | null | undefined): string {
    return this.sb.categorias.find((c) => c.slug === slug)?.nombre || 'Actividad';
  }

  /** Saludo según la hora (en España). */
  get saludo(): string {
    const h = Number(new Intl.DateTimeFormat('es-ES', { hour: 'numeric', hour12: false, timeZone: 'Europe/Madrid' }).format(new Date()));
    if (h >= 6 && h < 14) return 'Buenos días';
    if (h >= 14 && h < 21) return 'Buenas tardes';
    return 'Buenas noches';
  }

  // ---------- Buscador del panel (filtra la sección abierta) ----------
  busqueda = '';

  readonly placeholderBusqueda: Record<PanelPage['pestana'], string> = {
    pendientes: 'Buscar por título, pueblo u origen',
    ia: 'Buscar en las publicaciones detectadas',
    incidencias: 'Buscar por plan o comentario',
    publicadores: 'Buscar por nombre, correo o asociación',
    estadisticas: 'Buscar un plan o un pueblo',
    demo: '',
  };

  cambiarBusqueda(valor: string) {
    this.busqueda = valor;
    this.paginaPublicados = 1;
    this.cdr.detectChanges();
  }

  /** Devuelve solo los elementos cuyo texto contiene todas las palabras buscadas (sin tildes). */
  filtrar<T>(lista: T[]): T[] {
    const palabras = normalizar(this.busqueda).split(/\s+/).filter(Boolean);
    if (!palabras.length) return lista;
    return lista.filter((x) => {
      const texto = normalizar(textoBuscable(x));
      return palabras.every((p) => texto.includes(p));
    });
  }

  /** Listas de la sección abierta, para el contador "Mostrando X de Y". */
  private listasSeccion(): unknown[][] {
    switch (this.pestana) {
      case 'pendientes':
        return [this.pendientes, this.eventosActivos];
      case 'ia':
        return [this.ingestas];
      case 'incidencias':
        return [this.incidencias];
      case 'publicadores':
        return [this.candidatos, this.publicadoresActivos, this.entidades];
      case 'estadisticas':
        return [this.statsEventos];
      default:
        return [];
    }
  }

  get resumenBusqueda(): { visibles: number; total: number } {
    const listas = this.listasSeccion();
    return {
      visibles: listas.reduce((n, l) => n + this.filtrar(l).length, 0),
      total: listas.reduce((n, l) => n + l.length, 0),
    };
  }
  cargando = false;

  // 1. Pendientes y eventos activos
  pendientes: Evento[] = [];
  eventosActivos: Evento[] = [];

  // Planes publicados, de 5 en 5: primero los próximos y luego los ya celebrados (los más recientes antes)
  readonly porPaginaPublicados = 5;
  paginaPublicados = 1;

  private get publicadosOrdenados(): Evento[] {
    const lista = this.filtrar(this.eventosActivos);
    const activos = lista.filter((e) => e.status !== 'finalizado').sort((a, b) => a.starts_at.localeCompare(b.starts_at));
    const pasados = lista.filter((e) => e.status === 'finalizado').sort((a, b) => b.starts_at.localeCompare(a.starts_at));
    return [...activos, ...pasados];
  }
  get totalPaginasPublicados(): number {
    return Math.max(1, Math.ceil(this.publicadosOrdenados.length / this.porPaginaPublicados));
  }
  get paginaPublicadosVisible(): number {
    return Math.min(this.paginaPublicados, this.totalPaginasPublicados);
  }
  get paginaPublicadosLista(): Evento[] {
    const inicio = (this.paginaPublicadosVisible - 1) * this.porPaginaPublicados;
    return this.publicadosOrdenados.slice(inicio, inicio + this.porPaginaPublicados);
  }
  get rangoPublicados(): string {
    const total = this.publicadosOrdenados.length;
    const desde = (this.paginaPublicadosVisible - 1) * this.porPaginaPublicados + 1;
    return `${desde}–${Math.min(total, desde + this.porPaginaPublicados - 1)} de ${total}`;
  }
  irAPaginaPublicados(n: number) {
    this.paginaPublicados = Math.min(Math.max(1, n), this.totalPaginasPublicados);
    this.cdr.detectChanges();
  }

  // 2. Detectados por IA (ingestas)
  ingestas: Ingesta[] = [];

  // 3. Incidencias
  incidencias: Incidencia[] = [];

  // 4. Publicadores, entidades y candidatos IA
  entidades: Entidad[] = [];
  publicadoresActivos: any[] = [];
  candidatos: any[] = [];
  revisandoIa = false;

  // 5. Estadísticas
  statsEventos: any[] = [];
  solapamientos: any[] = [];
  resumenCifras = {
    abiertos: 0,
    inscripciones: 0,
    porcentajeAsistencia: 0,
    avisosEnviados: 0
  };
  consumoIa: { llamadas: number; tokens: number } | null = null;

  // 6. Demo
  demoPageUrl = 'https://facebook.com/asociacion-lucainena';
  demoText = '';
  demoImageUrl = '';
  demoEventIdSeleccionado = '';
  demoResultado = '';
  ejecutandoDemo = false;

  constructor() {
    addIcons({
      search,
      close,
      chevronBack,
      chevronForward,
      calendarOutline,
      locationOutline,
      cashOutline,
      alertCircleOutline,
      carOutline,
      megaphoneOutline,
      notificationsOutline,
      starOutline,
      trendingUpOutline,
      trendingDownOutline,
      chevronDownOutline,
      personAddOutline,
      personRemoveOutline,
      checkmarkCircleOutline,
      closeCircleOutline,
      createOutline,
      refreshOutline,
      sparklesOutline,
      warningOutline,
      peopleOutline,
      statsChartOutline,
      playCircleOutline,
      timeOutline,
      shieldCheckmarkOutline,
      documentTextOutline,
      menuOutline
    });
  }

  async ngOnInit() {
    await this.cargarTodo();
  }

  async ionViewWillEnter() {
    await this.cargarTodo();
  }

  async cargarTodo() {
    this.cargando = this.pendientes.length === 0 && this.eventosActivos.length === 0 && this.ingestas.length === 0;
    this.cdr.detectChanges();

    try {
      await Promise.all([
        // Las estadísticas usan los planes publicados (categoría, fecha): van después
        this.cargarPendientesYActivos().then(() => this.cargarEstadisticas()),
        this.cargarIngestas(),
        this.cargarIncidencias(),
        this.cargarPublicadores()
      ]);
    } catch {
      // Mantiene vista estable
    } finally {
      this.cargando = false;
      this.cdr.detectChanges();
    }
  }

  // --- PESTAÑA 1: PENDIENTES Y GESTIÓN DE EVENTOS ---
  async cargarPendientesYActivos() {
    const [pendRes, actRes] = await Promise.all([
      this.sb.client
        .from('eventos')
        .select('*, municipios(nombre)')
        .in('status', ['pendiente_moderacion', 'borrador_ia'])
        .order('created_at', { ascending: false }),
      this.sb.client
        .from('eventos')
        .select('*, municipios(nombre)')
        .in('status', ['abierto', 'completo', 'inscripcion_cerrada', 'en_curso', 'pospuesto', 'finalizado'])
        .order('starts_at', { ascending: true })
    ]);

    if (pendRes.data) this.pendientes = pendRes.data as Evento[];
    if (actRes.data) {
      this.eventosActivos = actRes.data as Evento[];
      if (!this.demoEventIdSeleccionado && this.eventosActivos.length > 0) {
        this.demoEventIdSeleccionado = this.eventosActivos[0].id;
      }
    }
    this.cdr.detectChanges();
  }

  async aprobarEvento(ev: Evento) {
    const { error } = await this.sb.client
      .from('eventos')
      .update({ status: 'abierto' })
      .eq('id', ev.id);

    if (error) {
      this.avisos.error(error, 'No se ha podido aprobar el plan.');
      return;
    }

    try {
      await this.api.avisarEvento(ev.id, 'aprobado');
      this.avisos.ok('Plan aprobado y publicado. Hemos avisado al organizador.');
    } catch {
      this.avisos.ok('Plan aprobado y publicado.');
    }

    await this.cargarPendientesYActivos();
  }

  async rechazarEvento(ev: Evento) {
    const alert = await this.alertCtrl.create({
      header: 'Rechazar propuesta',
      message: `Indica el motivo para "${ev.title}":`,
      inputs: [{ name: 'motivo', type: 'text', placeholder: 'Ej. Datos incompletos o duplicado' }],
      buttons: [
        { text: 'Cancelar', role: 'cancel' },
        {
          text: 'Rechazar',
          handler: async (vals) => {
            const note = vals.motivo?.trim() || 'No cumple los criterios de publicación';
            const { error } = await this.sb.client
              .from('eventos')
              .update({ status: 'rechazado', moderation_note: note })
              .eq('id', ev.id);

            if (error) {
              this.avisos.error(error, 'No se ha podido rechazar el plan.');
              return;
            }
            try {
              await this.api.avisarEvento(ev.id, 'rechazado');
            } catch {}
            this.avisos.info('Plan rechazado. Se ha avisado al organizador.');
            await this.cargarPendientesYActivos();
          }
        }
      ]
    });
    await alert.present();
  }

  // ---------- Editar un plan (formulario completo en una ventana) ----------
  edicion: EdicionPlan | null = null;
  erroresEdicion: Partial<Record<keyof EdicionPlan, string>> = {};
  guardandoEdicion = false;
  private planEditando: Evento | null = null;
  private edicionEsPublicado = false;

  editarEventoModal(ev: Evento, esYaPublicado = false) {
    const local = isoALocalInput(ev.starts_at);
    const [fecha, hora] = local.split('T');
    this.planEditando = ev;
    this.edicionEsPublicado = esYaPublicado;
    this.erroresEdicion = {};
    this.edicion = {
      title: ev.title || '',
      category: ev.category || 'otro',
      municipio_id: ev.municipio_id ?? null,
      fecha: fecha || '',
      hora: (hora || '').slice(0, 5),
      duration_minutes: ev.duration_minutes ?? null,
      location_name: ev.location_name || '',
      price: ev.price ?? 0,
      max_capacity: ev.max_capacity ?? null,
      min_age: ev.min_age ?? null,
      max_age: (ev as Evento & { max_age?: number | null }).max_age ?? null,
      is_accessible: !!ev.is_accessible,
      description: ev.description || '',
    };
    this.cdr.detectChanges();
  }

  get tituloEdicion(): string {
    return this.edicionEsPublicado ? 'Modificar un plan publicado' : 'Revisar y editar antes de aprobar';
  }

  get edicionEsDePendiente(): boolean {
    return !this.edicionEsPublicado;
  }

  cerrarEdicion() {
    this.edicion = null;
    this.planEditando = null;
    this.cdr.detectChanges();
  }

  private validarEdicion(e: EdicionPlan): Partial<Record<keyof EdicionPlan, string>> {
    const r: Partial<Record<keyof EdicionPlan, string>> = {};
    const num = (v: unknown) => (v === null || v === undefined || v === '' ? null : Number(v));
    if (e.title.trim().length < 3) r.title = 'Escribe un título de al menos 3 letras.';
    if (!e.municipio_id) r.municipio_id = 'Elige el pueblo.';
    if (!e.fecha) r.fecha = 'Elige el día.';
    if (!e.hora) r.hora = 'Elige la hora.';
    const precio = num(e.price);
    if (precio !== null && (isNaN(precio) || precio < 0)) r.price = 'No puede ser negativo. Pon 0 si es gratis.';
    const plazas = num(e.max_capacity);
    if (plazas !== null && (isNaN(plazas) || plazas < 1)) r.max_capacity = 'Pon un número desde 1, o déjalo vacío si no hay límite.';
    const dur = num(e.duration_minutes);
    if (dur !== null && (isNaN(dur) || dur < 0)) r.duration_minutes = 'No puede ser negativa.';
    const emin = num(e.min_age);
    const emax = num(e.max_age);
    if (emin !== null && emin < 0) r.min_age = 'No puede ser negativa.';
    if (emax !== null && emin !== null && emax < emin) r.max_age = 'Es menor que la edad mínima.';
    return r;
  }

  /** Guarda los cambios; si `yAprobar`, además publica el plan. */
  async guardarEdicion(yAprobar = false) {
    const e = this.edicion;
    const ev = this.planEditando;
    if (!e || !ev) return;
    this.erroresEdicion = this.validarEdicion(e);
    if (Object.keys(this.erroresEdicion).length) {
      this.cdr.detectChanges();
      return;
    }
    const num = (v: unknown) => (v === null || v === undefined || v === '' ? null : Number(v));
    const nuevaFechaIso = new Date(e.fecha + 'T' + e.hora).toISOString();
    const nuevoLugar = e.location_name.trim() || 'Por confirmar';
    const cambioHora = new Date(nuevaFechaIso).getTime() !== new Date(ev.starts_at).getTime();
    const cambioLugar = nuevoLugar !== ev.location_name || Number(e.municipio_id) !== ev.municipio_id;

    const cambios: Record<string, unknown> = {
      title: e.title.trim(),
      category: e.category,
      municipio_id: Number(e.municipio_id),
      starts_at: nuevaFechaIso,
      duration_minutes: num(e.duration_minutes),
      location_name: nuevoLugar,
      price: num(e.price) ?? 0,
      max_capacity: num(e.max_capacity),
      min_age: num(e.min_age),
      max_age: num(e.max_age),
      is_accessible: e.is_accessible,
      description: e.description.trim() || null,
    };

    this.guardandoEdicion = true;
    this.cdr.detectChanges();
    try {
      if (cambioLugar) {
        const pueblo = this.sb.municipios.find((m) => m.id === Number(e.municipio_id))?.nombre;
        const geo = await this.geo.buscar(nuevoLugar, pueblo);
        if (geo) {
          cambios['lat'] = geo.lat;
          cambios['lng'] = geo.lng;
        }
      }
      const { error } = await this.sb.client.from('eventos').update(cambios).eq('id', ev.id);
      if (error) {
        this.avisos.error(error, 'No se han podido guardar los cambios.');
        return;
      }
      if (this.edicionEsPublicado && (cambioHora || cambioLugar)) {
        try {
          const res = await this.api.avisarEvento(ev.id, 'cambio_hora');
          this.avisos.ok(`Cambios guardados. Hemos avisado a ${res?.enviados ?? 0} personas apuntadas y su calendario se actualizará solo.`);
        } catch {
          this.avisos.aviso('Cambios guardados, pero no hemos podido avisar a los apuntados. Inténtalo más tarde.');
        }
      } else if (!yAprobar) {
        this.avisos.ok('Cambios guardados.');
      }
      const actualizado = { ...ev, ...cambios } as Evento;
      this.cerrarEdicion();
      if (yAprobar) await this.aprobarEvento(actualizado);
      await this.cargarPendientesYActivos();
    } finally {
      this.guardandoEdicion = false;
      this.cdr.detectChanges();
    }
  }

  async cambiarEstadoManual(ev: Evento, nuevoEstado: 'abierto' | 'pospuesto' | 'cancelado') {
    const { error } = await this.sb.client
      .from('eventos')
      .update({ status: nuevoEstado })
      .eq('id', ev.id);

    if (error) {
      this.avisos.error(error, 'No se ha podido cambiar el estado del plan.');
      return;
    }

    const texto = nuevoEstado === 'cancelado' ? 'Plan cancelado' : nuevoEstado === 'pospuesto' ? 'Plan aplazado' : 'Plan reabierto';
    if (nuevoEstado === 'pospuesto' || nuevoEstado === 'cancelado') {
      try {
        const res = await this.api.avisarEvento(ev.id, 'cancelacion');
        this.avisos.ok(`${texto}. Hemos avisado a ${res?.enviados ?? 0} personas apuntadas.`);
      } catch {
        this.avisos.aviso(`${texto}, pero no hemos podido avisar a los apuntados. Inténtalo más tarde.`);
      }
    } else {
      this.avisos.ok(`${texto}.`);
    }

    await this.cargarPendientesYActivos();
  }

  // --- PESTAÑA 2: DETECTADOS POR IA (INGESTAS) ---
  async cargarIngestas() {
    // Solo posibles EVENTOS pendientes de revisar: el spam, lo descartado (no es un evento,
    // fuera de la Mancomunidad...) y lo ya convertido en evento no le llegan al moderador
    const { data } = await this.sb.client
      .from('ingestas')
      .select('*')
      .in('estado', ['extraida', 'esperando_confirmacion'])
      .is('event_id', null)
      .order('created_at', { ascending: false })
      .limit(30);

    if (data) {
      this.ingestas = data as Ingesta[];
      this.cdr.detectChanges();
    }
  }

  colorEstadoIngesta(estado: string): string {
    if (estado === 'spam' || estado === 'error') return 'danger';
    if (estado === 'esperando_confirmacion') return 'warning';
    if (estado === 'confirmada') return 'success';
    return 'medium';
  }

  async rescatarIngesta(ing: Ingesta) {
    const ext = ing.extracted;
    if (!ext) {
      this.avisos.aviso('No hay datos suficientes en esta publicación para crear un plan.');
      return;
    }

    const mun = this.sb.municipios.find(
      (m) => ext.municipio && m.nombre.toLowerCase().includes(ext.municipio.toLowerCase())
    );

    const { data: nuevoEv, error } = await this.sb.client
      .from('eventos')
      .insert({
        title: ext.title || 'Evento detectado',
        category: ext.category || 'otro',
        municipio_id: mun?.id || this.sb.municipios[0]?.id || 1,
        starts_at: ext.starts_at || new Date(Date.now() + 86400000).toISOString(),
        inscripcion_hasta: ext.inscripcion_hasta || null,
        duration_minutes: ext.duration_minutes || 120,
        location_name: ext.location_name || 'Por confirmar',
        price: ext.price ?? 0,
        max_capacity: ext.max_capacity ?? null,
        description: ext.description || ing.raw_text || null,
        is_accessible: ext.is_accessible ?? false,
        poster_url: ing.image_url || null,
        origen: (ing.origen as any) || 'facebook',
        source_url: ing.source_url || null,
        ai_confidence: ext.confidence ?? null
      })
      .select()
      .single();

    if (error) {
      this.avisos.error(error, 'No se ha podido crear el plan.');
      return;
    }

    await this.sb.client
      .from('ingestas')
      .update({ estado: 'confirmada', event_id: nuevoEv.id })
      .eq('id', ing.id);

    this.avisos.ok('Plan creado a partir de la publicación. Revísalo en Pendientes.');
    await Promise.all([this.cargarIngestas(), this.cargarPendientesYActivos()]);
  }

  async descartarIngesta(ing: Ingesta) {
    await this.sb.client
      .from('ingestas')
      .update({ estado: 'descartada' })
      .eq('id', ing.id);

    this.avisos.info('Publicación descartada.');
    await this.cargarIngestas();
  }

  // --- PESTAÑA 3: INCIDENCIAS ---
  async cargarIncidencias() {
    const { data } = await this.sb.client
      .from('incidencias')
      .select('*, eventos(title)')
      .order('estado', { ascending: true });

    if (data) {
      this.incidencias = data as Incidencia[];
      this.cdr.detectChanges();
    }
  }

  async resolverIncidencia(inc: Incidencia) {
    const { error } = await this.sb.client
      .from('incidencias')
      .update({ estado: 'resuelta' })
      .eq('id', inc.id);

    if (!error) {
      this.avisos.ok('Incidencia resuelta.');
      await this.cargarIncidencias();
    }
  }

  // --- PESTAÑA 4: PUBLICADORES Y CANDIDATOS IA ---
  async cargarPublicadores() {
    const [entRes, pubRes, candRes] = await Promise.all([
      this.sb.client.from('entidades').select('*').order('nombre'),
      this.sb.client
        .from('profiles')
        .select('id, full_name, email, reputation_score, promocion_motivo')
        .eq('rol', 'publicador')
        .order('full_name'),
      this.sb.client
        .from('v_candidatos_publicador')
        .select('id, full_name, reputation_score, telegram_linked, promocion_motivo, promocion_revisada_at')
    ]);

    if (entRes.data) this.entidades = entRes.data as Entidad[];
    if (pubRes.data) this.publicadoresActivos = pubRes.data;
    if (candRes.data) this.candidatos = candRes.data;
    this.cdr.detectChanges();
  }

  async revisarCandidatosConIa() {
    this.revisandoIa = true;
    this.cdr.detectChanges();
    try {
      const res = await this.api.revisarPromociones();
      const p = res.promovidos?.length || 0;
      const r = res.a_revisar?.length || 0;
      this.avisos.ok(
        p || r
          ? `Revisión hecha: ${p} ${p === 1 ? 'persona nueva es Publicadora' : 'personas nuevas son Publicadoras'} y ${r} quedan para que decidas tú.`
          : 'Revisión hecha: no hay candidaturas nuevas.'
      );
      await this.cargarPublicadores();
    } catch {
      this.avisos.aviso('El servidor no responde ahora mismo (puede tardar un minuto en despertar). Inténtalo de nuevo.');
    } finally {
      this.revisandoIa = false;
      this.cdr.detectChanges();
    }
  }

  // ---------- IA: resumen breve ----------
  esPublicidad(ing: Ingesta): boolean {
    return ing.estado === 'spam' || (ing.spam_score ?? 0) >= 0.5;
  }

  lugarIngesta(ing: Ingesta): string {
    const ex = ing.extracted;
    const partes = [ex?.location_name, ex?.municipio].filter((x) => !!x && String(x).trim());
    return partes.length ? partes.join(', ') : 'Sin lugar';
  }

  // ---------- Buscar a cualquier vecino ----------
  busquedaVecinos = '';
  buscandoVecinos = false;
  resultadosVecinos: VecinoFila[] = [];
  private temporizadorVecinos?: ReturnType<typeof setTimeout>;

  cambiarBusquedaVecinos(valor: string) {
    this.busquedaVecinos = valor;
    clearTimeout(this.temporizadorVecinos);
    if (valor.trim().length < 2) {
      this.resultadosVecinos = [];
      this.cdr.detectChanges();
      return;
    }
    this.temporizadorVecinos = setTimeout(() => this.buscarVecinos(), 300);
    this.cdr.detectChanges();
  }

  async buscarVecinos() {
    const q = this.busquedaVecinos.trim().replace(/[%,()]/g, ' ');
    if (q.length < 2) return;
    this.buscandoVecinos = true;
    this.cdr.detectChanges();
    try {
      const { data, error } = await this.sb.client
        .from('profiles')
        .select('id, full_name, email, rol, reputation_score, municipio_id')
        .or(`full_name.ilike.%${q}%,email.ilike.%${q}%`)
        .order('full_name')
        .limit(20);
      if (error) throw error;
      this.resultadosVecinos = (data || []) as typeof this.resultadosVecinos;
    } catch (err) {
      this.avisos.error(err, 'No hemos podido buscar vecinos ahora mismo.');
    } finally {
      this.buscandoVecinos = false;
      this.cdr.detectChanges();
    }
  }

  nombreRol(rol: string): string {
    return rol === 'publicador' ? 'Publicador/a' : rol === 'moderador' ? 'Moderador/a' : 'Vecino/a';
  }

  /** En la base de datos el rol de un vecino normal es 'usuario' (o 'visitante'). */
  puedeSerPublicador(rol: string): boolean {
    return rol !== 'publicador' && rol !== 'moderador';
  }

  /** Pide confirmación antes de dar o quitar el permiso de Publicador/a. */
  async cambiarRolVecino(v: VecinoFila, accion: 'promover' | 'revocar') {
    const nombre = v.full_name || v.email || 'esta persona';
    const alerta = await this.alertCtrl.create({
      header: accion === 'promover' ? '¿Hacer Publicador/a?' : '¿Quitar Publicador/a?',
      message:
        accion === 'promover'
          ? `Los planes que proponga ${nombre} se publicarán directamente, sin pasar por revisión.`
          : `Los próximos planes de ${nombre} volverán a pasar por revisión.`,
      buttons: [
        { text: 'Cancelar', role: 'cancel' },
        { text: accion === 'promover' ? 'Sí, hacer Publicador/a' : 'Sí, quitar', role: 'confirm' },
      ],
    });
    await alerta.present();
    const { role } = await alerta.onDidDismiss();
    if (role !== 'confirm') return;

    this.cambiandoRol = v.id;
    this.cdr.detectChanges();
    try {
      await this.decidirCandidato(v.id, accion);
      const nuevoRol = accion === 'promover' ? 'publicador' : 'usuario';
      for (const lista of [this.resultadosVecinos, this.directorio]) {
        const fila = lista.find((x) => x.id === v.id);
        if (fila) fila.rol = nuevoRol;
      }
    } finally {
      this.cambiandoRol = null;
      this.cdr.detectChanges();
    }
  }

  // ---------- Directorio desplegable de vecinos ----------
  directorio: VecinoFila[] = [];
  directorioCargado = false;
  cargandoDirectorio = false;
  filtroRol: 'todos' | 'vecinos' | 'publicadores' = 'todos';
  cambiandoRol: string | null = null;

  async alAbrirDirectorio(abierto: boolean) {
    if (abierto && !this.directorioCargado) await this.cargarDirectorio();
  }

  async cargarDirectorio() {
    this.cargandoDirectorio = true;
    this.cdr.detectChanges();
    try {
      const { data, error } = await this.sb.client
        .from('profiles')
        .select('id, full_name, email, rol, reputation_score, municipio_id')
        .order('full_name')
        .limit(500);
      if (error) throw error;
      this.directorio = (data || []) as VecinoFila[];
      this.directorioCargado = true;
    } catch (err) {
      this.avisos.error(err, 'No hemos podido cargar la lista de vecinos.');
    } finally {
      this.cargandoDirectorio = false;
      this.cdr.detectChanges();
    }
  }

  // Filtro por pueblo: '' = todos, 'sin' = sin pueblo indicado, o el id del pueblo
  filtroPueblo = '';
  paginaDirectorio = 1;
  readonly porPaginaDirectorio = 8;

  private porPueblo(lista: VecinoFila[]): VecinoFila[] {
    if (!this.filtroPueblo) return lista;
    if (this.filtroPueblo === 'sin') return lista.filter((v) => !v.municipio_id);
    return lista.filter((v) => String(v.municipio_id) === this.filtroPueblo);
  }

  get directorioFiltrado(): VecinoFila[] {
    const base = this.porPueblo(this.directorio);
    if (this.filtroRol === 'vecinos') return base.filter((v) => this.puedeSerPublicador(v.rol));
    if (this.filtroRol === 'publicadores') return base.filter((v) => v.rol === 'publicador');
    return base;
  }

  /** Solo los vecinos de la página actual. */
  get directorioPagina(): VecinoFila[] {
    const pagina = Math.min(this.paginaDirectorio, this.totalPaginasDirectorio);
    const desde = (pagina - 1) * this.porPaginaDirectorio;
    return this.directorioFiltrado.slice(desde, desde + this.porPaginaDirectorio);
  }

  get totalPaginasDirectorio(): number {
    return Math.max(1, Math.ceil(this.directorioFiltrado.length / this.porPaginaDirectorio));
  }

  get rangoDirectorio(): string {
    const total = this.directorioFiltrado.length;
    if (!total) return '';
    const pagina = Math.min(this.paginaDirectorio, this.totalPaginasDirectorio);
    const desde = (pagina - 1) * this.porPaginaDirectorio + 1;
    const hasta = Math.min(total, pagina * this.porPaginaDirectorio);
    return desde + '–' + hasta + ' de ' + total;
  }

  irAPaginaDirectorio(n: number) {
    this.paginaDirectorio = Math.min(Math.max(1, n), this.totalPaginasDirectorio);
    this.cdr.detectChanges();
  }

  cambiarFiltroRol(f: 'todos' | 'vecinos' | 'publicadores') {
    this.filtroRol = f;
    this.paginaDirectorio = 1;
    this.cdr.detectChanges();
  }

  cambiarFiltroPueblo(valor: string) {
    this.filtroPueblo = valor;
    this.paginaDirectorio = 1;
    this.cdr.detectChanges();
  }

  nombrePuebloDe(id: number | null | undefined): string {
    return this.sb.municipios.find((m) => m.id === id)?.nombre || '';
  }

  contarRol(filtro: 'todos' | 'vecinos' | 'publicadores'): number {
    const base = this.porPueblo(this.directorio);
    if (filtro === 'vecinos') return base.filter((v) => this.puedeSerPublicador(v.rol)).length;
    if (filtro === 'publicadores') return base.filter((v) => v.rol === 'publicador').length;
    return base.length;
  }

  async decidirCandidato(userId: string, accion: 'promover' | 'descartar' | 'revocar') {
    try {
      await this.api.decidirPromocion(userId, accion);
      const textos = { promover: 'Ahora es Publicador/a. Le hemos avisado.', descartar: 'Candidatura descartada.', revocar: 'Ya no es Publicador/a. Le hemos avisado.' };
      this.avisos.ok(textos[accion]);
      await this.cargarPublicadores();
    } catch {
      // Plan B directo a BD si el servidor no responde
      if (accion === 'promover') {
        await this.sb.client
          .from('profiles')
          .update({ rol: 'publicador', promocion_pendiente: false })
          .eq('id', userId);
      } else {
        await this.sb.client
          .from('profiles')
          .update({ rol: 'usuario', promocion_pendiente: false })
          .eq('id', userId);
      }
      this.avisos.aviso('Cambio guardado, pero no hemos podido avisar a la persona por Telegram.');
      await this.cargarPublicadores();
    }
  }

  async toggleAutoApproveEntidad(ent: Entidad, valor: boolean) {
    const { error } = await this.sb.client
      .from('entidades')
      .update({ auto_approve: valor })
      .eq('id', ent.id);

    if (!error) {
      ent.auto_approve = valor;
      this.avisos.ok(valor ? 'Sus planes se publicarán sin revisión.' : 'Sus planes volverán a pasar por revisión.');
      this.cdr.detectChanges();
    }
  }

  // --- PESTAÑA 5: ESTADÍSTICAS ---
  async cargarEstadisticas() {
    const hace30 = new Date(Date.now() - 29 * 86400000);
    hace30.setHours(0, 0, 0, 0);
    const [statsRes, solapRes, notifRes, inscRes] = await Promise.all([
      this.sb.client.from('v_eventos_stats').select('*'),
      this.sb.client.from('v_solapamientos').select('*'),
      this.sb.client.from('notificaciones').select('id', { count: 'exact', head: true }),
      this.sb.client
        .from('inscripciones')
        .select('created_at, status')
        .gte('created_at', hace30.toISOString())
        .limit(5000)
    ]);
    this.inscripcionesRecientes = (inscRes.data || []) as { created_at: string; status: string }[];

    if (statsRes.data) {
      this.statsEventos = statsRes.data;
      const totalInscritos = this.statsEventos.reduce((acc, s) => acc + (s.confirmed_count || 0), 0);
      const totalAsistieron = this.statsEventos.reduce((acc, s) => acc + (s.asistieron || 0), 0);
      this.resumenCifras.inscripciones = totalInscritos;
      this.resumenCifras.porcentajeAsistencia =
        totalInscritos > 0 ? Math.round((totalAsistieron / totalInscritos) * 100) : 0;
    }

    this.resumenCifras.abiertos = this.eventosActivos.length;
    this.resumenCifras.avisosEnviados = notifRes.count || 0;

    if (solapRes.data) this.solapamientos = solapRes.data;

    this.calcularGraficos();
    this.cdr.detectChanges();

    try {
      this.consumoIa = await this.api.getIaConsumo();
    } catch {
      this.consumoIa = null;
    }
    this.cdr.detectChanges();
  }

  // ---------- Gráficas de estadísticas ----------
  inscripcionesRecientes: { created_at: string; status: string }[] = [];
  graficos: Graficos = GRAFICOS_VACIOS;
  verDetallePlanes = false;

  /** Convierte las cifras de cada plan en datos listos para pintar gráficas y consejos. */
  calcularGraficos() {
    const stats = this.statsEventos;
    const eventoPorId = new Map(this.eventosActivos.map((e) => [e.id, e]));
    const n = (v: unknown) => Number(v) || 0;

    // Gente apuntada por pueblo (y nº de planes)
    const pueblos = new Map<string, { apuntados: number; planes: number }>();
    for (const m of this.sb.municipios) pueblos.set(m.nombre, { apuntados: 0, planes: 0 });
    for (const s of stats) {
      const nombre = s.municipio || 'Comarca';
      const p = pueblos.get(nombre) || { apuntados: 0, planes: 0 };
      p.apuntados += n(s.confirmed_count);
      p.planes += 1;
      pueblos.set(nombre, p);
    }
    const porPueblo = [...pueblos.entries()]
      .map(([nombre, v]) => ({ nombre, ...v, color: colorPueblo(nombre) }))
      .sort((a, b) => b.apuntados - a.apuntados);
    const maxPueblo = Math.max(1, ...porPueblo.map((p) => p.apuntados));

    // Gente apuntada por tipo de actividad
    const cats = new Map<string, number>();
    for (const s of stats) {
      const slug = s.category || eventoPorId.get(s.id)?.category || 'otro';
      cats.set(slug, (cats.get(slug) || 0) + n(s.confirmed_count));
    }
    const porCategoria = [...cats.entries()]
      .map(([slug, apuntados]) => ({ slug, nombre: this.nombreCategoria(slug), apuntados, color: estiloCategoria(slug).tinta, fondo: estiloCategoria(slug).fondo }))
      .sort((a, b) => b.apuntados - a.apuntados);
    const maxCategoria = Math.max(1, ...porCategoria.map((c) => c.apuntados));

    // Planes con más gente y cómo de llenos están
    const planes = stats.map((s) => {
      const apuntados = n(s.confirmed_count);
      const plazas = n(s.max_capacity);
      const ocupacion = plazas ? Math.min(100, Math.round((apuntados / plazas) * 100)) : null;
      return {
        id: s.id,
        titulo: s.title,
        pueblo: s.municipio,
        estado: s.status,
        apuntados,
        plazas,
        ocupacion,
        espera: n(s.en_espera),
        asistieron: n(s.asistieron),
        coches: n(s.coches_ofrecidos),
        buscanCoche: n(s.buscan_coche),
        media: s.media_estrellas != null ? Number(s.media_estrellas) : null,
        color: colorPueblo(s.municipio),
        inicio: s.starts_at || eventoPorId.get(s.id)?.starts_at || null,
      };
    });
    const topPlanes = [...planes].filter((p) => p.apuntados > 0 || p.espera > 0).sort((a, b) => b.apuntados - a.apuntados || b.espera - a.espera).slice(0, 8);
    const maxPlan = Math.max(1, ...topPlanes.map((p) => Math.max(p.apuntados, p.plazas)));

    // Embudo: plazas → apuntados → fueron (solo planes ya terminados para la asistencia)
    const terminados = planes.filter((p) => p.estado === 'finalizado');
    const embudo = [
      { texto: 'Plazas ofrecidas', valor: planes.reduce((a, p) => a + p.plazas, 0), color: '#a9c7c3' },
      { texto: 'Personas apuntadas', valor: planes.reduce((a, p) => a + p.apuntados, 0), color: '#2b6f68' },
      { texto: 'En lista de espera', valor: planes.reduce((a, p) => a + p.espera, 0), color: '#f4b740' },
      { texto: 'Fueron de verdad', valor: terminados.reduce((a, p) => a + p.asistieron, 0), color: '#4e4386' },
    ];
    const maxEmbudo = Math.max(1, ...embudo.map((e) => e.valor));
    const apuntadosTerminados = terminados.reduce((a, p) => a + p.apuntados, 0);
    const asistenciaTerminados = apuntadosTerminados
      ? Math.round((terminados.reduce((a, p) => a + p.asistieron, 0) / apuntadosTerminados) * 100)
      : null;

    // Planes por estado (gráfico circular)
    const estadosMap = new Map<string, number>();
    for (const p of planes) estadosMap.set(p.estado, (estadosMap.get(p.estado) || 0) + 1);
    const totalPlanes = planes.length || 1;
    let acumulado = 0;
    const estados = [...estadosMap.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([estado, cantidad]) => {
        const pct = (cantidad / totalPlanes) * 100;
        const trozo = { estado, texto: etiquetaEstado(estado), cantidad, pct, desde: acumulado, color: COLOR_ESTADO[estado] || '#9aa3a1' };
        acumulado += pct;
        return trozo;
      });

    // Inscripciones de los últimos 30 días
    const dias: { fecha: Date; clave: string; cantidad: number }[] = [];
    for (let i = 29; i >= 0; i--) {
      const d = new Date();
      d.setHours(0, 0, 0, 0);
      d.setDate(d.getDate() - i);
      dias.push({ fecha: d, clave: claveDia(d), cantidad: 0 });
    }
    const indiceDia = new Map(dias.map((d, i) => [d.clave, i]));
    for (const ins of this.inscripcionesRecientes) {
      const i = indiceDia.get(claveDia(new Date(ins.created_at)));
      if (i != null && ins.status !== 'cancelada') dias[i].cantidad++;
    }
    const maxDia = Math.max(1, ...dias.map((d) => d.cantidad));
    const ultimos7 = dias.slice(-7).reduce((a, d) => a + d.cantidad, 0);
    const previos7 = dias.slice(-14, -7).reduce((a, d) => a + d.cantidad, 0);

    // Coche compartido
    const cochesOfrecidos = planes.reduce((a, p) => a + p.coches, 0);
    const buscanCoche = planes.reduce((a, p) => a + p.buscanCoche, 0);

    // Consejos automáticos a partir de los datos
    const consejos: Consejo[] = [];
    const ahora = Date.now();
    const proximos = planes.filter((p) => !['finalizado', 'cancelado'].includes(p.estado) && (!p.inicio || new Date(p.inicio).getTime() > ahora));
    for (const p of [...planes].filter((x) => x.espera > 0).sort((a, b) => b.espera - a.espera).slice(0, 3)) {
      consejos.push({
        tipo: 'exito',
        icono: 'people-outline',
        texto: `«${p.titulo}» tiene ${p.espera} ${p.espera === 1 ? 'persona' : 'personas'} en lista de espera. Hay demanda: valora ampliar plazas o repetirlo.`,
      });
    }
    for (const p of proximos.filter((x) => x.ocupacion != null && x.ocupacion < 30).slice(0, 3)) {
      consejos.push({
        tipo: 'aviso',
        icono: 'megaphone-outline',
        texto: `«${p.titulo}» (${p.pueblo}) solo ha llenado el ${p.ocupacion}% de sus plazas. Conviene darle más difusión.`,
      });
    }
    if (buscanCoche > cochesOfrecidos) {
      consejos.push({
        tipo: 'aviso',
        icono: 'car-outline',
        texto: `${buscanCoche} ${buscanCoche === 1 ? 'persona busca' : 'personas buscan'} coche y solo hay ${cochesOfrecidos} ${cochesOfrecidos === 1 ? 'ofrecido' : 'ofrecidos'}. Anima a compartir coche.`,
      });
    }
    const sinPlanes = porPueblo.filter((p) => p.planes === 0).map((p) => p.nombre);
    if (sinPlanes.length) {
      consejos.push({
        tipo: 'info',
        icono: 'location-outline',
        texto: `${unirNombres(sinPlanes)} ${sinPlanes.length === 1 ? 'no tiene' : 'no tienen'} ningún plan. Busca asociaciones o vecinos que quieran organizar algo allí.`,
      });
    }
    if (asistenciaTerminados != null && asistenciaTerminados < 60) {
      consejos.push({
        tipo: 'aviso',
        icono: 'notifications-outline',
        texto: `Solo va el ${asistenciaTerminados}% de quienes se apuntan. Un recordatorio el día antes puede ayudar.`,
      });
    }
    const mejor = [...planes].filter((p) => p.media).sort((a, b) => (b.media || 0) - (a.media || 0))[0];
    if (mejor) {
      consejos.push({
        tipo: 'exito',
        icono: 'star-outline',
        texto: `El plan mejor valorado es «${mejor.titulo}» (${textoValoracion(mejor.media || 0)} estrellas). Es buena idea repetir ese tipo de actividad.`,
      });
    }
    if (porCategoria[0]?.apuntados) {
      consejos.push({
        tipo: 'info',
        icono: 'sparkles-outline',
        texto: `Lo que más gente atrae es «${porCategoria[0].nombre}», con ${porCategoria[0].apuntados} personas apuntadas.`,
      });
    }

    const conPlazas = planes.filter((p) => p.ocupacion != null);
    this.graficos = {
      porPueblo,
      maxPueblo,
      porCategoria,
      maxCategoria,
      topPlanes,
      maxPlan,
      planes,
      embudo,
      maxEmbudo,
      estados,
      totalPlanes: planes.length,
      dias,
      maxDia,
      ultimos7,
      previos7,
      cochesOfrecidos,
      buscanCoche,
      ocupacionMedia: conPlazas.length ? Math.round(conPlazas.reduce((a, p) => a + (p.ocupacion || 0), 0) / conPlazas.length) : null,
      asistenciaTerminados,
      consejos,
    };
  }

  /** Porcentaje de ancho de una barra (con un mínimo visible si hay algo). */
  /** Planes sin nadie apuntado (para no llenar el ranking de ceros). */
  get planesSinGente(): number {
    return this.graficos.planes.filter((p) => p.apuntados === 0 && p.espera === 0).length;
  }

  /** ¿Hay alguna inscripción en los últimos 30 días? */
  get hayDias(): boolean {
    return this.graficos.dias.some((d) => d.cantidad > 0);
  }

  ancho(valor: number, max: number): number {
    if (!valor) return 0;
    return Math.max(3, Math.round((valor / (max || 1)) * 100));
  }

  /** Color de la barra de ocupación: lleno, bien o flojo. */
  colorOcupacion(pct: number | null): string {
    if (pct == null) return '#9aa3a1';
    if (pct >= 90) return '#b42318';
    if (pct >= 50) return '#2b6f68';
    return '#f4b740';
  }

  etiquetaDia(d: Date): string {
    return new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'short' }).format(d);
  }

  /** Tendencia semanal en texto: "+40% que la semana anterior". */
  get tendenciaSemanal(): { texto: string; sube: boolean | null } {
    const { ultimos7, previos7 } = this.graficos;
    if (!previos7 && !ultimos7) return { texto: 'Sin inscripciones en las dos últimas semanas', sube: null };
    if (!previos7) return { texto: 'Primeras inscripciones en dos semanas', sube: true };
    const cambio = Math.round(((ultimos7 - previos7) / previos7) * 100);
    if (cambio === 0) return { texto: 'Igual que la semana anterior', sube: null };
    return { texto: (cambio > 0 ? '+' : '') + cambio + '% respecto a la semana anterior', sube: cambio > 0 };
  }

  // --- PESTAÑA 6: DEMO ---
  cargarEjemploDemo(tipo: 'real' | 'incompleto' | 'spam') {
    if (tipo === 'real') {
      this.demoPageUrl = 'https://facebook.com/asociacion-lucainena';
      this.demoText =
        '🥾 RUTA DE LAS MINAS DE LUCAINENA\nEste sábado 28 de noviembre a las 09:30 salimos desde la Plaza del Ayuntamiento. Recorrido guiado de 3 horas, gratis y apto para familias (30 plazas).';
      this.demoImageUrl = '';
    } else if (tipo === 'incompleto') {
      this.demoPageUrl = 'https://facebook.com/club-velefique';
      this.demoText =
        '🎶 TALLER DE FOLCLORE en Velefique este domingo a las 11:00 en el Salón Cultural. ¡Apúntate!';
      this.demoImageUrl = '';
    } else {
      this.demoPageUrl = 'https://facebook.com/promo-externa';
      this.demoText =
        '🎰💰 GANA 500€ DESDE CASA HOY MISMO!! Haz clic aquí bit.ly/dinero-rapido-casino oferta limitada cripto!!';
      this.demoImageUrl = '';
    }
    this.cdr.detectChanges();
  }

  async ejecutarDemo(accion: 'facebook' | 'recomendaciones' | 'resenas' | 'promociones' | 'health') {
    if (accion === 'facebook' && !this.demoText.trim()) {
      this.avisos.aviso('Escribe un texto o carga uno de los ejemplos.');
      return;
    }
    if (accion === 'resenas' && !this.demoEventIdSeleccionado) {
      this.avisos.aviso('Elige primero un plan.');
      return;
    }

    this.ejecutandoDemo = true;
    this.demoResultado = 'Un momento… (si el servidor estaba dormido puede tardar hasta un minuto)';
    this.cdr.detectChanges();

    try {
      let res: any;
      if (accion === 'facebook') {
        res = await this.api.ingestaFacebook({
          source_url: `demo-${Date.now()}`,
          page_url: this.demoPageUrl.trim() || 'https://facebook.com/demo',
          text: this.demoText.trim(),
          image_url: this.demoImageUrl.trim() || undefined
        });
        await this.cargarIngestas();
        this.demoResultado = `Publicación recibida. Estado: ${etiquetaIngesta(res?.estado)}. Puedes verla en "Detectados por IA".`;
      } else if (accion === 'recomendaciones') {
        res = await this.api.enviarRecomendaciones();
        this.demoResultado = `Recomendaciones enviadas a ${res?.enviados ?? 0} vecinos por Telegram.`;
      } else if (accion === 'resenas') {
        res = await this.api.pedirResenas(this.demoEventIdSeleccionado);
        this.demoResultado = `Hemos pedido su opinión a ${res?.enviados ?? 0} personas que asistieron.`;
      } else if (accion === 'promociones') {
        res = await this.api.revisarPromociones();
        await this.cargarPublicadores();
        const p = res?.promovidos?.length || 0;
        const r = res?.a_revisar?.length || 0;
        const nombres = (res?.promovidos || []).map((x: any) => x.full_name).filter(Boolean).join(', ');
        this.demoResultado = p || r
          ? `${p} ${p === 1 ? 'persona ascendida' : 'personas ascendidas'} a Publicador${nombres ? ' (' + nombres + ')' : ''}. ${r} pendientes de tu decisión.`
          : 'No hay candidaturas nuevas ahora mismo.';
      } else {
        res = await this.api.health();
        this.demoResultado = res?.ok ? 'El servidor está despierto y funcionando.' : 'El servidor ha respondido, pero algo no va bien.';
      }
      this.avisos.ok('Hecho.');
    } catch (err: any) {
      if (err?.status === 409) {
        this.demoResultado = 'Este plan todavía no ha terminado: las opiniones se piden al acabar.';
      } else if (err?.status === 403) {
        this.demoResultado = 'Tu cuenta no tiene permiso para esta acción.';
      } else if (err?.status === 401) {
        this.demoResultado = 'Tu sesión ha caducado. Vuelve a entrar.';
      } else {
        this.demoResultado = 'No hemos podido conectar con el servidor. Espera unos segundos y vuelve a intentarlo.';
      }
    } finally {
      this.ejecutandoDemo = false;
      this.cdr.detectChanges();
    }
  }

  formatoMedia(v: unknown): string {
    const n = Number(v);
    return v != null && !isNaN(n) && n > 0 ? '★ ' + textoValoracion(n) : '—';
  }

  textoSolapamiento(sol: any): string {
    return `«${sol.titulo_a}» (${sol.pueblo_a}) y «${sol.titulo_b}» (${sol.pueblo_b}) coinciden${sol.desde ? ' el ' + fechaCorta(sol.desde) : ''}.`;
  }
}

/**
 * Junta los textos de un elemento (y de sus objetos anidados, como el pueblo o el plan)
 * para poder buscar en ellos. Ignora identificadores y enlaces.
 */
function textoBuscable(x: unknown, profundidad = 0): string {
  if (x == null || profundidad > 2) return '';
  if (typeof x === 'string') return x;
  if (typeof x === 'number') return String(x);
  if (typeof x !== 'object') return '';
  return Object.entries(x as Record<string, unknown>)
    .filter(([k]) => k !== 'id' && !k.endsWith('_id') && !k.endsWith('_url'))
    .map(([, v]) => (typeof v === 'number' ? '' : textoBuscable(v, profundidad + 1)))
    .join(' ');
}

export interface VecinoFila {
  id: string;
  full_name: string | null;
  email: string | null;
  rol: string;
  reputation_score: number;
  municipio_id?: number | null;
}

export interface Consejo {
  tipo: 'exito' | 'aviso' | 'info';
  icono: string;
  texto: string;
}

export interface PlanGrafico {
  id: string;
  titulo: string;
  pueblo: string;
  estado: string;
  apuntados: number;
  plazas: number;
  ocupacion: number | null;
  espera: number;
  asistieron: number;
  coches: number;
  buscanCoche: number;
  media: number | null;
  color: string;
  inicio: string | null;
}

export interface Graficos {
  porPueblo: { nombre: string; apuntados: number; planes: number; color: string }[];
  maxPueblo: number;
  porCategoria: { slug: string; nombre: string; apuntados: number; color: string; fondo: string }[];
  maxCategoria: number;
  topPlanes: PlanGrafico[];
  maxPlan: number;
  planes: PlanGrafico[];
  embudo: { texto: string; valor: number; color: string }[];
  maxEmbudo: number;
  estados: { estado: string; texto: string; cantidad: number; pct: number; desde: number; color: string }[];
  totalPlanes: number;
  dias: { fecha: Date; clave: string; cantidad: number }[];
  maxDia: number;
  ultimos7: number;
  previos7: number;
  cochesOfrecidos: number;
  buscanCoche: number;
  ocupacionMedia: number | null;
  asistenciaTerminados: number | null;
  consejos: Consejo[];
}

const GRAFICOS_VACIOS: Graficos = {
  porPueblo: [],
  maxPueblo: 1,
  porCategoria: [],
  maxCategoria: 1,
  topPlanes: [],
  maxPlan: 1,
  planes: [],
  embudo: [],
  maxEmbudo: 1,
  estados: [],
  totalPlanes: 0,
  dias: [],
  maxDia: 1,
  ultimos7: 0,
  previos7: 0,
  cochesOfrecidos: 0,
  buscanCoche: 0,
  ocupacionMedia: null,
  asistenciaTerminados: null,
  consejos: [],
};

const COLOR_ESTADO: Record<string, string> = {
  abierto: '#2b6f68',
  completo: '#b42318',
  inscripcion_cerrada: '#c98f1c',
  en_curso: '#4e4386',
  pospuesto: '#f4b740',
  finalizado: '#8a8f8d',
  cancelado: '#d9cdb9',
  pendiente_moderacion: '#2a5a86',
};

function claveDia(d: Date): string {
  return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate();
}

/** "A, B y C" */
function unirNombres(nombres: string[]): string {
  if (nombres.length <= 1) return nombres.join('');
  return nombres.slice(0, -1).join(', ') + ' y ' + nombres[nombres.length - 1];
}

/** Datos del formulario de edición de un plan. */
export interface EdicionPlan {
  title: string;
  category: string;
  municipio_id: number | null;
  fecha: string;
  hora: string;
  duration_minutes: number | null;
  location_name: string;
  price: number | null;
  max_capacity: number | null;
  min_age: number | null;
  max_age: number | null;
  is_accessible: boolean;
  description: string;
}
