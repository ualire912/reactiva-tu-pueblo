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
} from 'ionicons/icons';
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

  constructor() {
    document.documentElement.lang = 'es';
    this.router.events.pipe(filter((e) => e instanceof NavigationEnd)).subscribe((e) => {
      this.enFichaDePlan.set((e as NavigationEnd).urlAfterRedirects.startsWith('/evento/'));
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
    });
  }
}
