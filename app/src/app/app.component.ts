import { Component, effect, inject, signal } from '@angular/core';
import { NavigationEnd, Router } from '@angular/router';
import { filter } from 'rxjs';
import { CommonModule } from '@angular/common';
import { IonApp, IonTabs, IonTabBar, IonTabButton, IonIcon, IonLabel } from '@ionic/angular';
import { addIcons } from 'ionicons';
import {
  home,
  homeOutline,
  calendar,
  calendarOutline,
  map,
  mapOutline,
  addCircle,
  addCircleOutline,
  person,
  personOutline,
  shieldCheckmark,
  shieldCheckmarkOutline,
  // Avisos
  checkmarkCircle,
  informationCircle,
  alertCircle,
  closeCircle,
  close,
  // Categorías
  walk,
  bicycle,
  colorPalette,
  restaurant,
  musicalNotes,
  construct,
  heart,
  sparkles,
  settingsOutline,
  paperPlane,
} from 'ionicons/icons';
import { environment } from '../environments/environment';
import { AjustesService } from './core/ajustes.service';
import { TraductorService } from './core/traductor.service';
import { SupabaseService } from './core/supabase.service';

@Component({
  selector: 'app-root',
  templateUrl: 'app.component.html',
  standalone: true,
  imports: [CommonModule, IonApp, IonTabs, IonTabBar, IonTabButton, IonIcon, IonLabel],
})
export class AppComponent {
  public sb = inject(SupabaseService);
  /** Aplica tamaño, colores, letra… guardados en este dispositivo desde el primer momento. */
  private ajustes = inject(AjustesService);
  private traductor = inject(TraductorService);
  /** Varios idiomas: si el idioma elegido no es español, se traduce lo que hay en pantalla. */
  private idiomaWeb = effect(() => {
    const idioma = this.ajustes.ajustes().idioma || 'es';
    setTimeout(() => this.traductor.activar(idioma), 300);
  });
  private router = inject(Router);

  /** La ficha de un plan forma parte de la agenda: se marca esa pestaña abajo. */
  enFichaDePlan = signal(false);

  /** Botón del asistente: lleva al bot de Telegram. */
  enlaceBot = `https://t.me/${environment.telegramBotUsername || 'ReactivaPuebloBot'}`;
  /** Hueco ocupado abajo por las barras fijas (pestañas, paginación, guardar, Atrás/Siguiente) para no taparlas. */
  altoPie = signal(70);
  verAsistente = signal(true);
  private medirPendiente = false;

  private medirPie() {
    if (this.medirPendiente) return;
    this.medirPendiente = true;
    // setTimeout y no requestAnimationFrame: este último se para si la pestaña no está a la vista
    setTimeout(() => {
      this.medirPendiente = false;
      const pie = document.querySelector<HTMLElement>(
        'ion-router-outlet > .ion-page:not(.ion-page-hidden) ion-footer, ion-tabs .ion-page:not(.ion-page-hidden) ion-footer',
      );
      // Lo que ocupa abajo la barra fija (o, si no hay, la barra de pestañas): el botón va justo encima
      const tope = pie ?? document.querySelector<HTMLElement>('ion-tab-bar');
      const alto = tope ? Math.max(0, Math.round(window.innerHeight - tope.getBoundingClientRect().top)) : 70;
      if (alto !== this.altoPie()) this.altoPie.set(alto);
    }, 60);
  }

  constructor() {
    document.documentElement.lang = 'es';
    // Las barras fijas aparecen y desaparecen al cambiar de pantalla o de paso: se vuelve a medir
    new MutationObserver(() => this.medirPie()).observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['class'],
    });
    window.addEventListener('resize', () => this.medirPie());
    this.router.events.pipe(filter((e) => e instanceof NavigationEnd)).subscribe((e) => {
      const url = (e as NavigationEnd).urlAfterRedirects;
      this.enFichaDePlan.set(url.startsWith('/evento/'));
      // En la pantalla de entrar ya está el botón de Telegram: ahí no hace falta el atajo
      this.verAsistente.set(!url.startsWith('/login'));
      // La pantalla nueva tarda un poco en colocar su barra fija
      [150, 500, 1200].forEach((ms) => setTimeout(() => this.medirPie(), ms));
    });
    addIcons({
      home,
      homeOutline,
      calendar,
      calendarOutline,
      map,
      mapOutline,
      addCircle,
      addCircleOutline,
      person,
      personOutline,
      shieldCheckmark,
      shieldCheckmarkOutline,
      checkmarkCircle,
      informationCircle,
      alertCircle,
      closeCircle,
      close,
      walk,
      bicycle,
      colorPalette,
      restaurant,
      musicalNotes,
      construct,
      heart,
      sparkles,
      settingsOutline,
      paperPlane,
    });
  }
}
