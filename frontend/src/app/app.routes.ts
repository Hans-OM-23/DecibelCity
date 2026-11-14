import { Routes } from '@angular/router';
import { MainLayoutComponent } from './layout/main-layout/main-layout.component';

export const routes: Routes = [
  {
    path: '',
    component: MainLayoutComponent,
    children: [
      { path: '', title: 'DecibelCity · El sonido de Huancayo', loadComponent: () => import('./features/home/home.component').then((m) => m.HomeComponent) },
      { path: 'puntos-criticos', title: 'Puntos críticos · DecibelCity', loadComponent: () => import('./features/hotspots/hotspots.component').then((m) => m.HotspotsComponent) },
      { path: 'reportar', title: 'Reportar ruido · DecibelCity', loadComponent: () => import('./features/reports/report.component').then((m) => m.ReportComponent) },
      { path: 'privacidad', title: 'Privacidad · DecibelCity', data: { kind: 'privacy' }, loadComponent: () => import('./features/legal/legal.component').then((m) => m.LegalComponent) },
      { path: 'condiciones', title: 'Condiciones de uso · DecibelCity', data: { kind: 'terms' }, loadComponent: () => import('./features/legal/legal.component').then((m) => m.LegalComponent) },
      { path: '**', redirectTo: '' },
    ],
  },
];
