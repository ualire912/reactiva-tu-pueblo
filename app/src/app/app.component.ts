import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import {
  IonApp,
  IonTabs,
  IonTabBar,
  IonTabButton,
  IonIcon,
  IonLabel
} from '@ionic/angular';
import { addIcons } from 'ionicons';
import {
  calendarOutline,
  mapOutline,
  addCircleOutline,
  personOutline,
  shieldCheckmarkOutline
} from 'ionicons/icons';
import { SupabaseService } from './core/supabase.service';

@Component({
  selector: 'app-root',
  templateUrl: 'app.component.html',
  standalone: true,
  imports: [
    CommonModule,
    IonApp,
    IonTabs,
    IonTabBar,
    IonTabButton,
    IonIcon,
    IonLabel
  ],
})
export class AppComponent {
  public sb = inject(SupabaseService);

  constructor() {
    addIcons({
      calendarOutline,
      mapOutline,
      addCircleOutline,
      personOutline,
      shieldCheckmarkOutline
    });
  }
}