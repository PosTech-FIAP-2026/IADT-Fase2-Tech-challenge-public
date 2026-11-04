import { HttpErrorResponse } from '@angular/common/http';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { AuthService } from '../services/auth.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [
    FormsModule, MatCardModule, MatFormFieldModule, MatInputModule, MatButtonModule, MatIconModule,
    MatProgressSpinnerModule,
  ],
  template: `
    <div class="login-page">
      <mat-card appearance="outlined" class="login-card">
        <form (ngSubmit)="submit()" #f="ngForm">
          <div class="login-brand">🚑</div>
          <h1>Rotas Médicas</h1>
          <p class="hint">Entre para otimizar as rotas de distribuição de medicamentos e insumos.</p>

          @if (auth.notice) { <p class="login-notice" role="status">{{ auth.notice }}</p> }

          <mat-form-field>
            <mat-label>Usuário</mat-label>
            <input matInput name="username" [(ngModel)]="username" required autocomplete="username" autofocus
                   [disabled]="loading" />
            <mat-icon matPrefix>person</mat-icon>
          </mat-form-field>

          <mat-form-field>
            <mat-label>Senha</mat-label>
            <input matInput name="password" [type]="showPassword ? 'text' : 'password'" [(ngModel)]="password" required
                   autocomplete="current-password" [disabled]="loading" />
            <mat-icon matPrefix>lock</mat-icon>
            <button mat-icon-button matSuffix type="button" (click)="showPassword = !showPassword"
                    [attr.aria-label]="showPassword ? 'Ocultar senha' : 'Mostrar senha'">
              <mat-icon>{{ showPassword ? 'visibility_off' : 'visibility' }}</mat-icon>
            </button>
          </mat-form-field>

          @if (error) { <p class="bad" role="alert">{{ error }}</p> }

          <button mat-flat-button type="submit" [disabled]="loading || !f.valid">
            @if (loading) { <mat-spinner class="btn-spinner" diameter="18" /> Entrando... } @else { Entrar }
          </button>
        </form>
      </mat-card>
    </div>
  `,
})
export class LoginComponent {
  auth = inject(AuthService);

  username = '';
  password = '';
  showPassword = false;
  loading = false;
  error = '';

  submit(): void {
    if (!this.username || !this.password) return;
    this.loading = true;
    this.error = '';
    this.auth.login(this.username.trim(), this.password).subscribe({
      next: () => {
        this.loading = false;
        this.password = '';
      },
      error: (err: HttpErrorResponse) => {
        this.loading = false;
        this.error =
          err.status === 401
            ? 'Usuário ou senha inválidos.'
            : err.status === 0
              ? 'Não foi possível conectar ao backend. Verifique se a API está rodando.'
              : `Erro ao entrar (${err.status}).`;
      },
    });
  }
}
