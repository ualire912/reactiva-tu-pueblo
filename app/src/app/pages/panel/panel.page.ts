import { Component, OnInit, inject, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { ToastController, AlertController } from '@ionic/angular';
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
  shieldCheckmarkOutline
} from 'ionicons/icons';
import { SupabaseService } from '../../core/supabase.service';
import { ApiService } from '../../core/api.service';
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
  IonSegment,
  IonSegmentButton,
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
  IonSpinner
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
    IonSegment,
    IonSegmentButton,
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
    IonSpinner
  ]
})
export class PanelPage implements OnInit {
  public sb = inject(SupabaseService);
  private api = inject(ApiService);
  private toastCtrl = inject(ToastController);
  private alertCtrl = inject(AlertController);
  private cdr = inject(ChangeDetectorRef);

  pestana: 'pendientes' | 'ia' | 'incidencias' | 'publicadores' | 'estadisticas' | 'demo' = 'pendientes';
  cargando = false;

  // 1. Pendientes y eventos activos
  pendientes: Evento[] = [];
  eventosActivos: Evento[] = [];

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
  demoResultadoJson = '';
  ejecutandoDemo = false;

  constructor() {
    addIcons({
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
      shieldCheckmarkOutline
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
        this.cargarPendientesYActivos(),
        this.cargarIngestas(),
        this.cargarIncidencias(),
        this.cargarPublicadores(),
        this.cargarEstadisticas()
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
      this.mostrarAviso(error.message);
      return;
    }

    try {
      await this.api.avisarEvento(ev.id, 'aprobado');
    } catch {}

    this.mostrarAviso('Evento aprobado y aviso enviado');
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

            if (!error) {
              try {
                await this.api.avisarEvento(ev.id, 'rechazado');
              } catch {}
              this.mostrarAviso('Evento rechazado');
              await this.cargarPendientesYActivos();
            }
          }
        }
      ]
    });
    await alert.present();
  }

  async editarEventoModal(ev: Evento, esYaPublicado = false) {
    const d = new Date(ev.starts_at);
    const offset = d.getTimezoneOffset() * 60000;
    const fechaLocal = !isNaN(d.getTime())
      ? new Date(d.getTime() - offset).toISOString().slice(0, 16)
      : '';

    const alert = await this.alertCtrl.create({
      header: esYaPublicado ? 'Modificar evento publicado' : 'Editar antes de aprobar',
      inputs: [
        { name: 'title', type: 'text', value: ev.title, placeholder: 'Título' },
        { name: 'starts_at', type: 'datetime-local', value: fechaLocal },
        { name: 'location_name', type: 'text', value: ev.location_name, placeholder: 'Lugar' },
        { name: 'price', type: 'number', value: String(ev.price), placeholder: 'Precio (€)' }
      ],
      buttons: [
        { text: 'Cancelar', role: 'cancel' },
        {
          text: 'Guardar cambios',
          handler: async (vals) => {
            const nuevaFechaIso = vals.starts_at ? new Date(vals.starts_at).toISOString() : ev.starts_at;
            const cambioHoraOLugar =
              nuevaFechaIso !== ev.starts_at || vals.location_name !== ev.location_name;

            const { error } = await this.sb.client
              .from('eventos')
              .update({
                title: vals.title?.trim() || ev.title,
                starts_at: nuevaFechaIso,
                location_name: vals.location_name?.trim() || ev.location_name,
                price: Number(vals.price) || 0
              })
              .eq('id', ev.id);

            if (!error) {
              if (esYaPublicado && cambioHoraOLugar) {
                try {
                  await this.api.avisarEvento(ev.id, 'cambio_hora');
                  this.mostrarAviso('Actualizado y avisados los inscritos con el nuevo .ics');
                } catch {
                  this.mostrarAviso('Evento actualizado');
                }
              } else {
                this.mostrarAviso('Cambios guardados');
              }
              await this.cargarPendientesYActivos();
            }
          }
        }
      ]
    });
    await alert.present();
  }

  async cambiarEstadoManual(ev: Evento, nuevoEstado: 'abierto' | 'pospuesto' | 'cancelado') {
    const { error } = await this.sb.client
      .from('eventos')
      .update({ status: nuevoEstado })
      .eq('id', ev.id);

    if (error) {
      this.mostrarAviso(`Error al actualizar: ${error.message}`);
      return;
    }

    if (nuevoEstado === 'pospuesto' || nuevoEstado === 'cancelado') {
      try {
        const res = await this.api.avisarEvento(ev.id, 'cancelacion');
        this.mostrarAviso(`Estado "${nuevoEstado}" · Avisados ${res?.enviados ?? 0} inscritos por Telegram`);
      } catch {
        this.mostrarAviso(`Estado "${nuevoEstado}" guardado, pero falló el aviso de Telegram (revisa apiUrl/Render)`);
      }
    } else {
      this.mostrarAviso(`Estado cambiado a "${nuevoEstado}"`);
    }

    await this.cargarPendientesYActivos();
  }

  // --- PESTAÑA 2: DETECTADOS POR IA (INGESTAS) ---
  async cargarIngestas() {
    const { data } = await this.sb.client
      .from('ingestas')
      .select('*')
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
      this.mostrarAviso('Esta entrada no tiene datos extraídos por IA');
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
      this.mostrarAviso(error.message);
      return;
    }

    await this.sb.client
      .from('ingestas')
      .update({ estado: 'confirmada', event_id: nuevoEv.id })
      .eq('id', ing.id);

    this.mostrarAviso('Evento creado desde la ingesta');
    await Promise.all([this.cargarIngestas(), this.cargarPendientesYActivos()]);
  }

  async descartarIngesta(ing: Ingesta) {
    await this.sb.client
      .from('ingestas')
      .update({ estado: 'descartada' })
      .eq('id', ing.id);

    this.mostrarAviso('Ingesta descartada');
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
      this.mostrarAviso('Incidencia resuelta y reputación recalculada');
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
      this.mostrarAviso(
        `IA revisó candidatos: ${res.promovidos?.length || 0} promovidos, ${res.a_revisar?.length || 0} a revisar`
      );
      await this.cargarPublicadores();
    } catch {
      this.mostrarAviso('No se pudo conectar con el servidor IA');
    } finally {
      this.revisandoIa = false;
      this.cdr.detectChanges();
    }
  }

  async decidirCandidato(userId: string, accion: 'promover' | 'descartar' | 'revocar') {
    try {
      await this.api.decidirPromocion(userId, accion);
      this.mostrarAviso(`Acción "${accion}" aplicada y avisada por Telegram`);
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
      this.mostrarAviso('Rol actualizado en BD (sin aviso de Telegram)');
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
      this.mostrarAviso(valor ? 'Aprobación automática activada' : 'Aprobación automática desactivada');
      this.cdr.detectChanges();
    }
  }

  // --- PESTAÑA 5: ESTADÍSTICAS ---
  async cargarEstadisticas() {
    const [statsRes, solapRes, notifRes] = await Promise.all([
      this.sb.client.from('v_eventos_stats').select('*'),
      this.sb.client.from('v_solapamientos').select('*'),
      this.sb.client.from('notificaciones').select('id', { count: 'exact', head: true })
    ]);

    if (statsRes.data) {
      this.statsEventos = statsRes.data;
      const totalInscritos = this.statsEventos.reduce((acc, s) => acc + (s.inscritos || 0), 0);
      const totalAsistieron = this.statsEventos.reduce((acc, s) => acc + (s.asistieron || 0), 0);
      this.resumenCifras.inscripciones = totalInscritos;
      this.resumenCifras.porcentajeAsistencia =
        totalInscritos > 0 ? Math.round((totalAsistieron / totalInscritos) * 100) : 0;
    }

    this.resumenCifras.abiertos = this.eventosActivos.length;
    this.resumenCifras.avisosEnviados = notifRes.count || 0;

    if (solapRes.data) this.solapamientos = solapRes.data;

    try {
      this.consumoIa = await this.api.getIaConsumo();
    } catch {
      this.consumoIa = null;
    }
    this.cdr.detectChanges();
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
    this.ejecutandoDemo = true;
    this.demoResultadoJson = 'Ejecutando...';
    this.cdr.detectChanges();

    try {
      let res: any;
      if (accion === 'facebook') {
        if (!this.demoText.trim()) {
          this.mostrarAviso('Escribe o carga un ejemplo de texto primero');
          return;
        }
        res = await this.api.ingestaFacebook({
          source_url: `demo-${Date.now()}`,
          page_url: this.demoPageUrl.trim() || 'https://facebook.com/demo',
          text: this.demoText.trim(),
          image_url: this.demoImageUrl.trim() || undefined
        });
        await this.cargarIngestas();
      } else if (accion === 'recomendaciones') {
        res = await this.api.enviarRecomendaciones();
      } else if (accion === 'resenas') {
        if (!this.demoEventIdSeleccionado) {
          this.mostrarAviso('Selecciona un evento primero');
          return;
        }
        res = await this.api.pedirResenas(this.demoEventIdSeleccionado);
      } else if (accion === 'promociones') {
        res = await this.api.revisarPromociones();
        await this.cargarPublicadores();
      } else if (accion === 'health') {
        res = await this.api.health();
      }

      this.demoResultadoJson = JSON.stringify(res, null, 2);
      this.mostrarAviso('Acción de demo completada');
    } catch (err: any) {
      this.demoResultadoJson = JSON.stringify({ error: err.message || 'No se pudo conectar con el servidor' }, null, 2);
    } finally {
      this.ejecutandoDemo = false;
      this.cdr.detectChanges();
    }
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

  private async mostrarAviso(message: string) {
    const toast = await this.toastCtrl.create({ message, duration: 2500 });
    await toast.present();
  }
}