import { Injectable, inject } from '@angular/core';
import { ToastController } from '@ionic/angular';
import { AjustesService } from './ajustes.service';

/**
 * Avisos al usuario en lenguaje sencillo (sin códigos, tablas ni tipos de datos).
 */
@Injectable({ providedIn: 'root' })
export class AvisosService {
  private toastCtrl = inject(ToastController);
  private ajustes = inject(AjustesService);

  ok(mensaje: string) {
    return this.mostrar(mensaje, 'success', 'checkmark-circle');
  }

  info(mensaje: string) {
    return this.mostrar(mensaje, 'dark', 'information-circle');
  }

  aviso(mensaje: string) {
    return this.mostrar(mensaje, 'warning', 'alert-circle');
  }

  /** Muestra un error traducido a lenguaje normal. */
  error(err: unknown, porDefecto = 'No se ha podido completar. Inténtalo de nuevo en un momento.') {
    return this.mostrar(mensajeAmigable(err, porDefecto), 'danger', 'close-circle');
  }

  private async mostrar(message: string, color: string, icon: string) {
    // Sonido y vibración, si están activados en Ajustes
    this.ajustes.respuesta(color === 'danger' ? 'error' : color === 'warning' ? 'aviso' : 'ok');
    const toast = await this.toastCtrl.create({
      message,
      color,
      icon,
      duration: 3500,
      position: 'top',
      cssClass: 'aviso-toast',
      buttons: [{ icon: 'close', role: 'cancel', side: 'end', htmlAttributes: { 'aria-label': 'Cerrar aviso' } }],
    });
    await toast.present();
  }
}

/**
 * Convierte errores de Supabase, del servidor o de red en frases comprensibles.
 * Los mensajes que la base de datos ya da en español y claros se mantienen.
 */
export function mensajeAmigable(err: unknown, porDefecto = 'Algo no ha ido bien. Inténtalo de nuevo.'): string {
  const e = err as { message?: string; code?: string; status?: number } | null;
  const raw = (typeof err === 'string' ? err : e?.message || '').trim();
  const msg = raw.toLowerCase();
  const code = String(e?.code || '');

  if (!raw && !code) return porDefecto;

  // Mensajes del servidor que ya vienen en español (ApiError con "detail")
  if ((err as Error)?.name === 'ApiError' && e?.status && e.status < 500 && raw && !raw.startsWith('HTTP ')) return raw;

  // Mensajes propios de la BD al apuntarse (ya están en español)
  if (msg.includes('plazo de inscripción')) return 'El plazo para apuntarse a este plan ya ha terminado.';
  if (msg.includes('no admite inscripciones')) return 'Este plan ya no admite inscripciones.';

  // Red / servidor dormido
  if (msg.includes('failed to fetch') || msg.includes('networkerror') || msg.includes('load failed') || msg.includes('timeout') || msg.includes('aborted')) {
    return 'No hemos podido conectar. Revisa tu conexión e inténtalo de nuevo en unos segundos.';
  }

  // Sesión
  if (msg.includes('jwt') || msg.includes('token') || e?.status === 401) return 'Tu sesión ha caducado. Vuelve a entrar, por favor.';
  if (msg.includes('invalid login credentials')) return 'El teléfono (o correo) o la contraseña no son correctos.';
  if (msg.includes('user already registered') || msg.includes('already been registered')) return 'Ya existe una cuenta con ese correo. Prueba a entrar.';
  if (msg.includes('password should be at least') || msg.includes('weak password')) return 'La contraseña debe tener al menos 6 caracteres.';
  if (msg.includes('email') && msg.includes('invalid')) return 'Revisa que el correo esté bien escrito.';
  if (msg.includes('email not confirmed')) return 'Tienes que confirmar tu correo antes de entrar.';

  // Permisos
  if (code === '42501' || msg.includes('row-level security') || msg.includes('permission denied') || e?.status === 403) {
    return 'No tienes permiso para hacer esto.';
  }

  // Datos duplicados / no válidos
  if (code === '23505' || msg.includes('duplicate key')) return 'Esto ya estaba guardado antes.';
  if (code === '23514' || msg.includes('violates check constraint')) return 'Algún dato no es válido. Revisa el formulario.';
  if (code === '23502' || msg.includes('null value')) return 'Falta algún dato obligatorio.';
  if (code === '22P02' || msg.includes('invalid input syntax')) return 'Algún dato no tiene el formato correcto.';
  if (msg.includes('payload too large') || msg.includes('exceeded the maximum')) return 'La imagen es demasiado grande. Prueba con una más pequeña.';

  // Si es un texto en español sin aspecto técnico, se muestra tal cual
  const pareceTecnico = /[_{}()[\]<>=]|error:|exception|violat|constraint|relation|column|null|undefined|\bat\b/i.test(raw);
  if (!pareceTecnico && /[áéíóúñ¿¡]/i.test(raw)) return raw;

  return porDefecto;
}
