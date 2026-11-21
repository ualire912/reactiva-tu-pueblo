import { ChangeDetectorRef, Component, OnDestroy, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { IonHeader, IonToolbar, IonButtons, IonBackButton, IonTitle, IonContent, IonButton, IonIcon, IonSpinner } from '@ionic/angular';
import { addIcons } from 'ionicons';
import { eyeOutline, eyeOffOutline, paperPlane, callOutline, addCircleOutline } from 'ionicons/icons';
import { SupabaseService } from '../../../core/supabase.service';
import { AvisosService } from '../../../core/avisos.service';
import { ApiService } from '../../../core/api.service';

/** '600 11 22 33' → '+34600112233' (igual que el servidor, api/routers/acceso.py). */
export function normalizarTelefono(texto: string): string | null {
  const t = (texto || '').trim();
  let digitos = t.replace(/\D/g, '');
  if (t.startsWith('00')) digitos = digitos.slice(2);
  else if (!t.startsWith('+') && digitos.length === 9 && '6789'.includes(digitos[0])) digitos = '34' + digitos;
  if (digitos.length < 8 || digitos.length > 15) return null;
  return '+' + digitos;
}

/** Correo técnico con el que Supabase identifica una cuenta creada con teléfono. */
function emailDeAcceso(telefono: string): string {
  return `tel${telefono.replace('+', '')}@reactiva.local`;
}

@Component({
  selector: 'app-login',
  templateUrl: './login.page.html',
  standalone: true,
  imports: [CommonModule, FormsModule, IonHeader, IonToolbar, IonButtons, IonBackButton, IonTitle, IonContent, IonButton, IonIcon, IonSpinner],
})
export class LoginPage implements OnDestroy {
  private sb = inject(SupabaseService);
  private api = inject(ApiService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);
  private avisos = inject(AvisosService);
  /** Sin zone.js: avisar a Angular tras esperar al servidor para que repinte. */
  private cdr = inject(ChangeDetectorRef);

  /** Mostrar u ocultar la contraseña (ayuda a quien escribe despacio o con el móvil). */
  verClave = false;
  modoRegistro = false;
  fullName = '';
  /** Al entrar: teléfono o correo (las cuentas antiguas usan correo). */
  usuario = '';
  telefono = '';
  email = '';
  password = '';
  cargando = false;

  // Entrar con Telegram
  esperandoTelegram = false;
  enlaceTelegram = '';
  private sondeo?: ReturnType<typeof setInterval>;

  /** Por qué llega aquí (p. ej. ?motivo=proponer) y a dónde volver después. */
  motivo = this.route.snapshot.queryParamMap.get('motivo') || '';
  private volver = this.route.snapshot.queryParamMap.get('volver') || '/inicio';

  constructor() {
    addIcons({ eyeOutline, eyeOffOutline, paperPlane, callOutline, addCircleOutline });
  }

  ngOnDestroy() {
    clearInterval(this.sondeo);
  }

  ionViewWillLeave() {
    clearInterval(this.sondeo);
    this.esperandoTelegram = false;
  }

  private destino(): string {
    return this.volver.startsWith('/') && !this.volver.startsWith('//') ? this.volver : '/inicio';
  }

  // ---------- Teléfono (o correo) y contraseña ----------
  async enviar() {
    if (this.cargando) return;
    this.cargando = true;
    try {
      if (this.modoRegistro) await this.registrarse();
      else await this.entrar();
    } catch (err: any) {
      this.avisos.error(err, this.modoRegistro ? 'No hemos podido crear la cuenta. Inténtalo de nuevo.' : 'No hemos podido entrar. Revisa tus datos e inténtalo de nuevo.');
    } finally {
      this.cargando = false;
      this.cdr.markForCheck();
    }
  }

  private async entrar() {
    const usuario = this.usuario.trim();
    if (!usuario || !this.password) {
      this.avisos.aviso('Escribe tu teléfono (o tu correo) y tu contraseña.');
      return;
    }
    let email = usuario;
    if (!usuario.includes('@')) {
      const tel = normalizarTelefono(usuario);
      if (!tel) {
        this.avisos.aviso('Ese teléfono no parece correcto. Escríbelo así: 600 11 22 33.');
        return;
      }
      email = emailDeAcceso(tel);
    }
    const { error } = await this.sb.client.auth.signInWithPassword({ email, password: this.password });
    if (error) throw error;
    this.avisos.ok('¡Hola de nuevo!');
    this.router.navigateByUrl(this.destino());
  }

  private async registrarse() {
    if (!this.fullName.trim()) {
      this.avisos.aviso('Escribe tu nombre.');
      return;
    }
    const tel = normalizarTelefono(this.telefono);
    if (!tel) {
      this.avisos.aviso('Escribe tu número de teléfono (por ejemplo, 600 11 22 33).');
      return;
    }
    if (this.password.length < 6) {
      this.avisos.aviso('La contraseña debe tener al menos 6 caracteres.');
      return;
    }
    const { email_acceso } = await this.api.registroTelefono({
      telefono: tel,
      password: this.password,
      nombre: this.fullName.trim(),
      email: this.email.trim() || null,
    });
    const { error } = await this.sb.client.auth.signInWithPassword({ email: email_acceso, password: this.password });
    if (error) throw error;
    this.avisos.ok('¡Cuenta creada! Te damos la bienvenida.');
    this.router.navigateByUrl(this.destino());
  }

  // ---------- Telegram ----------
  async entrarConTelegram() {
    if (this.esperandoTelegram) return;
    // La pestaña se abre ya (si se abre tras esperar al servidor, el navegador la bloquea)
    const ventana = window.open('', '_blank');
    this.esperandoTelegram = true;
    this.cdr.markForCheck();
    try {
      const { codigo, enlace } = await this.api.telegramIniciar();
      this.enlaceTelegram = enlace;
      if (ventana) ventana.location.href = enlace;
      this.esperarTelegram(codigo);
    } catch (err) {
      ventana?.close();
      this.esperandoTelegram = false;
      this.avisos.error(err, 'No hemos podido conectar con Telegram. Inténtalo de nuevo en unos segundos.');
    } finally {
      this.cdr.markForCheck();
    }
  }

  private esperarTelegram(codigo: string) {
    clearInterval(this.sondeo);
    const limite = Date.now() + 10 * 60000;
    this.sondeo = setInterval(async () => {
      if (Date.now() > limite) return this.cancelarTelegram('El tiempo para entrar con Telegram se ha acabado. Vuelve a intentarlo.');
      try {
        const r = await this.api.telegramEstado(codigo);
        if (!r.listo || !r.token_hash) return;
        clearInterval(this.sondeo);
        const { error } = await this.sb.client.auth.verifyOtp({ token_hash: r.token_hash, type: 'magiclink' });
        if (error) throw error;
        this.esperandoTelegram = false;
        this.avisos.ok('¡Has entrado con Telegram!');
        this.router.navigateByUrl(this.destino());
      } catch (err: any) {
        if (err?.status === 404 || err?.status === 410 || err?.name !== 'ApiError') {
          this.cancelarTelegram();
          this.avisos.error(err, 'No hemos podido entrar con Telegram. Vuelve a intentarlo.');
        }
      } finally {
        this.cdr.markForCheck();
      }
    }, 2500);
  }

  cancelarTelegram(mensaje?: string) {
    clearInterval(this.sondeo);
    this.esperandoTelegram = false;
    if (mensaje) this.avisos.aviso(mensaje);
    this.cdr.markForCheck();
  }
}
