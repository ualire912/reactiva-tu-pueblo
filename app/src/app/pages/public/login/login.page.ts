import { ChangeDetectorRef, Component, inject } from '@angular/core';
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
  IonIcon
} from '@ionic/angular';
import { addIcons } from 'ionicons';
import { eyeOutline, eyeOffOutline } from 'ionicons/icons';
import { SupabaseService } from '../../../core/supabase.service';
import { AvisosService } from '../../../core/avisos.service';

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
    IonButton,
    IonIcon
  ]
})
export class LoginPage {
  /** Mostrar u ocultar la contraseña (ayuda a quien escribe despacio o con el móvil). */
  verClave = false;

  constructor() {
    addIcons({ eyeOutline, eyeOffOutline });
  }

  private sb = inject(SupabaseService);
  private router = inject(Router);
  private avisos = inject(AvisosService);

  modoRegistro = false;
  fullName = '';
  email = '';
  password = '';
  cargando = false;
  /** Sin zone.js: avisar a Angular tras esperar al servidor para que repinte el botón. */
  private cdr = inject(ChangeDetectorRef);

  async enviar() {
    if (this.cargando) return;
    if (!this.email || !this.password) {
      this.avisos.aviso('Escribe tu correo y tu contraseña.');
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
        this.avisos.ok('¡Cuenta creada! Te damos la bienvenida.');
      } else {
        const { error } = await this.sb.client.auth.signInWithPassword({
          email: this.email.trim(),
          password: this.password
        });
        if (error) throw error;
        this.avisos.ok('¡Hola de nuevo!');
      }
      this.router.navigateByUrl('/inicio');
    } catch (err: any) {
      this.avisos.error(err, 'No hemos podido entrar. Revisa tus datos e inténtalo de nuevo.');
    } finally {
      this.cargando = false;
      this.cdr.markForCheck();
    }
  }

}