import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterModule } from '@angular/router';
import {
  IonHeader,
  IonToolbar,
  IonButtons,
  IonBackButton,
  IonTitle,
  IonContent,
  IonButton,
  IonIcon,
  IonSpinner,
  AlertController,
  IonModal,
} from '@ionic/angular';
import { addIcons } from 'ionicons';
import {
  calendar,
  car,
  chatbubbles,
  sparkles,
  star,
  starOutline,
  warningOutline,
  volumeHigh,
  send,
  checkmarkCircle,
  location,
  time,
  cash,
  people,
  person,
  accessibility,
  hourglass,
  navigate,
  camera,
  images,
  logoGoogle,
  trash,
  expandOutline,
  walkOutline,
  bicycleOutline,
  colorPaletteOutline,
  restaurantOutline,
  musicalNotesOutline,
  constructOutline,
  heartOutline,
  sparklesOutline,
  createOutline,
} from 'ionicons/icons';
import { SupabaseService } from '../../../core/supabase.service';
import { ApiService } from '../../../core/api.service';
import { AvisosService } from '../../../core/avisos.service';
import { EventoVista, EventosService } from '../../../core/eventos.service';
import { Evento, Incidencia, Inscripcion, Mensaje, Resena, TipoTransporte } from '../../../core/models';
import {
  colorEstado,
  etiquetaEstado,
  fechaCorta,
  fechaLarga,
  estiloCategoria,
  haTerminado,
  esOfensivo,
  iconoCategoria,
  iconoCategoriaLinea,
  partesFecha,
  porcentajeOcupacion,
  textoPlazas,
  textoPrecio,
  textoValoracion,
  ubicacionCompleta,
} from '../../../core/ui';

const TAMANO_MAX_FOTO = 5 * 1024 * 1024; // 5 MB

@Component({
  selector: 'app-evento-detalle',
  templateUrl: './evento-detalle.page.html',
  standalone: true,
  imports: [
    FormsModule,
    RouterModule,
    IonHeader,
    IonToolbar,
    IonButtons,
    IonBackButton,
    IonTitle,
    IonContent,
    IonButton,
    IonIcon,
    IonSpinner,
    IonModal
  ],
})
export class EventoDetallePage implements OnInit, OnDestroy {
  private route = inject(ActivatedRoute);
  sb = inject(SupabaseService);
  private api = inject(ApiService);
  private eventosSvc = inject(EventosService);
  private avisos = inject(AvisosService);
  private alertCtrl = inject(AlertController);

  // Helpers para la plantilla
  fechaLarga = fechaLarga;
  fechaCorta = fechaCorta;
  etiquetaEstado = etiquetaEstado;
  colorEstado = colorEstado;

  eventId = '';
  evento = signal<EventoVista | null>(null);
  cargando = signal(true);
  uid = signal<string | null>(null);
  private perfil = toSignal(this.sb.profile$, { initialValue: null });

  // Inscripción
  miInscripcion = signal<Inscripcion | null>(null);
  transporte: TipoTransporte = 'ninguno';
  transportePlazas = 2;
  transporteMunicipioId: number | null = null;
  procesando = signal(false);

  // Chat
  mensajes = signal<Mensaje[]>([]);
  nombres = signal<Record<string, string>>({});
  nuevoMensaje = '';
  enviando = signal(false);
  esperandoIa = signal(false);

  // Reseñas y fotos
  resenas = signal<Resena[]>([]);
  estrellas = signal(5);
  comentarioResena = '';
  fotoSeleccionada = signal<File | null>(null);
  fotoPrevia = signal<string | null>(null);
  enviandoResena = signal(false);

  // Organizador
  propietarioEntidad = signal<string | null>(null);
  incidenciasEvento = signal<Incidencia[]>([]);

  private chatChannel: any;
  private eventoChannel: any;

  // ---------- Derivados ----------
  terminado = computed(() => {
    const ev = this.evento();
    return !!ev && ev.status !== 'cancelado' && haTerminado(ev);
  });
  precio = computed(() => textoPrecio(this.evento()?.price));
  gratis = computed(() => !Number(this.evento()?.price));
  ubicacion = computed(() => ubicacionCompleta(this.evento()?.location_name, this.evento()?.pueblo));
  plazas = computed(() => (this.evento() ? textoPlazas(this.evento()!) : ''));
  ocupacion = computed(() => (this.evento() ? porcentajeOcupacion(this.evento()!) : 0));
  valoracion = computed(() => textoValoracion(this.evento()?.organizador_media));
  icono = computed(() => iconoCategoria(this.evento()?.category));
  iconoLinea = computed(() => iconoCategoriaLinea(this.evento()?.category));
  estilo = computed(() => estiloCategoria(this.evento()?.category));
  fechaPartes = computed(() => partesFecha(this.evento()?.starts_at));
  /** Cartel ampliado a pantalla completa. */
  fotoAbierta = signal(false);
  categoriaNombre = computed(() => {
    this.sb.catalogos();
    return this.sb.categorias.find((c) => c.slug === this.evento()?.category)?.nombre || 'Actividad';
  });
  edades = computed(() => {
    const ev = this.evento();
    if (!ev || (ev.min_age == null && ev.max_age == null)) return 'Todas las edades';
    if (ev.min_age != null && ev.max_age != null) return `De ${ev.min_age} a ${ev.max_age} años`;
    if (ev.min_age != null) return `Desde ${ev.min_age} años`;
    return `Hasta ${ev.max_age} años`;
  });
  enlaceMapa = computed(() => {
    const ev = this.evento();
    if (!ev) return '';
    if (ev.lat != null && ev.lng != null) return `https://www.google.com/maps/search/?api=1&query=${ev.lat},${ev.lng}`;
    return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(this.ubicacion() + ', Almería')}`;
  });
  enlaceIcs = computed(() => (this.evento() ? this.api.getIcsUrl(this.evento()!.id) : ''));

  /** Ya empezó (o terminó): no se admiten nuevas inscripciones ni cancelaciones. */
  yaEmpezo = computed(() => {
    const ev = this.evento();
    return !!ev && new Date(ev.starts_at).getTime() <= Date.now();
  });
  /** Pasó la fecha límite para apuntarse (aunque el estado aún no se haya recalculado). */
  plazoCerrado = computed(() => {
    const limite = this.evento()?.inscripcion_hasta;
    return !!limite && new Date(limite).getTime() <= Date.now();
  });
  puedeApuntarse = computed(() => {
    const s = this.evento()?.status;
    if (this.yaEmpezo() || this.plazoCerrado()) return false;
    return s === 'abierto' || s === 'completo';
  });
  textoBotonApuntarse = computed(() => {
    const s = this.evento()?.status;
    if (this.terminado()) return 'Plan finalizado';
    if (this.yaEmpezo()) return 'En curso';
    if (this.plazoCerrado()) return 'Inscripción cerrada';
    if (s === 'abierto') return 'Apuntarme';
    if (s === 'completo') return 'Apuntarme a la lista de espera';
    return 'Inscripción no disponible';
  });
  motivoNoApuntarse = computed(() => {
    const ev = this.evento();
    if (!ev || this.puedeApuntarse()) return '';
    if (this.terminado()) return 'Este plan ya se ha celebrado.';
    if (this.yaEmpezo()) return 'Este plan ya ha empezado.';
    if (this.plazoCerrado()) return 'El plazo para apuntarse ya ha terminado.';
    const motivos: Record<string, string> = {
      inscripcion_cerrada: 'El plazo para apuntarse ya ha terminado.',
      en_curso: 'Este plan ya ha empezado.',
      finalizado: 'Este plan ya se ha celebrado.',
      pospuesto: 'El plan está aplazado. Te avisaremos cuando tenga nueva fecha.',
      cancelado: 'Este plan se ha cancelado.',
      pendiente_moderacion: 'El plan está pendiente de revisión.',
      rechazado: 'Este plan no se ha publicado.',
    };
    return motivos[ev.status] || 'Ahora mismo no se admiten inscripciones.';
  });

  fotos = computed(() => this.resenas().filter((r) => !!r.foto_url));
  miResena = computed(() => this.resenas().find((r) => r.user_id === this.uid()) || null);
  puedeValorar = computed(
    () => this.terminado() && this.miInscripcion()?.status === 'confirmada' && !this.miResena()
  );
  /** Cambiando una valoración que ya hizo (estrellas y comentario). */
  editandoResena = signal(false);
  mediaEvento = computed(() => {
    const r = this.resenas();
    if (!r.length) return null;
    return textoValoracion(r.reduce((a, x) => a + x.estrellas, 0) / r.length);
  });

  esOrganizador = computed(() => {
    const p = this.perfil();
    const ev = this.evento();
    if (!p || !ev) return false;
    if (p.rol === 'moderador') return true;
    return p.rol === 'publicador' && (ev.created_by === p.id || this.propietarioEntidad() === p.id);
  });

  constructor() {
    addIcons({
      createOutline,
      expandOutline,
      walkOutline,
      bicycleOutline,
      colorPaletteOutline,
      restaurantOutline,
      musicalNotesOutline,
      constructOutline,
      heartOutline,
      sparklesOutline,
      calendar,
      car,
      chatbubbles,
      sparkles,
      star,
      starOutline,
      warningOutline,
      volumeHigh,
      send,
      checkmarkCircle,
      location,
      time,
      cash,
      people,
      person,
      accessibility,
      hourglass,
      navigate,
      camera,
      images,
      logoGoogle,
      trash,
    });
  }

  async ngOnInit() {
    this.eventId = this.route.snapshot.paramMap.get('id') || '';
    if (!this.eventId) return;
    const { data: sesion } = await this.sb.client.auth.getSession();
    this.uid.set(sesion.session?.user?.id ?? null);
    if (sesion.session?.user && !this.sb.currentProfile) await this.sb.loadProfile(sesion.session.user.id);

    await this.cargarFichaCompleta();

    this.chatChannel = this.sb.client
      .channel(`chat-${this.eventId}`)
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'mensajes_evento', filter: `event_id=eq.${this.eventId}` },
        async () => {
          await this.cargarMensajes();
          this.esperandoIa.set(false);
        }
      )
      .subscribe();

    this.eventoChannel = this.sb.client
      .channel(`evento-${this.eventId}`)
      .on(
        'postgres_changes',
        { event: 'UPDATE', schema: 'public', table: 'eventos', filter: `id=eq.${this.eventId}` },
        () => this.cargarEvento()
      )
      .subscribe();
  }

  ngOnDestroy() {
    if (this.chatChannel) this.sb.client.removeChannel(this.chatChannel);
    if (this.eventoChannel) this.sb.client.removeChannel(this.eventoChannel);
    if (this.fotoPrevia()) URL.revokeObjectURL(this.fotoPrevia()!);
  }

  async cargarFichaCompleta() {
    this.cargando.set(!this.evento());
    try {
      await Promise.all([this.cargarEvento(), this.cargarMiInscripcion(), this.cargarMensajes(), this.cargarResenas()]);
      await this.cargarIncidenciasSiOrganizador();
    } finally {
      this.cargando.set(false);
    }
  }

  async cargarEvento() {
    const { data } = await this.sb.client.from('eventos').select('*, municipios(nombre)').eq('id', this.eventId).maybeSingle();
    if (!data) return;

    // La media del organizador se calcula con todos sus eventos visibles
    let referencia: Evento[] = [data as Evento];
    try {
      referencia = await this.eventosSvc.cargarVisibles();
    } catch {
      /* sin media si falla */
    }
    const [vista] = await this.eventosSvc.enriquecer([data as Evento], referencia);
    this.evento.set(vista);

    if (!this.transporteMunicipioId) {
      this.transporteMunicipioId = this.sb.currentProfile?.municipio_id || vista.municipio_id;
    }
    if (vista.entidad_organizadora_id) {
      const { data: ent } = await this.sb.client
        .from('entidades')
        .select('owner_user_id')
        .eq('id', vista.entidad_organizadora_id)
        .maybeSingle();
      this.propietarioEntidad.set(ent?.owner_user_id ?? null);
    }
  }

  async cargarMiInscripcion() {
    const uid = this.uid();
    if (!uid) {
      this.miInscripcion.set(null);
      return;
    }
    const { data } = await this.sb.client
      .from('inscripciones')
      .select('*')
      .eq('event_id', this.eventId)
      .eq('user_id', uid)
      .neq('status', 'cancelada')
      .maybeSingle();
    this.miInscripcion.set((data as Inscripcion) || null);
  }

  async cargarMensajes() {
    const { data } = await this.sb.client
      .from('mensajes_evento')
      .select('*')
      .eq('event_id', this.eventId)
      .order('created_at', { ascending: true });
    if (!data) return;
    this.mensajes.set(data as Mensaje[]);
    await this.cargarNombres((data as Mensaje[]).map((m) => m.user_id));
  }

  async cargarResenas() {
    const { data } = await this.sb.client
      .from('resenas')
      .select('*')
      .eq('event_id', this.eventId)
      .order('estrellas', { ascending: false });
    if (!data) return;
    this.resenas.set(data as Resena[]);
    await this.cargarNombres((data as Resena[]).map((r) => r.user_id));
  }

  /** Nombres públicos de autores (chat y reseñas) desde perfiles_publicos. */
  private async cargarNombres(ids: (string | null)[]) {
    const conocidos = this.nombres();
    const faltan = [...new Set(ids.filter((x): x is string => !!x && !(x in conocidos)))];
    if (!faltan.length) return;
    const { data } = await this.sb.client.from('perfiles_publicos').select('id, full_name').in('id', faltan);
    const nuevos = { ...this.nombres() };
    for (const p of (data || []) as { id: string; full_name: string | null }[]) {
      nuevos[p.id] = p.full_name || 'Vecino/a';
    }
    this.nombres.set(nuevos);
  }

  nombreDe(userId: string | null): string {
    if (!userId) return 'Asistente';
    if (userId === this.uid()) return 'Tú';
    return this.nombres()[userId] || 'Vecino/a';
  }

  inicial(userId: string | null): string {
    return this.nombreDe(userId).charAt(0).toUpperCase();
  }

  // ---------- Incidencias del organizador ----------
  async cargarIncidenciasSiOrganizador() {
    if (!this.esOrganizador()) return;
    const { data } = await this.sb.client
      .from('incidencias')
      .select('*')
      .eq('event_id', this.eventId)
      .order('estado', { ascending: true });
    if (data) this.incidenciasEvento.set(data as Incidencia[]);
  }

  async resolverIncidenciaPropia(inc: Incidencia) {
    const { error } = await this.sb.client.from('incidencias').update({ estado: 'resuelta' }).eq('id', inc.id);
    if (error) {
      this.avisos.error(error);
      return;
    }
    this.avisos.ok('Incidencia marcada como resuelta');
    await this.cargarIncidenciasSiOrganizador();
  }

  // ---------- Inscripción ----------
  async apuntarme() {
    const uid = this.uid();
    const ev = this.evento();
    if (!uid || !ev) return;
    if (!this.puedeApuntarse()) {
      this.avisos.aviso(this.motivoNoApuntarse() || 'Este plan ya no admite inscripciones.');
      return;
    }

    this.procesando.set(true);
    try {
      const { data, error } = await this.sb.client
        .from('inscripciones')
        .insert({
          user_id: uid,
          event_id: ev.id,
          transporte: this.transporte,
          transporte_plazas: this.transporte === 'ofrece' ? Math.min(8, Math.max(1, Number(this.transportePlazas) || 1)) : 0,
          transporte_desde_municipio_id: this.transporte !== 'ninguno' ? Number(this.transporteMunicipioId) : null,
        })
        .select()
        .single();
      if (error) throw error;

      this.miInscripcion.set(data as Inscripcion);
      if (data.status === 'lista_de_espera') {
        this.avisos.info('Estás en la lista de espera. Te avisaremos si queda una plaza libre.');
      } else {
        this.avisos.ok('¡Ya estás apuntado! Te esperamos.');
      }
      await this.cargarEvento();
    } catch (err) {
      this.avisos.error(err, 'No hemos podido apuntarte. Inténtalo de nuevo.');
    } finally {
      this.procesando.set(false);
    }
  }

  async cancelarInscripcion() {
    const insc = this.miInscripcion();
    if (!insc) return;
    const alert = await this.alertCtrl.create({
      header: '¿Cancelar tu inscripción?',
      message: 'Dejarás tu plaza libre para otra persona.',
      buttons: [
        { text: 'No, mantener', role: 'cancel' },
        { text: 'Sí, cancelar', role: 'destructive', handler: () => this.confirmarCancelacion(insc) },
      ],
    });
    await alert.present();
  }

  private async confirmarCancelacion(insc: Inscripcion) {
    this.procesando.set(true);
    try {
      const { error } = await this.sb.client.from('inscripciones').update({ status: 'cancelada' }).eq('id', insc.id);
      if (error) throw error;
      this.miInscripcion.set(null);
      this.avisos.ok('Has cancelado tu inscripción.');
      await this.cargarEvento();
    } catch (err) {
      this.avisos.error(err, 'No hemos podido cancelar la inscripción.');
    } finally {
      this.procesando.set(false);
    }
  }

  abrirGoogleCalendar() {
    const ev = this.evento();
    if (!ev) return;
    const f = (d: Date) => d.toISOString().replace(/[-:]/g, '').split('.')[0] + 'Z';
    const inicio = new Date(ev.starts_at);
    const fin = ev.ends_at ? new Date(ev.ends_at) : new Date(inicio.getTime() + 7200000);
    const url =
      'https://calendar.google.com/calendar/render?action=TEMPLATE' +
      `&text=${encodeURIComponent(ev.title)}&dates=${f(inicio)}/${f(fin)}` +
      `&location=${encodeURIComponent(this.ubicacion())}&details=${encodeURIComponent(ev.description || '')}`;
    window.open(url, '_blank', 'noopener');
  }

  leerEnVozAlta() {
    const ev = this.evento();
    if (!ev || !('speechSynthesis' in window)) return;
    window.speechSynthesis.cancel();
    const texto = `${ev.title}. ${fechaLarga(ev.starts_at)}. En ${this.ubicacion()}. ${
      this.gratis() ? 'Es gratis.' : 'Precio: ' + this.precio() + '.'
    } Organiza ${ev.organizador_nombre}. ${ev.description || ''}`;
    const u = new SpeechSynthesisUtterance(texto);
    u.lang = 'es-ES';
    u.rate = 0.95;
    window.speechSynthesis.speak(u);
  }

  // ---------- Chat ----------
  async enviarMensaje(preguntarIa: boolean) {
    const uid = this.uid();
    const ev = this.evento();
    const texto = this.nuevoMensaje.trim();
    // Evita enviar dos veces (Enter y botón a la vez, o doble clic)
    if (!uid || !ev || !texto || this.enviando()) return;

    // El chat es público: los insultos no se publican
    if (esOfensivo(texto)) {
      this.avisos.aviso('Este chat es público y lo leen vecinos de todas las edades. Escribe tu mensaje sin insultos ni palabrotas, por favor.');
      return;
    }

    this.enviando.set(true);
    this.nuevoMensaje = ''; // se vacía ya, para que no se pueda mandar otra vez
    try {
      const { error } = await this.sb.client
        .from('mensajes_evento')
        .insert({ event_id: ev.id, user_id: uid, tipo: 'pregunta', contenido: texto.slice(0, 2000) });
      if (error) {
        this.nuevoMensaje = texto; // no se ha enviado: se devuelve al campo
        throw error;
      }
      await this.cargarMensajes();

      if (preguntarIa) {
        this.esperandoIa.set(true);
        try {
          const res = await this.api.chatIa(ev.id, texto);
          if (res?.bloqueado) this.avisos.aviso(res.respuesta);
          await this.cargarMensajes();
        } catch {
          this.avisos.aviso('El asistente no responde ahora mismo. Tu pregunta queda en el chat para los vecinos.');
        } finally {
          this.esperandoIa.set(false);
        }
      }
    } catch (err) {
      this.avisos.error(err, 'No hemos podido enviar tu mensaje.');
    } finally {
      this.enviando.set(false);
    }
  }

  // ---------- Reseñas y fotos post evento ----------
  elegirEstrellas(n: number) {
    this.estrellas.set(n);
  }

  seleccionarFoto(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      this.avisos.aviso('El archivo debe ser una foto.');
      return;
    }
    if (file.size > TAMANO_MAX_FOTO) {
      this.avisos.aviso('La foto es demasiado grande (máximo 5 MB).');
      return;
    }
    if (this.fotoPrevia()) URL.revokeObjectURL(this.fotoPrevia()!);
    this.fotoSeleccionada.set(file);
    this.fotoPrevia.set(URL.createObjectURL(file));
  }

  quitarFoto() {
    if (this.fotoPrevia()) URL.revokeObjectURL(this.fotoPrevia()!);
    this.fotoSeleccionada.set(null);
    this.fotoPrevia.set(null);
  }

  private async subirFoto(file: File): Promise<string> {
    const ext = (file.name.split('.').pop() || 'jpg').toLowerCase();
    const ruta = `${this.eventId}/${this.uid()}-${Date.now()}.${ext}`;
    const { error } = await this.sb.client.storage.from('resenas').upload(ruta, file, { contentType: file.type });
    if (error) throw error;
    return this.sb.client.storage.from('resenas').getPublicUrl(ruta).data.publicUrl;
  }

  /** Abre el formulario con la valoración actual para cambiarla. */
  empezarCambioResena() {
    const r = this.miResena();
    if (!r) return;
    this.estrellas.set(r.estrellas);
    this.comentarioResena = r.comentario || '';
    this.editandoResena.set(true);
  }

  cancelarCambioResena() {
    this.editandoResena.set(false);
    this.comentarioResena = '';
    this.quitarFoto();
  }

  async enviarResena() {
    const uid = this.uid();
    const ev = this.evento();
    if (!uid || !ev) return;

    const mia = this.miResena();
    if (mia) {
      // Ya la había valorado: se cambia por el servidor (la app no puede editar reseñas directamente)
      if (!this.editandoResena()) return;
      this.enviandoResena.set(true);
      try {
        await this.api.cambiarResena(ev.id, Number(this.estrellas()), this.comentarioResena.trim());
        const foto = this.fotoSeleccionada();
        if (foto) {
          const url = await this.subirFoto(foto);
          const { error } = await this.sb.client.from('resenas').update({ foto_url: url }).eq('id', mia.id);
          if (error) throw error;
        }
        this.cancelarCambioResena();
        this.avisos.ok('Valoración cambiada. ¡Gracias!');
        await Promise.all([this.cargarResenas(), this.cargarEvento()]);
      } catch (err) {
        this.avisos.error(err, 'No hemos podido cambiar tu valoración. Inténtalo de nuevo.');
      } finally {
        this.enviandoResena.set(false);
      }
      return;
    }
    if (!this.puedeValorar()) return;

    this.enviandoResena.set(true);
    try {
      let fotoUrl: string | null = null;
      const foto = this.fotoSeleccionada();
      if (foto) fotoUrl = await this.subirFoto(foto);

      const { error } = await this.sb.client.from('resenas').insert({
        event_id: ev.id,
        user_id: uid,
        estrellas: this.estrellas(),
        comentario: this.comentarioResena.trim() || null,
        foto_url: fotoUrl,
      });
      if (error) throw error;

      this.comentarioResena = '';
      this.quitarFoto();
      this.avisos.ok('¡Gracias por tu opinión!');
      await Promise.all([this.cargarResenas(), this.cargarEvento()]);
    } catch (err) {
      this.avisos.error(err, 'No hemos podido guardar tu opinión. Solo pueden opinar las personas con plaza confirmada.');
    } finally {
      this.enviandoResena.set(false);
    }
  }

  /** Añadir o cambiar la foto de una opinión ya enviada. */
  async subirFotoAMiResena(event: Event) {
    const mia = this.miResena();
    this.seleccionarFoto(event);
    const foto = this.fotoSeleccionada();
    if (!mia || !foto) return;

    this.enviandoResena.set(true);
    try {
      const url = await this.subirFoto(foto);
      const { error } = await this.sb.client.from('resenas').update({ foto_url: url }).eq('id', mia.id);
      if (error) throw error;
      this.avisos.ok('¡Foto añadida! Gracias por compartirla.');
      await this.cargarResenas();
    } catch (err) {
      this.avisos.error(err, 'No hemos podido subir la foto. Inténtalo de nuevo.');
    } finally {
      this.quitarFoto();
      this.enviandoResena.set(false);
    }
  }

  estrellasTexto(n: number): string {
    return '★'.repeat(n) + '☆'.repeat(Math.max(0, 5 - n));
  }

  // ---------- Incidencias ----------
  async reportarIncidencia() {
    const uid = this.uid();
    const ev = this.evento();
    if (!uid || !ev) return;

    const alert = await this.alertCtrl.create({
      header: 'Avisar de un problema',
      message: 'Cuéntanos qué ocurre con este plan:',
      inputs: [{ name: 'comentario', type: 'textarea', placeholder: 'Ej. El lugar está cerrado o la hora no es correcta' }],
      buttons: [
        { text: 'Cancelar', role: 'cancel' },
        {
          text: 'Enviar aviso',
          handler: async (vals) => {
            const comentario = (vals?.comentario || '').trim();
            if (!comentario) {
              alert.message = 'Escribe qué ocurre con este plan para poder enviar el aviso.';
              alert.cssClass = 'alerta-con-error';
              return false;
            }
            const { error } = await this.sb.client.from('incidencias').insert({ event_id: ev.id, user_id: uid, comentario });
            if (error) this.avisos.error(error, 'No hemos podido enviar el aviso.');
            else this.avisos.ok('Gracias. El equipo lo revisará pronto.');
            return true;
          },
        },
      ],
    });
    await alert.present();
  }
}
