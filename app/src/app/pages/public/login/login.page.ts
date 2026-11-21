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
  IonList,
  IonItem,
  IonLabel,
  IonInput,
  IonButton,
  ToastController
} from '@ionic/angular';
import { SupabaseService } from '../../../core/supabase.service';

@Component({
  selector: 'app-login',
  templateUrl: './login.page.html',
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
    IonList,
    IonItem,
    IonLabel,
    IonInput,
    IonButton
  ]
})
export class LoginPage {
  private sb = inject(SupabaseService);
  private router = inject(Router);
  private toastCtrl = inject(ToastController);

  modoRegistro = false;
  fullName = '';
  email = '';
  password = '';
  cargando = false;

  async enviar() {
    if (!this.email || !this.password) {
      this.mostrarAviso('Escribe tu correo y contraseña');
      return;
    }

    this.cargando = true;
    try {
      if (this.modoRegistro) {
        const { error } = await this.sb.client.auth.signUp({
          email: this.email.trim(),
          password: this.password,
          options: {
            data: { full_name: this.fullName.trim() || 'Vecino/a' }
          }
        });
        if (error) throw error;
        this.mostrarAviso('¡Cuenta creada! Bienvenido/a');
      } else {
        const { error } = await this.sb.client.auth.signInWithPassword({
          email: this.email.trim(),
          password: this.password
        });
        if (error) throw error;
        this.mostrarAviso('Sesión iniciada');
      }
      this.router.navigateByUrl('/agenda');
    } catch (err: any) {
      this.mostrarAviso(err.message || 'Error al iniciar sesión');
    } finally {
      this.cargando = false;
    }
  }

  private async mostrarAviso(message: string) {
    const toast = await this.toastCtrl.create({ message, duration: 2500, position: 'bottom' });
    await toast.present();
  }
}