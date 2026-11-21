import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
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
  IonList,
  IonItem,
  IonInput,
  IonSelect,
  IonSelectOption,
  IonTextarea,
  IonToggle,
  IonButton,
  IonIcon,
  IonBadge,
  ToastController
} from '@ionic/angular';
import { addIcons } from 'ionicons';
import {
  sparklesOutline,
  imageOutline,
  saveOutline,
  alertCircleOutline
} from 'ionicons/icons';
import { SupabaseService } from '../../../core/supabase.service';
import { ApiService } from '../../../core/api.service';

@Component({
  selector: 'app-proponer',
  templateUrl: './proponer.page.html',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
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
    IonList,
    IonItem,
    IonInput,
    IonSelect,
    IonSelectOption,
    IonTextarea,
    IonToggle,
    IonButton,
    IonIcon,
    IonBadge
  ]
})
export class ProponerPage {
  public sb = inject(SupabaseService);
  private api = inject(ApiService);
  private router = inject(Router);
  private toastCtrl = inject(ToastController);

  // Campos del formulario
  title = '';
  category = 'otro';
  municipioId: number | null = null;
  startsAtLocal = '';
  inscripcionHastaLocal = '';
  durationMinutes: number | null = 120;
  locationName = '';
  price: number = 0;
  maxCapacity: number | null = null;
  minAge: number | null = null;
  maxAge: number | null = null;
  description = '';
  isAccessible = false;
  posterUrl = '';

  // IA y subida de imagen
  textoCartelOpcional = '';
  camposFaltantes: string[] = [];
  subiendoFoto = false;
  extrayendoIa = false;
  guardando = false;

  constructor() {
    addIcons({
      sparklesOutline,
      imageOutline,
      saveOutline,
      alertCircleOutline
    });
  }

  async onFileSelected(event: any) {
    const file: File | undefined = event.target.files?.[0];
    if (!file) return;

    this.subiendoFoto = true;
    try {
      const ext = file.name.split('.').pop() || 'jpg';
      const filePath = `cartel-${Date.now()}.${ext}`;

      const { error } = await this.sb.client.storage
        .from('posters')
        .upload(filePath, file, { upsert: true });

      if (error) throw error;

      const { data } = this.sb.client.storage.from('posters').getPublicUrl(filePath);
      this.posterUrl = data.publicUrl;
      this.mostrarAviso('Cartel subido correctamente');
    } catch (err: any) {
      this.mostrarAviso(err.message || 'Error al subir la imagen');
    } finally {
      this.subiendoFoto = false;
    }
  }

  async rellenarConIa() {
    if (!this.posterUrl && !this.textoCartelOpcional.trim()) {
      this.mostrarAviso('Sube primero la foto de un cartel o pega el texto del anuncio');
      return;
    }

    this.extrayendoIa = true;
    try {
      const res = await this.api.extraerEvento({
        image_url: this.posterUrl || undefined,
        text: this.textoCartelOpcional.trim() || undefined
      });

      if (res.title) this.title = res.title;
      if (res.category) this.category = res.category;
      if (res.location_name) this.locationName = res.location_name;
      if (res.price !== null && res.price !== undefined) this.price = res.price;
      if (res.max_capacity !== null && res.max_capacity !== undefined) this.maxCapacity = res.max_capacity;
      if (res.duration_minutes) this.durationMinutes = res.duration_minutes;
      if (res.min_age !== null) this.minAge = res.min_age;
      if (res.max_age !== null) this.maxAge = res.max_age;
      if (res.description) this.description = res.description;
      if (typeof res.is_accessible === 'boolean') this.isAccessible = res.is_accessible;

      if (res.starts_at) {
        const d = new Date(res.starts_at);
        if (!isNaN(d.getTime())) {
          const offset = d.getTimezoneOffset() * 60000;
          this.startsAtLocal = new Date(d.getTime() - offset).toISOString().slice(0, 16);
        }
      }

      if (res.inscripcion_hasta) {
        const dLim = new Date(res.inscripcion_hasta);
        if (!isNaN(dLim.getTime())) {
          const offset = dLim.getTimezoneOffset() * 60000;
          this.inscripcionHastaLocal = new Date(dLim.getTime() - offset).toISOString().slice(0, 16);
        }
      }

      if (res.municipio) {
        const encontrado = this.sb.municipios.find(
          (m) => m.nombre.toLowerCase().includes(res.municipio!.toLowerCase())
        );
        if (encontrado) this.municipioId = encontrado.id;
      }

      this.camposFaltantes = res.missing || [];
      this.mostrarAviso(
        this.camposFaltantes.length > 0
          ? 'Datos extraídos. Revisa los campos marcados en amarillo.'
          : '¡Todos los datos rellenados automáticamente!'
      );
    } catch {
      this.mostrarAviso('No se pudo conectar con la IA. Rellena los campos a mano.');
    } finally {
      this.extrayendoIa = false;
    }
  }

  faltaCampo(nombreCampo: string): boolean {
    return this.camposFaltantes.includes(nombreCampo);
  }

  async guardarEvento() {
    if (!this.title.trim() || !this.municipioId || !this.startsAtLocal) {
      this.mostrarAviso('Completa al menos el título, el pueblo y la fecha');
      return;
    }

    if (this.inscripcionHastaLocal && new Date(this.inscripcionHastaLocal) > new Date(this.startsAtLocal)) {
      this.mostrarAviso('La fecha límite de inscripción debe ser anterior o igual al inicio del evento');
      return;
    }

    this.guardando = true;
    try {
      const { data, error } = await this.sb.client
        .from('eventos')
        .insert({
          title: this.title.trim(),
          category: this.category,
          municipio_id: Number(this.municipioId),
          starts_at: new Date(this.startsAtLocal).toISOString(),
          inscripcion_hasta: this.inscripcionHastaLocal ? new Date(this.inscripcionHastaLocal).toISOString() : null,
          duration_minutes: this.durationMinutes ? Number(this.durationMinutes) : null,
          location_name: this.locationName.trim() || 'Por confirmar',
          price: Number(this.price) || 0,
          max_capacity: this.maxCapacity ? Number(this.maxCapacity) : null,
          min_age: this.minAge ? Number(this.minAge) : null,
          max_age: this.maxAge ? Number(this.maxAge) : null,
          description: this.description.trim() || null,
          is_accessible: this.isAccessible,
          poster_url: this.posterUrl || null,
          origen: 'web'
        })
        .select()
        .single();

      if (error) throw error;

      if (data.status === 'abierto' || data.status === 'completo') {
        this.mostrarAviso('¡Publicado directamente en la agenda!');
      } else {
        this.mostrarAviso('Enviado. Un moderador lo revisará en breve.');
      }

      this.router.navigateByUrl('/agenda');
    } catch (err: any) {
      this.mostrarAviso(err.message || 'Error al guardar el evento');
    } finally {
      this.guardando = false;
    }
  }

  private async mostrarAviso(message: string) {
    const toast = await this.toastCtrl.create({ message, duration: 3000 });
    await toast.present();
  }
}