import { Component, OnDestroy, OnInit, ViewChild, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { IonHeader, IonToolbar, IonButtons, IonBackButton, IonTitle, IonContent, IonFooter, IonButton, IonIcon, IonSpinner } from '@ionic/angular';
import { addIcons } from 'ionicons';
import {
  accessibility,
  addCircleOutline,
  removeCircleOutline,
  alertCircle,
  arrowBack,
  arrowForward,
  calendarOutline,
  cashOutline,
  checkmarkCircle,
  createOutline,
  image,
  imageOutline,
  location,
  locationOutline,
  paperPlaneOutline,
  peopleOutline,
  search,
  send,
  sparkles,
  timeOutline,
} from 'ionicons/icons';
import * as L from 'leaflet';
import { SupabaseService } from '../../../core/supabase.service';
import { ApiService } from '../../../core/api.service';
import { AvisosService, mensajeAmigable } from '../../../core/avisos.service';
import { GeocodingService } from '../../../core/geocoding.service';
import { colorPueblo, estiloCategoria, fechaLarga, isoALocalInput, listaCampos, normalizar, textoPrecio, ubicacionCompleta } from '../../../core/ui';

@Component({
  selector: 'app-proponer',
  templateUrl: './proponer.page.html',
  standalone: true,
  imports: [FormsModule, RouterModule, IonHeader, IonToolbar, IonButtons, IonBackButton, IonTitle, IonContent, IonFooter, IonButton, IonIcon, IonSpinner],
})
export class ProponerPage implements OnInit, OnDestroy {
  sb = inject(SupabaseService);
  private api = inject(ApiService);
  private router = inject(Router);
  private avisos = inject(AvisosService);
  private geo = inject(GeocodingService);
  private route = inject(ActivatedRoute);

  @ViewChild(IonContent) contenido?: IonContent;

  // Campos del formulario
  title = '';
  category = 'otro';
  municipioId: number | null = null;
  fecha = '';
  hora = '';
  /** Día y hora juntos ('AAAA-MM-DDTHH:MM'), a partir de los dos campos sencillos. */
  get startsAtLocal(): string {
    return this.fecha && this.hora ? this.fecha + 'T' + this.hora : '';
  }
  set startsAtLocal(valor: string) {
    const [f, h] = (valor || '').split('T');
    this.fecha = f || '';
    this.hora = (h || '').slice(0, 5);
  }
  inscripcionHastaLocal = '';
  durationMinutes: number | null = 120;
  locationName = '';
  price: number | null = 0;
  maxCapacity: number | null = null;
  minAge: number | null = null;
  maxAge: number | null = null;
  description = '';
  isAccessible = false;
  posterUrl = signal('');

  // Preguntas de sí/no que sustituyen a campos técnicos
  esGratis = true;
  hayLimite = false;
  todasEdades = true;
  verMasFecha = false;

  // ---------- Asistente paso a paso ----------
  /** 0 = cómo empezar, 1 = qué, 2 = cuándo, 3 = dónde, 4 = detalles, 5 = revisar. */
  paso = signal(0);
  modoCartel = signal(false);
  pasosIntentados = signal<Set<number>>(new Set());
  readonly nombresPasos = ['Cómo empezar', '¿Qué plan es?', '¿Cuándo es?', '¿Dónde es?', 'Últimos detalles', 'Revisa y envía'];
  readonly totalPasos = 5;

  readonly opcionesDia = calcularDias();
  readonly opcionesHora = [
    { texto: '10:00', valor: '10:00' },
    { texto: '12:00', valor: '12:00' },
    { texto: '17:00', valor: '17:00' },
    { texto: '19:00', valor: '19:00' },
  ];
  readonly opcionesDuracion = [
    { texto: '1 hora', valor: 60 },
    { texto: '2 horas', valor: 120 },
    { texto: 'Media jornada', valor: 240 },
    { texto: 'Todo el día', valor: 480 },
  ];

  // Coordenadas
  lat = signal<number | null>(null);
  lng = signal<number | null>(null);
  buscandoLugar = signal(false);
  estadoLugar = signal<'' | 'encontrado' | 'pueblo' | 'no-encontrado'>('');

  // IA
  textoCartelOpcional = '';
  camposFaltantes = signal<string[]>([]);
  subiendoFoto = signal(false);
  extrayendoIa = signal(false);
  guardando = signal(false);

  faltantesTexto = computed(() => listaCampos(this.camposFaltantes()));

  // Errores del formulario: se muestran tras el primer intento de envío
  // y se recalculan solos mientras la persona corrige.
  intentado = signal(false);
  estiloCategoria = estiloCategoria;
  textoPrecio = textoPrecio;
  erroresServidor = signal<Partial<Record<CampoPlan, string>>>({});

  private map?: L.Map;
  private marker?: L.Marker;

  constructor() {
    addIcons({
      accessibility,
      addCircleOutline,
      removeCircleOutline,
      alertCircle,
      arrowBack,
      arrowForward,
      calendarOutline,
      cashOutline,
      checkmarkCircle,
      createOutline,
      image,
      imageOutline,
      location,
      locationOutline,
      paperPlaneOutline,
      peopleOutline,
      search,
      send,
      sparkles,
      timeOutline,
    });
  }

  /** Tras enviar un plan, la próxima visita empieza con el formulario vacío. */
  private debeReiniciar = false;

  ngOnInit() {
    this.aplicarModo();
  }

  /**
   * La página vive dentro de las pestañas de Ionic, que la guardan en memoria:
   * ngOnInit solo se ejecuta la primera vez. Por eso aquí se vacía el formulario
   * si ya se envió un plan y se vuelve a leer el camino elegido (?modo=...).
   */
  /** Publicadores y moderadores publican sin revisión (lo decide la BD al guardar). */
  get publicaDirecto(): boolean {
    return this.sb.rol === 'publicador' || this.sb.rol === 'moderador';
  }

  ionViewWillEnter() {
    // Por si le acaban de hacer Publicador/a: se relee su rol
    this.sb.loadProfile().catch(() => null);
    if (this.debeReiniciar) {
      this.reiniciarFormulario();
      this.debeReiniciar = false;
    }
    this.aplicarModo();
  }

  private aplicarModo() {
    // Desde Inicio se puede llegar ya con un camino elegido
    // Solo si está al principio: un borrador a medias no se pierde al cambiar de pestaña
    const modo = this.route.snapshot.queryParamMap.get('modo');
    if (!modo || this.paso() !== 0) return;
    if (modo === 'cartel') {
      this.modoCartel.set(true);
      this.irAPaso(0);
    }
    if (modo === 'pasos') {
      this.modoCartel.set(false);
      this.irAPaso(1);
    }
  }

  /** Deja el asistente como recién abierto. */
  reiniciarFormulario() {
    this.destruirMapa();
    this.title = '';
    this.category = 'otro';
    this.municipioId = null;
    this.fecha = '';
    this.hora = '';
    this.inscripcionHastaLocal = '';
    this.durationMinutes = 120;
    this.locationName = '';
    this.price = 0;
    this.maxCapacity = null;
    this.minAge = null;
    this.maxAge = null;
    this.description = '';
    this.isAccessible = false;
    this.posterUrl.set('');
    const selectorCartel = document.getElementById('cartel') as HTMLInputElement | null;
    if (selectorCartel) selectorCartel.value = '';
    this.esGratis = true;
    this.hayLimite = false;
    this.todasEdades = true;
    this.verMasFecha = false;
    this.paso.set(0);
    this.modoCartel.set(false);
    this.pasosIntentados.set(new Set());
    this.lat.set(null);
    this.lng.set(null);
    this.buscandoLugar.set(false);
    this.estadoLugar.set('');
    this.textoCartelOpcional = '';
    this.camposFaltantes.set([]);
    this.intentado.set(false);
    this.erroresServidor.set({});
  }

  // ---------- Navegación del asistente ----------
  nombrePaso = computed(() => this.nombresPasos[this.paso()]);

  irAPaso(n: number) {
    const antes = this.paso();
    if (antes === 3 && n !== 3) this.destruirMapa();
    this.paso.set(n);
    this.contenido?.scrollToTop(200);
    if (n === 3) setTimeout(() => this.initMapa(), 60);
  }

  /** Comprueba solo las preguntas de este paso antes de avanzar. */
  siguiente() {
    const actual = this.paso();
    const marcados = new Set(this.pasosIntentados());
    marcados.add(actual);
    this.pasosIntentados.set(marcados);
    if (this.listaErrores.some((e) => e.paso === actual)) {
      this.mostrarResumenErrores();
      return;
    }
    this.irAPaso(actual + 1);
  }

  atras() {
    if (this.paso() > 0) this.irAPaso(this.paso() - 1);
  }

  elegirPueblo(id: number) {
    this.municipioId = id;
    this.limpiarServidor('municipio');
    this.alCambiarPueblo();
  }

  elegirGratis(gratis: boolean) {
    this.esGratis = gratis;
    if (gratis) this.price = 0;
    else if (!this.price) this.price = null;
  }

  elegirLimite(limite: boolean) {
    this.hayLimite = limite;
    if (!limite) this.maxCapacity = null;
  }

  elegirEdades(todas: boolean) {
    this.todasEdades = todas;
    if (todas) {
      this.minAge = null;
      this.maxAge = null;
    }
  }

  // ---------- Último día para apuntarse ----------
  /** Solo el día; la hora se pone sola (fin del día, o la hora del plan si es el mismo día). */
  get limiteFecha(): string {
    return (this.inscripcionHastaLocal || '').split('T')[0];
  }
  set limiteFecha(dia: string) {
    if (!dia) {
      this.inscripcionHastaLocal = '';
      return;
    }
    const hora = dia === this.fecha && this.hora ? this.hora : '23:59';
    this.inscripcionHastaLocal = dia + 'T' + hora;
    this.limpiarServidor('inscripcion_hasta');
  }

  /** Atajos calculados a partir del día del plan (sin fechas ya pasadas). */
  get opcionesLimite(): { texto: string; valor: string }[] {
    const lista = [{ texto: 'Hasta que empiece', valor: '' }];
    if (!this.fecha) return lista;
    const [a, m, d] = this.fecha.split('-').map(Number);
    const hoy = new Date();
    hoy.setHours(0, 0, 0, 0);
    const ymd = (x: Date) => x.getFullYear() + '-' + String(x.getMonth() + 1).padStart(2, '0') + '-' + String(x.getDate()).padStart(2, '0');
    for (const [texto, dias] of [
      ['El día antes', 1],
      ['2 días antes', 2],
      ['Una semana antes', 7],
    ] as [string, number][]) {
      const f = new Date(a, m - 1, d - dias);
      if (f >= hoy) lista.push({ texto, valor: ymd(f) });
    }
    return lista;
  }

  get textoLimite(): string {
    if (!this.inscripcionHastaLocal) return '';
    const d = new Date(this.inscripcionHastaLocal);
    return isNaN(d.getTime()) ? '' : 'Apuntarse hasta el ' + fechaLarga(d.toISOString());
  }

  // ---------- Textos del resumen final ----------
  get nombreCategoria(): string {
    return this.sb.categorias.find((c) => c.slug === this.category)?.nombre || 'Otro';
  }
  get textoCuando(): string {
    if (!this.startsAtLocal) return 'Sin fecha';
    const d = new Date(this.startsAtLocal);
    return isNaN(d.getTime()) ? 'Sin fecha' : fechaLarga(d.toISOString());
  }
  get textoDuracion(): string {
    const m = Number(this.durationMinutes) || 0;
    if (!m) return 'Sin duración indicada';
    const op = this.opcionesDuracion.find((o) => o.valor === m);
    if (op) return op.texto;
    return m < 60 ? m + ' minutos' : Math.round((m / 60) * 10) / 10 + ' horas';
  }
  get textoPlazas(): string {
    return this.hayLimite && this.maxCapacity ? this.maxCapacity + ' plazas' : 'Sin límite de plazas';
  }
  get textoEdades(): string {
    if (this.todasEdades || (!this.minAge && !this.maxAge)) return 'Para todas las edades';
    if (this.minAge && this.maxAge) return 'De ' + this.minAge + ' a ' + this.maxAge + ' años';
    if (this.minAge) return 'Desde ' + this.minAge + ' años';
    return 'Hasta ' + this.maxAge + ' años';
  }
  pasoConError(n: number): boolean {
    return this.listaErrores.some((e) => e.paso === n);
  }

  ionViewDidEnter() {
    if (this.paso() === 3) this.initMapa();
  }

  ionViewWillLeave() {
    this.destruirMapa();
  }

  ngOnDestroy() {
    this.destruirMapa();
  }

  get nombrePueblo(): string {
    return this.sb.municipios.find((m) => m.id === Number(this.municipioId))?.nombre || '';
  }

  colorPuebloDe(nombre: string): string {
    return colorPueblo(nombre);
  }

  get colorDelPueblo(): string {
    return colorPueblo(this.nombrePueblo);
  }

  get vistaPreviaLugar(): string {
    return ubicacionCompleta(this.locationName, this.nombrePueblo);
  }

  // ---------- Mapa y coordenadas ----------
  private initMapa() {
    if (this.map) return;
    const el = document.getElementById('mapa-proponer');
    if (!el) return;
    this.map = L.map(el, { center: [37.15, -2.3], zoom: 10 });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18, attribution: '&copy; OpenStreetMap' }).addTo(this.map);
    this.map.on('click', (e: L.LeafletMouseEvent) => this.ponerPunto(e.latlng.lat, e.latlng.lng, true));
    if (this.lat() != null && this.lng() != null) this.ponerPunto(this.lat()!, this.lng()!, false);
    setTimeout(() => this.map?.invalidateSize(), 200);
  }

  private destruirMapa() {
    this.map?.remove();
    this.map = undefined;
    this.marker = undefined;
  }

  private ponerPunto(lat: number, lng: number, manual: boolean) {
    this.lat.set(Number(lat.toFixed(6)));
    this.lng.set(Number(lng.toFixed(6)));
    if (manual) this.estadoLugar.set('encontrado');
    if (!this.map) return;
    const icon = L.divIcon({
      className: '',
      html: `<div class="custom-pin" style="background-color:${this.colorDelPueblo}"><span>●</span></div>`,
      iconSize: [34, 34],
      iconAnchor: [17, 34],
    });
    if (!this.marker) {
      this.marker = L.marker([lat, lng], { icon, draggable: true, title: 'Punto de encuentro' }).addTo(this.map);
      this.marker.on('dragend', () => {
        const p = this.marker!.getLatLng();
        this.ponerPunto(p.lat, p.lng, true);
      });
    } else {
      this.marker.setLatLng([lat, lng]);
      this.marker.setIcon(icon);
    }
    this.map.setView([lat, lng], Math.max(this.map.getZoom(), 15));
  }

  /** Busca latitud y longitud a partir del lugar y el pueblo escritos. */
  async buscarCoordenadas() {
    const pueblo = this.nombrePueblo;
    if (!this.locationName.trim() && !pueblo) return;

    this.buscandoLugar.set(true);
    try {
      const r = await this.geo.buscar(this.locationName, pueblo);
      if (!r) {
        this.estadoLugar.set('no-encontrado');
        return;
      }
      this.estadoLugar.set(r.exacto ? 'encontrado' : 'pueblo');
      this.ponerPunto(r.lat, r.lng, false);
    } finally {
      this.buscandoLugar.set(false);
    }
  }

  alCambiarPueblo() {
    const m = this.sb.municipios.find((x) => x.id === Number(this.municipioId));
    if (this.locationName.trim()) {
      this.buscarCoordenadas();
    } else if (m && this.map) {
      this.map.setView([m.lat, m.lng], 14);
    }
  }

  // ---------- Cartel e IA ----------
  async onFileSelected(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      this.avisos.aviso('El archivo debe ser una imagen.');
      return;
    }

    this.subiendoFoto.set(true);
    try {
      const ext = (file.name.split('.').pop() || 'jpg').toLowerCase();
      const filePath = `${this.sb.currentProfile?.id || 'anonimo'}/cartel-${Date.now()}.${ext}`;
      const { error } = await this.sb.client.storage.from('posters').upload(filePath, file, { contentType: file.type });
      if (error) throw error;
      this.posterUrl.set(this.sb.client.storage.from('posters').getPublicUrl(filePath).data.publicUrl);
      this.avisos.ok('Cartel subido');
    } catch (err) {
      this.avisos.error(err, 'No hemos podido subir el cartel. Prueba con otra imagen.');
    } finally {
      this.subiendoFoto.set(false);
    }
  }

  async rellenarConIa() {
    if (!this.posterUrl() && !this.textoCartelOpcional.trim()) {
      this.avisos.aviso('Sube primero la foto de un cartel o pega el texto del anuncio.');
      return;
    }

    this.extrayendoIa.set(true);
    try {
      const res = await this.api.extraerEvento({
        image_url: this.posterUrl() || undefined,
        text: this.textoCartelOpcional.trim() || undefined,
      });

      if (res.title) this.title = res.title;
      if (res.category) this.category = res.category;
      if (res.location_name) this.locationName = res.location_name;
      if (res.price != null) this.price = res.price;
      if (res.max_capacity != null) this.maxCapacity = res.max_capacity;
      if (res.duration_minutes) this.durationMinutes = res.duration_minutes;
      if (res.min_age != null) this.minAge = res.min_age;
      if (res.max_age != null) this.maxAge = res.max_age;
      if (res.description) this.description = res.description;
      if (typeof res.is_accessible === 'boolean') this.isAccessible = res.is_accessible;
      if (res.starts_at) this.startsAtLocal = isoALocalInput(res.starts_at);
      if (res.inscripcion_hasta) this.inscripcionHastaLocal = isoALocalInput(res.inscripcion_hasta);
      if (res.municipio) {
        const buscado = normalizar(res.municipio);
        const m = this.sb.municipios.find((x) => normalizar(x.nombre).includes(buscado) || buscado.includes(normalizar(x.nombre)));
        if (m) this.municipioId = m.id;
      }

      this.camposFaltantes.set(res.missing || []);
      this.esGratis = !(Number(this.price) > 0);
      this.hayLimite = !!this.maxCapacity;
      this.todasEdades = !this.minAge && !this.maxAge;
      if (this.locationName || this.municipioId) await this.buscarCoordenadas();

      this.irAPaso(5);
      if (this.camposFaltantes().length) {
        this.avisos.info(`Hemos rellenado lo que pone el cartel. Completa lo que falta: ${this.faltantesTexto()}.`);
      } else {
        this.avisos.ok('¡Listo! Revisa los datos antes de enviar.');
      }
    } catch {
      this.avisos.aviso('El asistente no responde ahora mismo (puede tardar hasta un minuto en despertar). Prueba otra vez o rellena los campos a mano.');
    } finally {
      this.extrayendoIa.set(false);
    }
  }

  falta(campo: string): boolean {
    return this.camposFaltantes().includes(campo);
  }

  // ---------- Validación ----------
  /** Comprueba cada campo y devuelve, por campo, qué falla en lenguaje sencillo. */
  validar(): Partial<Record<CampoPlan, string>> {
    const e: Partial<Record<CampoPlan, string>> = {};
    const num = (v: number | string | null | undefined): number | null => (v === null || v === undefined || v === '' ? null : Number(v));

    if (!this.title.trim()) e.title = 'Escribe un título para que la gente sepa de qué va.';
    else if (this.title.trim().length < 3) e.title = 'Tiene que tener al menos 3 letras.';

    if (!this.municipioId) e.municipio = 'Elige en qué pueblo se hace.';

    if (!this.fecha && !this.hora) e.starts_at = 'Elige qué día es y a qué hora empieza.';
    else if (!this.fecha) e.starts_at = 'Elige qué día es.';
    else if (!this.hora) e.starts_at = 'Elige a qué hora empieza.';
    else if (isNaN(new Date(this.startsAtLocal).getTime())) e.starts_at = 'La fecha no es válida.';
    else if (new Date(this.startsAtLocal) < new Date()) e.starts_at = 'Esa fecha ya ha pasado. Elige una fecha futura.';

    if (this.inscripcionHastaLocal && this.startsAtLocal && new Date(this.inscripcionHastaLocal) > new Date(this.startsAtLocal)) {
      e.inscripcion_hasta = 'Tiene que ser antes del inicio del plan.';
    }

    const dur = num(this.durationMinutes);
    if (dur !== null && (isNaN(dur) || dur < 0)) e.duration_minutes = 'No puede ser negativa.';

    const precio = num(this.price);
    if (!this.esGratis && (precio === null || isNaN(precio) || precio <= 0)) e.price = 'Escribe cuánto cuesta, o elige «Es gratis».';

    const plazas = num(this.maxCapacity);
    if (this.hayLimite && (plazas === null || isNaN(plazas) || plazas < 1 || !Number.isInteger(plazas))) {
      e.max_capacity = 'Escribe cuántas plazas hay (un número desde 1), o elige «Sin límite».';
    }

    const emin = num(this.minAge);
    const emax = num(this.maxAge);
    if (!this.todasEdades && emin === null && emax === null) e.min_age = 'Escribe la edad mínima, la máxima o las dos, o elige «Todas las edades».';
    if (emin !== null && (isNaN(emin) || emin < 0)) e.min_age = 'No puede ser negativa.';
    if (emax !== null && (isNaN(emax) || emax < 0)) e.max_age = 'No puede ser negativa.';
    else if (emin !== null && emax !== null && emax < emin) e.max_age = 'Es menor que la edad mínima (' + emin + ').';

    return e;
  }

  /** Errores visibles: los de validación (tras intentar enviar) más los que devuelva el servidor. */
  get errores(): Partial<Record<CampoPlan, string>> {
    const validos = this.validar();
    const intentados = this.pasosIntentados();
    const visibles: Partial<Record<CampoPlan, string>> = {};
    for (const c of CAMPOS_PLAN) {
      if (validos[c.campo] && (this.intentado() || intentados.has(c.paso))) visibles[c.campo] = validos[c.campo];
    }
    return { ...this.erroresServidor(), ...visibles };
  }

  get listaErrores(): { campo: CampoPlan; id: string; etiqueta: string; seccion: string; paso: number; mensaje: string }[] {
    const e = this.errores;
    return CAMPOS_PLAN.filter((c) => e[c.campo]).map((c) => ({ ...c, mensaje: e[c.campo] as string }));
  }

  error(campo: CampoPlan): string | undefined {
    return this.errores[campo];
  }

  /** Al editar un campo, el error que dio el servidor sobre él deja de valer. */
  limpiarServidor(campo: CampoPlan) {
    if (!this.erroresServidor()[campo]) return;
    const copia = { ...this.erroresServidor() };
    delete copia[campo];
    this.erroresServidor.set(copia);
  }

  irACampo(id: string) {
    const c = CAMPOS_PLAN.find((x) => x.id === id);
    if (c && c.paso !== this.paso()) {
      this.irAPaso(c.paso);
      setTimeout(() => this.irACampo(id), 250);
      return;
    }
    const el = document.getElementById(id);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.focus({ preventScroll: true });
  }

  private mostrarResumenErrores() {
    setTimeout(() => {
      const r = document.getElementById('resumen-errores');
      r?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      r?.focus({ preventScroll: true });
    }, 50);
  }

  /** Intenta saber a qué campo se refiere un error del servidor (columna citada en el mensaje). */
  private campoDeErrorServidor(err: unknown): CampoPlan | null {
    const e = err as { message?: string; details?: string; hint?: string } | null;
    const texto = [e?.message, e?.details, e?.hint].filter(Boolean).join(' ').toLowerCase();
    if (!texto) return null;
    const orden: CampoPlan[] = ['inscripcion_hasta', 'duration_minutes', 'max_capacity', 'min_age', 'max_age', 'starts_at', 'location_name', 'municipio', 'title', 'price'];
    for (const c of orden) {
      const columna = c === 'municipio' ? 'municipio_id' : c;
      if (texto.includes(columna)) return c;
    }
    return null;
  }

  // ---------- Guardar ----------
  async guardarEvento() {
    this.intentado.set(true);
    this.erroresServidor.set({});
    if (this.listaErrores.length) {
      this.irAPaso(this.listaErrores[0].paso);
      this.mostrarResumenErrores();
      return;
    }

    // Si escribió un lugar pero aún no se han buscado las coordenadas, se buscan ahora
    if (this.locationName.trim() && this.lat() == null) await this.buscarCoordenadas();

    this.guardando.set(true);
    try {
      // El estado lo decide la base de datos (trigger tg_evento_antes):
      // Publicador/a, moderador/a o asociación con auto_approve → publicado; resto → pendiente
      const { data, error } = await this.sb.client
        .from('eventos')
        .insert({
          title: this.title.trim().slice(0, 140),
          category: this.category,
          municipio_id: Number(this.municipioId),
          starts_at: new Date(this.startsAtLocal).toISOString(),
          inscripcion_hasta: this.inscripcionHastaLocal ? new Date(this.inscripcionHastaLocal).toISOString() : null,
          duration_minutes: this.durationMinutes ? Number(this.durationMinutes) : null,
          location_name: this.locationName.trim() || 'Por confirmar',
          lat: this.lat(),
          lng: this.lng(),
          price: this.esGratis ? 0 : Number(this.price) || 0,
          max_capacity: this.hayLimite && this.maxCapacity ? Number(this.maxCapacity) : null,
          min_age: !this.todasEdades && this.minAge ? Number(this.minAge) : null,
          max_age: !this.todasEdades && this.maxAge ? Number(this.maxAge) : null,
          description: this.description.trim() || null,
          is_accessible: this.isAccessible,
          poster_url: this.posterUrl() || null,
          origen: 'web',
        })
        .select()
        .single();
      if (error) throw error;

      // El plan ya está enviado: la próxima vez que se entre, formulario vacío
      this.debeReiniciar = true;

      if (data.status === 'pendiente_moderacion') {
        this.avisos.info('¡Gracias! Tu plan está pendiente de revisión. Te avisaremos cuando se publique.');
        this.router.navigateByUrl('/agenda');
      } else {
        this.avisos.ok('¡Publicado! Ya aparece en la agenda.');
        this.router.navigate(['/evento', data.id]);
      }
    } catch (err) {
      const campo = this.campoDeErrorServidor(err);
      if (campo) {
        this.erroresServidor.set({ [campo]: mensajeAmigable(err, 'El servidor no acepta este dato. Revísalo.') });
        const c = CAMPOS_PLAN.find((x) => x.campo === campo);
        if (c) this.irAPaso(c.paso);
        this.mostrarResumenErrores();
      } else {
        this.avisos.error(err, 'No hemos podido enviar el plan. Revisa los datos e inténtalo de nuevo.');
      }
    } finally {
      this.guardando.set(false);
    }
  }
}

export type CampoPlan =
  | 'title'
  | 'municipio'
  | 'starts_at'
  | 'inscripcion_hasta'
  | 'duration_minutes'
  | 'location_name'
  | 'price'
  | 'max_capacity'
  | 'min_age'
  | 'max_age';

/** Campos del formulario en el orden en que aparecen, con su id y su nombre para la lista de errores. */
export const CAMPOS_PLAN: { campo: CampoPlan; id: string; etiqueta: string; seccion: string; paso: number }[] = [
  { campo: 'title', id: 'f-title', etiqueta: 'Nombre del plan', seccion: 'paso 1', paso: 1 },
  { campo: 'starts_at', id: 'f-fecha', etiqueta: 'Día y hora', seccion: 'paso 2', paso: 2 },
  { campo: 'duration_minutes', id: 'f-duracion', etiqueta: 'Duración', seccion: 'paso 2', paso: 2 },
  { campo: 'inscripcion_hasta', id: 'f-limite', etiqueta: 'Último día para apuntarse', seccion: 'paso 2', paso: 2 },
  { campo: 'municipio', id: 'f-pueblo', etiqueta: 'Pueblo', seccion: 'paso 3', paso: 3 },
  { campo: 'location_name', id: 'f-lugar', etiqueta: 'Sitio exacto', seccion: 'paso 3', paso: 3 },
  { campo: 'price', id: 'f-precio', etiqueta: 'Precio', seccion: 'paso 4', paso: 4 },
  { campo: 'max_capacity', id: 'f-plazas', etiqueta: 'Plazas', seccion: 'paso 4', paso: 4 },
  { campo: 'min_age', id: 'f-edad-min', etiqueta: 'Edad mínima', seccion: 'paso 4', paso: 4 },
  { campo: 'max_age', id: 'f-edad-max', etiqueta: 'Edad máxima', seccion: 'paso 4', paso: 4 },
];

/** Atajos de día: hoy, mañana y el próximo fin de semana (en hora local). */
function calcularDias(): { texto: string; valor: string }[] {
  const ymd = (d: Date) =>
    d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  const hoy = new Date();
  const mas = (n: number) => new Date(hoy.getFullYear(), hoy.getMonth(), hoy.getDate() + n);
  const hastaSabado = (6 - hoy.getDay() + 7) % 7 || 7;
  const lista = [
    { texto: 'Hoy', valor: ymd(hoy) },
    { texto: 'Mañana', valor: ymd(mas(1)) },
    { texto: 'Este sábado', valor: ymd(mas(hastaSabado)) },
    { texto: 'Este domingo', valor: ymd(mas(hastaSabado + 1)) },
  ];
  // Sin repetidos (p. ej. si mañana ya es sábado)
  return lista.filter((o, i) => lista.findIndex((x) => x.valor === o.valor) === i);
}
