import { Component, effect, inject, input, signal } from '@angular/core';
import { Prevision, TiempoService } from '../../core/tiempo.service';

/**
 * Tarjetita con el tiempo previsto en el lugar y a la hora del plan,
 * y aviso en naranja si se espera lluvia.
 */
@Component({
  selector: 'app-tiempo-evento',
  standalone: true,
  template: `
    @if (prevision(); as t) {
      @if (t.estado === 'ok') {
        <span class="tiempo-chip" [attr.aria-label]="'Tiempo previsto: ' + t.texto + ', ' + t.temperatura + ' grados'">
          <span class="tiempo-emoji" aria-hidden="true">{{ t.emoji }}</span>
          <strong>{{ t.temperatura }} °C</strong>
          @if (!compacto()) {
            <span class="tiempo-texto">{{ t.texto }}</span>
          }
        </span>
        @if (t.lluvia) {
          <p class="aviso-lluvia" role="note">
            <span aria-hidden="true">⚠️</span>
            Aviso por lluvia{{ t.probLluvia ? ': ' + t.probLluvia + '% de probabilidad a esa hora' : ' a esa hora' }}
          </p>
        }
      } @else if (!compacto() && t.estado === 'lejos') {
        <p class="tiempo-lejos">🌤️ La previsión del tiempo aparecerá unos días antes del plan.</p>
      }
    }
  `,
})
export class TiempoEventoComponent {
  private tiempo = inject(TiempoService);

  lat = input<number | null | undefined>(null);
  lng = input<number | null | undefined>(null);
  inicio = input<string | null | undefined>(null);
  /** En las tarjetas: solo emoticono y grados. */
  compacto = input(false);

  prevision = signal<Prevision | null>(null);

  constructor() {
    effect(() => {
      const [lat, lng, inicio] = [this.lat(), this.lng(), this.inicio()];
      this.tiempo.prevision(lat, lng, inicio).then((p) => this.prevision.set(p));
    });
  }
}
