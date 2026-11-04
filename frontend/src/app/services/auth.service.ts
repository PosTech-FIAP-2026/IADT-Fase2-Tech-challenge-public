import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, tap } from 'rxjs';
import { environment } from '../../environments/environment';

interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  username: string;
}

interface Session {
  token: string;
  username: string;
  expiresAt: number; // epoch em ms
}

const STORAGE_KEY = 'rotas-medicas.session';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private http = inject(HttpClient);
  private session: Session | null = this.restore();
  private expiryTimer?: ReturnType<typeof setTimeout>;

  constructor() {
    this.scheduleExpiry();
  }

  /** Mensagem exibida na tela de login (ex.: sessão expirada). */
  notice = '';

  get isAuthenticated(): boolean {
    return !!this.session && Date.now() < this.session.expiresAt;
  }

  get token(): string | null {
    return this.isAuthenticated ? this.session!.token : null;
  }

  get username(): string {
    return this.session?.username ?? '';
  }

  login(username: string, password: string): Observable<TokenResponse> {
    return this.http.post<TokenResponse>(`${environment.apiUrl}/auth/login`, { username, password }).pipe(
      tap((r) => {
        this.session = { token: r.access_token, username: r.username, expiresAt: Date.now() + r.expires_in * 1000 };
        this.notice = '';
        this.persist();
        this.scheduleExpiry();
      }),
    );
  }

  logout(notice = ''): void {
    this.session = null;
    this.notice = notice;
    this.persist();
    this.scheduleExpiry();
  }

  /** Volta para o login automaticamente quando o token expira. */
  private scheduleExpiry(): void {
    if (this.expiryTimer) clearTimeout(this.expiryTimer);
    this.expiryTimer = undefined;
    if (!this.session) return;
    const ms = this.session.expiresAt - Date.now();
    // setTimeout aceita no máximo ~24,8 dias; tokens daqui são de horas.
    this.expiryTimer = setTimeout(() => this.logout('Sua sessão expirou. Entre novamente.'), Math.max(0, Math.min(ms, 2 ** 31 - 1)));
  }

  // sessionStorage: a sessão sobrevive a um F5, mas não a fechar a aba.
  private persist(): void {
    try {
      if (this.session) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(this.session));
      else sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      /* armazenamento indisponível (modo privado etc.): a sessão fica só em memória */
    }
  }

  private restore(): Session | null {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      const s = raw ? (JSON.parse(raw) as Session) : null;
      return s && s.token && s.expiresAt > Date.now() ? s : null;
    } catch {
      return null;
    }
  }
}
