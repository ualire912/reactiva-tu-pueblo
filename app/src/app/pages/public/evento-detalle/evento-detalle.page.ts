import { Component, OnInit, OnDestroy, inject, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterModule } from '@angular/router';
import {
  IonHeader,
  IonToolbar,
  IonButtons,
  IonBackButton,
  IonTitle,
  IonContent,
  IonCard,
  IonCardHeader,
  IonCardTitle,
  IonCardSubtitle,
  IonCardContent,
  IonBadge,
  IonButton,
  IonIcon,
  IonList,
  IonItem,
  IonLabel,
  IonSelect,
  IonSelectOption,
  IonInput,
  IonTextarea,
  IonSpinner,
  IonNote,
  ToastController,
  AlertController
} from '@ionic/angular';
import { addIcons } from 'ionicons';
import {
  calendarOutline,
  carOutline,
  chatbubblesOutline,
  sparklesOutline,
  starOutline,
  warningOutline,
  volumeHighOutline,
  sendOutline,
  checkmarkCircleOutline
} from 'ionicons/icons';
import { SupabaseService } from '../../../core/supabase.service';
import { ApiService } from '../../../core/api.service';
import { Evento, Inscripcion, Mensaje, Resena, Incidencia, TipoTransporte } from '../../../core/models';

@Component({
  selector: 'app-evento-detalle',
  templateUrl: './evento-detalle.page.html',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterModule,
    IonHeader,
    IonToolbar,
    IonButtons,
    IonBackButton,
    IonTitle,
    IonContent,
    IonCard,
    IonCardHeader,
    IonCardTitle,
    IonCardSubtitle,
    IonCardContent,
    IonBadge,
    IonButton,
    IonIcon,
    IonList,
    IonItem,
    IonLabel,
    IonSelect,
    IonSelectOption,
    IonInput,
    IonTextarea,
    IonSpinner,
    IonNote
  ]
})
export class EventoDetallePage implements OnInit, OnDestroy {
  private route = inject(ActivatedRoute);
  public sb = inject(SupabaseService);
  private api = inject(ApiService);
  private toastCtrl = inject(ToastController);
  private alertCtrl = inject(AlertController);
  private cdr = inject(ChangeDetectorRef);

  eventId = '';
  evento: Evento | null = null;
  cargando = true;

  // Inscripción
  miInscripcion: Inscripcion | null = null;
  transporte: TipoTransporte = 'ninguno';
  transportePlazas = 2;
  transporteMunicipioId: number | null = null;
  procesandoInscripcion = false;

  // Chat en tiempo real
  mensajes: Mensaje[] = [];
  nuevoMensaje = '';
  preguntarIa = false;
  enviandoMensaje = false;
  private chatChannel: any;
  private eventoChannel: any;

  // Reseñas e Incidencias (si es publicador organizador)
  resenas: Resena[] = [];
  estrellas = 5;
  comentarioResena = '';
  incidenciasEvento: Incidencia[] = [];

  constructor() {
    addIcons({
      calendarOutline,
      carOutline,
      chatbubblesOutline,
      sparklesOutline,
      starOutline,
      warningOutline,
      volumeHighOutline,
      sendOutline,
      checkmarkCircleOutline
    });
  }

  async ngOnInit() {
    this.eventId = this.route.snapshot.paramMap.get('id') || '';
    if (!this.eventId) return;

    await this.cargarFichaCompleta();

    this.chatChannel = this.sb.client
      .channel(`chat-${this.eventId}`)
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'mensajes_evento', filter: `event_id=eq.${this.eventId}` },
        async () => {
          await this.cargarMensajes();
          this.cdr.detectChanges();
        }
      )
      .subscribe();

    this.eventoChannel = this.sb.client
      .channel(`evento-${this.eventId}`)
      .on(
        'postgres_changes',
        { event: 'UPDATE', schema: 'public', table: 'eventos', filter: `id=eq.${this.eventId}` },
        async () => {
          await this.cargarEvento();
          this.cdr.detectChanges();
        }
      )
      .subscribe();
  }

  ngOnDestroy() {
    if (this.chatChannel) this.sb.client.removeChannel(this.chatChannel);
    if (this.eventoChannel) this.sb.client.removeChannel(this.eventoChannel);
  }

  async cargarFichaCompleta() {
    this.cargando = !this.evento;
    this.cdr.detectChanges();

    try {
      await Promise.all([
        this.cargarEvento(),
        this.cargarMiInscripcion(),
        this.cargarMensajes(),
        this.cargarResenas()
      ]);
      await this.cargarIncidenciasSiOrganizador();
    } finally {
      this.cargando = false;
      this.cdr.detectChanges();
    }
  }

  get puedeValorar(): boolean {
    return (
      this.evento?.status === 'finalizado' &&
      !!this.miInscripcion &&
      this.miInscripcion.asistio === true
    );
  }

  async cargarEvento() {
    const { data } = await this.sb.client
      .from('eventos')
      .select('*, municipios(nombre)')
      .eq('id', this.eventId)
      .single();

    if (data) {
      this.evento = data as Evento;
      if (!this.transporteMunicipioId) {
        this.transporteMunicipioId = this.sb.currentProfile?.municipio_id || this.evento.municipio_id;
      }
      this.cdr.detectChanges();
    }
  }

  async cargarMiInscripcion() {
    const uid = this.sb.currentSession?.user?.id;
    if (!uid) {
      this.miInscripcion = null;
      this.cdr.detectChanges();
      return;
    }
    const { data } = await this.sb.client
      .from('inscripciones')
      .select('*')
      .eq('event_id', this.eventId)
      .eq('user_id', uid)
      .neq('status', 'cancelada')
      .maybeSingle();

    this.miInscripcion = (data as Inscripcion) || null;
    this.cdr.detectChanges();
  }

  async cargarMensajes() {
    const { data } = await this.sb.client
      .from('mensajes_evento')
      .select('*')
      .eq('event_id', this.eventId)
      .order('created_at', { ascending: true });

    if (data) {
      this.mensajes = data as Mensaje[];
      this.cdr.detectChanges();
    }
  }

  async cargarResenas() {
    const { data } = await this.sb.client
      .from('resenas')
      .select('*')
      .eq('event_id', this.eventId);

    if (data) {
      this.resenas = data as Resena[];
      this.cdr.detectChanges();
    }
  }

  get esPublicadorOrganizador(): boolean {
    const p = this.sb.currentProfile;
    return !!p && p.rol === 'publicador' && !!this.evento && this.evento.created_by === p.id;
  }

  async cargarIncidenciasSiOrganizador() {
    if (!this.esPublicadorOrganizador) return;
    const { data } = await this.sb.client
      .from('incidencias')
      .select('*')
      .eq('event_id', this.eventId)
      .order('estado', { ascending: true });

    if (data) {
      this.incidenciasEvento = data as Incidencia[];
      this.cdr.detectChanges();
    }
  }

  async resolverIncidenciaPropia(inc: Incidencia) {
    const { error } = await this.sb.client
      .from('incidencias')
      .update({ estado: 'resuelta' })
      .eq('id', inc.id);

    if (!error) {
      this.mostrarAviso('Incidencia marcada como resuelta');
      await this.cargarIncidenciasSiOrganizador();
    }
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

  get puedeApuntarse(): boolean {
    return this.evento?.status === 'abierto' || this.evento?.status === 'completo';
  }

  get textoBotonApuntarse(): string {
    if (!this.evento) return 'Apuntarme';
    if (this.evento.status === 'abierto') return 'Apuntarme';
    if (this.evento.status === 'completo') return 'Apuntarme a la lista de espera';
    return this.etiquetaEstado(this.evento.status);
  }

  async apuntarme() {
    const uid = this.sb.currentSession?.user?.id;
    if (!uid || !this.evento) return;

    this.procesandoInscripcion = true;
    this.cdr.detectChanges();
    try {
      const { data, error } = await this.sb.client
        .from('inscripciones')
        .insert({
          user_id: uid,
          event_id: this.evento.id,
          transporte: this.transporte,
          transporte_plazas: this.transporte === 'ofrece' ? Number(this.transportePlazas) : 0,
          transporte_desde_municipio_id: this.transporte !== 'ninguno' ? Number(this.transporteMunicipioId) : null
        })
        .select()
        .single();

      if (error) throw error;

      this.miInscripcion = data as Inscripcion;
      this.mostrarAviso(
        data.status === 'lista_de_espera'
          ? 'Te hemos apuntado a la lista de espera'
          : '¡Inscripción confirmada!'
      );
      await this.cargarEvento();
    } catch (err: any) {
      this.mostrarAviso(err.message || 'No se pudo completar la inscripción');
    } finally {
      this.procesandoInscripcion = false;
      this.cdr.detectChanges();
    }
  }

  async cancelarInscripcion() {
    if (!this.miInscripcion) return;
    this.procesandoInscripcion = true;
    this.cdr.detectChanges();
    try {
      const { error } = await this.sb.client
        .from('inscripciones')
        .update({ status: 'cancelada' })
        .eq('id', this.miInscripcion.id);

      if (error) throw error;

      this.miInscripcion = null;
      this.mostrarAviso('Inscripción cancelada');
      await this.cargarEvento();
    } catch (err: any) {
      this.mostrarAviso(err.message || 'Error al cancelar');
    } finally {
      this.procesandoInscripcion = false;
      this.cdr.detectChanges();
    }
  }

  descargarIcs() {
    if (!this.evento) return;
    window.open(this.api.getIcsUrl(this.evento.id), '_blank');
  }

  abrirGoogleCalendar() {
    if (!this.evento) return;
    const inicio = new Date(this.evento.starts_at).toISOString().replace(/[-:]/g, '').split('.')[0] + 'Z';
    const finDate = this.evento.ends_at
      ? new Date(this.evento.ends_at)
      : new Date(new Date(this.evento.starts_at).getTime() + 7200000);
    const fin = finDate.toISOString().replace(/[-:]/g, '').split('.')[0] + 'Z';
    const url = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${encodeURIComponent(this.evento.title)}&dates=${inicio}/${fin}&location=${encodeURIComponent(this.evento.location_name || '')}&details=${encodeURIComponent(this.evento.description || '')}`;
    window.open(url, '_blank');
  }

  async enviarMensaje() {
    const uid = this.sb.currentSession?.user?.id;
    const texto = this.nuevoMensaje.trim();
    if (!uid || !texto || !this.evento) return;

    this.enviandoMensaje = true;
    this.cdr.detectChanges();
    try {
      await this.sb.client.from('mensajes_evento').insert({
        event_id: this.evento.id,
        user_id: uid,
        tipo: 'pregunta',
        contenido: texto
      });

      this.nuevoMensaje = '';
      this.cdr.detectChanges();

      if (this.preguntarIa) {
        await this.api.chatIa(this.evento.id, texto);
      }

      await this.cargarMensajes();
    } catch {
      this.mostrarAviso('Error al enviar el mensaje');
    } finally {
      this.enviandoMensaje = false;
      this.cdr.detectChanges();
    }
  }

  async enviarResena() {
    const uid = this.sb.currentSession?.user?.id;
    if (!uid || !this.evento) return;

    const { error } = await this.sb.client.from('resenas').insert({
      event_id: this.evento.id,
      user_id: uid,
      estrellas: Number(this.estrellas),
      comentario: this.comentarioResena.trim() || null
    });

    if (error) {
      this.mostrarAviso(error.message || 'Solo puedes reseñar eventos terminados a los que asististe');
      return;
    }

    this.comentarioResena = '';
    this.mostrarAviso('¡Gracias por tu valoración!');
    await this.cargarResenas();
  }

  async reportarIncidencia() {
    const uid = this.sb.currentSession?.user?.id;
    if (!uid || !this.evento) return;

    const alert = await this.alertCtrl.create({
      header: 'Reportar incidencia',
      message: 'Cuéntanos qué ocurre con este evento:',
      inputs: [{ name: 'comentario', type: 'textarea', placeholder: 'Ej. El lugar está cerrado o los datos son erróneos' }],
      buttons: [
        { text: 'Cancelar', role: 'cancel' },
        {
          text: 'Enviar reporte',
          handler: async (vals) => {
            if (!vals.comentario?.trim()) return;
            const { error } = await this.sb.client.from('incidencias').insert({
              event_id: this.evento!.id,
              user_id: uid,
              comentario: vals.comentario.trim()
            });
            if (!error) {
              this.mostrarAviso('Incidencia enviada al equipo moderador');
            }
          }
        }
      ]
    });
    await alert.present();
  }

  formatearFecha(iso: string): string {
    return new Intl.DateTimeFormat('es-ES', {
      timeZone: 'Europe/Madrid',
      weekday: 'long',
      day: 'numeric',
      month: 'long',
      hour: '2-digit',
      minute: '2-digit'
    }).format(new Date(iso));
  }

  private async mostrarAviso(message: string) {
    const toast = await this.toastCtrl.create({ message, duration: 3000 });
    await toast.present();
  }
}