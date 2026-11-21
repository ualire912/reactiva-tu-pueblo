import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { IonContent, IonHeader, IonTitle, IonToolbar } from '@ionic/angular';

@Component({
  selector: 'app-mi-agenda',
  templateUrl: './mi-agenda.page.html',
  styleUrls: ['./mi-agenda.page.scss'],
  imports: [IonContent, IonHeader, IonTitle, IonToolbar, CommonModule, FormsModule],
})
export class MiAgendaPage implements OnInit {
  constructor() {}

  ngOnInit() {}
}
