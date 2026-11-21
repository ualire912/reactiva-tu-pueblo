import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { IonContent, IonHeader, IonTitle, IonToolbar } from '@ionic/angular';

@Component({
  selector: 'app-moderacion',
  templateUrl: './moderacion.page.html',
  styleUrls: ['./moderacion.page.scss'],
  imports: [IonContent, IonHeader, IonTitle, IonToolbar, CommonModule, FormsModule],
})
export class ModeracionPage implements OnInit {
  constructor() {}

  ngOnInit() {}
}
