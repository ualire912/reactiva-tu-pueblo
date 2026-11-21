import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterModule } from '@angular/router';
import {
  IonHeader,
  IonToolbar,
  IonTitle,
  IonButtons,
  IonButton,
  IonIcon,
  IonContent,
  IonCard,
  IonCardHeader,
  IonCardTitle,
  IonCardSubtitle,
  IonCardContent,
  IonList,
  IonItem,
  IonLabel,
  IonInput,
  IonSelect,
  IonSelectOption,
  IonToggle,
  IonChip,
  IonBadge,
  IonNote,
  ToastController
} from '@ionic/angular';
import { addIcons } from 'ionicons';
import {
  paperPlaneOutline,
  checkmarkCircleOutline,
  refreshOutline,
  logOutOutline,
  calendarOutline,
  volumeHighOutline,
  saveOutline
} from 'ionicons/icons';
import { SupabaseService } from '../../../core/supabase.service';
import { environment } from '../../../../environments/environment';
import { Inscripcion } from '../../../core/models';

@Component({
  selector: 'app-perfil',
  templateUrl: './perfil.page.html',
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
    IonIcon,
    IonContent,
    IonCard,
    IonCardHeader,
    IonCardTitle,
    IonCardSubtitle,
    IonCardContent,
    IonList,
    IonItem,
    IonLabel,
    IonInput,
    IonSelect,
    IonSelectOption,
    IonToggle,
    IonChip,
    IonBadge,
    IonNote
  ]
})
export class PerfilPage implements OnInit {
  public sb = inject(SupabaseService);
  private router = inject(Router);
  private toastCtrl = inject(ToastController);

  // Campos editables del perfil
  fullName = '';
  edad: number | null = null;
  municipioId: number | null = null;
  categoriasFav: string[] = [];
  municipiosFav: string[] = []; // Siempre strings: ["1", "2"]
  notificacionesActivas = true;
  prefiereAudio = false;

  misPlanes: Inscripcion[] = [];
  guardando = false;
  comprobandoTelegram = false;

  constructor() {
    addIcons({
      paperPlaneOutline,
      checkmarkCircleOutline,
      refreshOutline,
      logOutOutline,
      calendarOutline,
      volumeHighOutline,
      saveOutline
    });
  }

  async ngOnInit() {
    await this.cargarDatos();
  }

  async ionViewWillEnter() {
    await this.cargarDatos();
  }

  async cargarDatos() {
    const profile = await this.sb.loadProfile();
    if (profile) {
      this.fullName = profile.full_name || '';
      this.edad = profile.edad ?? null;
      this.municipioId = profile.municipio_id ?? null;
      this.categoriasFav = [...(profile.preferencias?.categorias || [])];
      this.municipiosFav = [...(profile.preferencias?.municipios || [])].map(String);
      this.notificacionesActivas = profile.notificaciones_activas ?? true;
      this.prefiereAudio = profile.preferencias?.audio ?? false;
    }

    await this.cargarMisPlanes();
  }

  async cargarMisPlanes() {
    const uid = this.sb.currentSession?.user?.id;
    if (!uid) return;

    const { data } = await this.sb.client
      .from('inscripciones')
      .select('*, eventos(*, municipios(nombre))')
      .eq('user_id', uid)
      .neq('status', 'cancelada')
      .order('created_at', { ascending: false });

    if (data) {
      this.misPlanes = data as Inscripcion[];
    }
  }

  toggleCategoria(slug: string) {
    if (this.categoriasFav.includes(slug)) {
      this.categoriasFav = this.categoriasFav.filter((c) => c !== slug);
    } else {
      this.categoriasFav = [...this.categoriasFav, slug];
    }
  }

  toggleMunicipioFav(id: number) {
    const idStr = String(id);
    if (this.municipiosFav.includes(idStr)) {
      this.municipiosFav = this.municipiosFav.filter((m) => m !== idStr);
    } else {
      this.municipiosFav = [...this.municipiosFav, idStr];
    }
  }

  async guardarPerfil() {
    const uid = this.sb.currentSession?.user?.id;
    if (!uid) return;

    this.guardando = true;
    try {
      const { error } = await this.sb.client
        .from('profiles')
        .update({
          full_name: this.fullName.trim() || null,
          edad: this.edad ? Number(this.edad) : null,
          municipio_id: this.municipioId ? Number(this.municipioId) : null,
          notificaciones_activas: this.notificacionesActivas,
          preferencias: {
            categorias: this.categoriasFav,
            municipios: this.municipiosFav.map(String),
            audio: this.prefiereAudio
          }
        })
        .eq('id', uid);

      if (error) throw error;

      await this.sb.loadProfile(uid);
      this.mostrarAviso('Preferencias guardadas');
    } catch (err: any) {
      this.mostrarAviso(err.message || 'Error al guardar');
    } finally {
      this.guardando = false;
    }
  }

  get enlaceTelegram(): string {
    const code = this.sb.currentProfile?.link_code || '';
    const bot = environment.telegramBotUsername || 'ReactivaPuebloBot';
    return `https://t.me/${bot}?start=VINCULAR-${code}`;
  }

  async comprobarVinculacion() {
    this.comprobandoTelegram = true;
    try {
      const p = await this.sb.loadProfile();
      if (p?.telegram_linked) {
        this.mostrarAviso('¡Telegram vinculado correctamente!');
      } else {
        this.mostrarAviso('Aún no aparece vinculado. Pulsa "Iniciar" en Telegram y vuelve a probar.');
      }
    } finally {
      this.comprobandoTelegram = false;
    }
  }

  async cerrarSesion() {
    await this.sb.signOut();
    this.router.navigateByUrl('/agenda');
  }

  formatearFecha(iso?: string): string {
    if (!iso) return '';
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