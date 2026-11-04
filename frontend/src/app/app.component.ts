import { Component, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatToolbarModule } from '@angular/material/toolbar';
import { LoginComponent } from './components/login.component';
import { PlannerComponent } from './planner.component';
import { AuthService } from './services/auth.service';

/** Shell da aplicação: mostra o login ou a tela de otimização conforme a sessão. */
@Component({
  selector: 'app-root',
  standalone: true,
  imports: [LoginComponent, PlannerComponent, MatToolbarModule, MatButtonModule, MatIconModule],
  template: `
    @if (auth.isAuthenticated) {
      <mat-toolbar color="primary" class="app-toolbar">
        <span class="brand">
          <span>🚑 Rotas Médicas</span>
          <small>Otimização de rotas para distribuição de medicamentos e insumos com algoritmo genético</small>
        </span>
        <span class="spacer"></span>
        <span class="user"><mat-icon>account_circle</mat-icon> {{ auth.username }}</span>
        <button mat-stroked-button type="button" (click)="auth.logout()">
          <mat-icon>logout</mat-icon> Sair
        </button>
      </mat-toolbar>
      <app-planner />
    } @else {
      <app-login />
    }

    <footer class="app-footer">
      <p class="footer-title">Grupo 17 - Tech Challenge Fase 2</p>
      <p class="footer-label">Integrantes</p>
      <ul class="footer-members">
        @for (m of members; track m.rm) { <li>{{ m.name }} - {{ m.rm }}</li> }
      </ul>
      <p class="footer-class">TURMA: 11IADT</p>
    </footer>
  `,
})
export class AppComponent {
  auth = inject(AuthService);

  readonly members = [
    { name: 'Douglas de Souza Santos', rm: 'RM376076' },
    { name: 'Elisangela de Souza Alves', rm: 'RM376784' },
    { name: 'Thiago Oliveira Teberga', rm: 'RM376295' },
  ];
}
