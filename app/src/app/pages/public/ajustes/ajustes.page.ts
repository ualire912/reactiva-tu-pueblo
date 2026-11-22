import { ChangeDetectorRef, Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { IonHeader, IonToolbar, IonTitle, IonContent, IonIcon, IonToggle, IonButton } from '@ionic/angular';
import { addIcons } from 'ionicons';
import {
  accessibilityOutline,
  contrastOutline,
  eyeOutline,
  musicalNotesOutline,
  notificationsOutline,
  paperPlane,
  refreshOutline,
  textOutline,
  trashOutline,
  volumeHighOutline,
} from 'ionicons/icons';
import { AjustesService, Ajustes } from '../../../core/ajustes.service';
import { SupabaseService } from '../../../core/supabase.service';
import { AvisosService } from '../../../core/avisos.service';
import { AlertController } from '@ionic/angular';
import { Router } from '@angular/router';
import { ApiService } from '../../../core/api.service';

type TipoAviso = 'recordatorio' | 'recomendacion' | 'cambio' | 'resena';

@Component({
  selector: 'app-ajustes',
  templateUrl: './ajustes.page.html',
  standalone: true,
  imports: [FormsModule, RouterModule, IonHeader, IonToolbar, IonTitle, IonContent, IonIcon, IonToggle, IonButton],
})
export class AjustesPage implements OnInit {
  ajustesSvc = inject(AjustesService);
  sb = inject(SupabaseService);
  private avisos = inject(AvisosService);
  private alertCtrl = inject(AlertController);
  private cdr = inject(ChangeDetectorRef);
  private api = inject(ApiService);
  private router = inject(Router);
  borrando = false;

  a = this.ajustesSvc.ajustes;
  perfil = toSignal(this.sb.profile$, { initialValue: this.sb.currentProfile });
  voces = signal<SpeechSynthesisVoice[]>([]);
  hayVoz = 'speechSynthesis' in window;
  hayVibracion = 'vibrate' in navigator;

  readonly tamanos = [
    { valor: 100, texto: 'Normal' },
    { valor: 115, texto: 'Grande' },
    { valor: 130, texto: 'Muy grande' },
    { valor: 150, texto: 'Enorme' },
  ];
  readonly temas: { valor: Ajustes['tema']; texto: string }[] = [
    { valor: 'claro', texto: 'Claro' },
    { valor: 'oscuro', texto: 'Oscuro' },
    { valor: 'auto', texto: 'Automático' },
  ];
  readonly tiposAviso: { tipo: TipoAviso; texto: string; explica: string }[] = [
    { tipo: 'recordatorio', texto: 'Recordatorios', explica: 'El día antes de un plan al que te has apuntado.' },
    { tipo: 'cambio', texto: 'Cambios y cancelaciones', explica: 'Si un plan tuyo cambia de hora o lugar, o se cancela.' },
    { tipo: 'recomendacion', texto: 'Planes que te pueden gustar', explica: 'Como mucho, un mensaje al día.' },
    { tipo: 'resena', texto: 'Pedir tu opinión', explica: 'Al terminar un plan al que fuiste.' },
  ];

  // Notificaciones (se guardan en el perfil: las usa el bot de Telegram)
  notificacionesActivas = true;
  audio = false;
  avisosTipo: Record<TipoAviso, boolean> = { recordatorio: true, cambio: true, recomendacion: true, resena: true };
  guardandoAvisos = false;

  constructor() {
    addIcons({
      accessibilityOutline,
      contrastOutline,
      eyeOutline,
      musicalNotesOutline,
      notificationsOutline,
      paperPlane,
      refreshOutline,
      textOutline,
      trashOutline,
      volumeHighOutline,
    });
  }

  ngOnInit() {
    this.cargarVoces();
    if (this.hayVoz) window.speechSynthesis.onvoiceschanged = () => this.cargarVoces();
    this.leerAvisosDelPerfil();
  }

  ionViewWillEnter() {
    this.sb.loadProfile().then(() => this.leerAvisosDelPerfil()).catch(() => null);
  }

  private cargarVoces() {
    this.voces.set(this.ajustesSvc.vocesEspanol());
  }

  private leerAvisosDelPerfil() {
    const p = this.sb.currentProfile;
    if (!p) return;
    this.notificacionesActivas = p.notificaciones_activas ?? true;
    this.audio = !!p.preferencias?.audio;
    const guardados = ((p.preferencias as any)?.avisos || {}) as Partial<Record<TipoAviso, boolean>>;
    for (const t of this.tiposAviso) this.avisosTipo[t.tipo] = guardados[t.tipo] !== false;
    this.cdr.markForCheck();
  }

  cambiar(cambios: Partial<Ajustes>) {
    this.ajustesSvc.cambiar(cambios);
  }

  probarVoz() {
    this.ajustesSvc.leer('Hola. Así sonará la lectura de las fichas de los planes, por ejemplo: Taller de esparto, el sábado a las cinco de la tarde, en Tahal.');
  }

  async restablecer() {
    const alerta = await this.alertCtrl.create({
      header: '¿Volver a los ajustes de fábrica?',
      message: 'El tamaño, los colores, la letra, el sonido y la voz vuelven a como estaban al principio.',
      buttons: [
        { text: 'Cancelar', role: 'cancel' },
        { text: 'Sí, restablecer', role: 'confirm' },
      ],
    });
    await alerta.present();
    const { role } = await alerta.onDidDismiss();
    if (role !== 'confirm') return;
    this.ajustesSvc.restablecer();
    this.avisos.ok('Ajustes restablecidos.');
  }

  /** Borra la cuenta para siempre (se pide escribir BORRAR para evitar sustos). */
  async borrarCuenta() {
    const alerta = await this.alertCtrl.create({
      header: '¿Borrar tu cuenta?',
      message:
        'Se borran tu perfil, tus inscripciones, tus opiniones y la conexión con Telegram. Los planes que organizaste siguen publicados, pero sin tu nombre. No se puede deshacer. Para confirmarlo, escribe BORRAR.',
      inputs: [{ name: 'confirmacion', type: 'text', placeholder: 'BORRAR', attributes: { 'aria-label': 'Escribe BORRAR para confirmar' } }],
      buttons: [
        { text: 'No, mantenerla', role: 'cancel' },
        { text: 'Borrar mi cuenta', role: 'destructive' },
      ],
    });
    await alerta.present();
    const { role, data } = await alerta.onDidDismiss();
    if (role !== 'destructive') return;
    if (String(data?.values?.confirmacion || '').trim().toUpperCase() !== 'BORRAR') {
      this.avisos.aviso('No se ha borrado nada: tenías que escribir BORRAR.');
      return;
    }
    this.borrando = true;
    this.cdr.markForCheck();
    try {
      await this.api.borrarCuenta();
      await this.sb.client.auth.signOut();
      this.avisos.ok('Tu cuenta se ha borrado. ¡Gracias por haber participado!');
      this.router.navigateByUrl('/inicio');
    } catch (err) {
      this.avisos.error(err, 'No hemos podido borrar tu cuenta. Inténtalo de nuevo.');
    } finally {
      this.borrando = false;
      this.cdr.markForCheck();
    }
  }

  async guardarAvisos() {
    const p = this.sb.currentProfile;
    if (!p) return;
    this.guardandoAvisos = true;
    this.cdr.markForCheck();
    try {
      const { error } = await this.sb.client
        .from('profiles')
        .update({
          notificaciones_activas: this.notificacionesActivas,
          preferencias: { ...(p.preferencias || {}), audio: this.audio, avisos: { ...this.avisosTipo } },
        })
        .eq('id', p.id);
      if (error) throw error;
      await this.sb.loadProfile();
      this.avisos.ok('Avisos guardados.');
    } catch (err) {
      this.avisos.error(err, 'No hemos podido guardar tus avisos.');
    } finally {
      this.guardandoAvisos = false;
      this.cdr.markForCheck();
    }
  }
}
