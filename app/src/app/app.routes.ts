import { Routes } from '@angular/router';
import { authGuard, moderadorGuard } from './core/guards';

export const routes: Routes = [
  {
    path: '',
    redirectTo: 'inicio',
    pathMatch: 'full',
  },
  {
    path: 'inicio',
    loadComponent: () => import('./pages/public/inicio/inicio.page').then((m) => m.InicioPage),
  },
  {
    path: 'agenda',
    loadComponent: () => import('./pages/public/agenda/agenda.page').then((m) => m.AgendaPage),
  },
  {
    path: 'mapa',
    loadComponent: () => import('./pages/public/mapa/mapa.page').then((m) => m.MapaPage),
  },
  {
    path: 'evento/:id',
    loadComponent: () => import('./pages/public/evento-detalle/evento-detalle.page').then((m) => m.EventoDetallePage),
  },
  {
    path: 'proponer',
    canActivate: [authGuard],
    loadComponent: () => import('./pages/public/proponer/proponer.page').then((m) => m.ProponerPage),
  },
  {
    path: 'perfil',
    canActivate: [authGuard],
    loadComponent: () => import('./pages/public/perfil/perfil.page').then((m) => m.PerfilPage),
  },
  {
    path: 'ajustes',
    loadComponent: () => import('./pages/public/ajustes/ajustes.page').then((m) => m.AjustesPage),
  },
  {
    path: 'login',
    loadComponent: () => import('./pages/public/login/login.page').then((m) => m.LoginPage),
  },
  {
    path: 'panel',
    canActivate: [moderadorGuard],
    loadComponent: () => import('./pages/panel/panel.page').then((m) => m.PanelPage),
  },
  {
    path: '**',
    redirectTo: 'inicio',
  },
];