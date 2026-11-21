import { Component, inject, signal } from '@angular/core';
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
} from 'ionicons/icons';
import { SupabaseService } from './core/supabase.service';

@Component({
  selector: 'app-root',
  templateUrl: 'app.component.html',
  standalone: true,
  imports: [CommonModule, IonApp, IonTabs, IonTabBar, IonTabButton, IonIcon, IonLabel],
})
export class AppComponent {
  public sb = inject(SupabaseService);
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
    });
  }
}
