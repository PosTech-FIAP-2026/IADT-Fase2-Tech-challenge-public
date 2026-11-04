import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, throwError } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthService } from './auth.service';

/** Anexa o token às chamadas da API e volta para o login quando o backend responde 401. */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const isApi = req.url.startsWith(environment.apiUrl);
  const isLogin = req.url.endsWith('/auth/login');

  const token = isApi && !isLogin ? auth.token : null;
  const request = token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req;

  return next(request).pipe(
    catchError((err: unknown) => {
      if (err instanceof HttpErrorResponse && err.status === 401 && isApi && !isLogin) {
        auth.logout('Sua sessão expirou ou é inválida. Entre novamente.');
      }
      return throwError(() => err);
    }),
  );
};
