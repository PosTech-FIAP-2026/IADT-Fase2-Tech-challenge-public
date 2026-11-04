"""Autenticação por token JWT (Bearer) com um usuário fixo configurado no backend."""
import hmac
import logging
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"

bearer_scheme = HTTPBearer(auto_error=False, description="Token obtido em POST /api/auth/login")


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Validade do token em segundos")
    username: str


class UserInfo(BaseModel):
    username: str


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def authenticate(username: str, password: str) -> bool:
    # compare_digest evita ataques de temporização; avalia os dois campos sempre.
    user_ok = hmac.compare_digest(username.encode(), settings.auth_username.encode())
    pass_ok = hmac.compare_digest(password.encode(), settings.auth_password.encode())
    return user_ok and pass_ok


def create_access_token(username: str) -> tuple[str, int]:
    expires_in = settings.jwt_expire_minutes * 60
    now = datetime.now(timezone.utc)
    payload = {"sub": username, "iat": now, "exp": now + timedelta(seconds=expires_in)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), expires_in


def require_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> str:
    """Dependência que exige um token válido; retorna o usuário autenticado."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Token de acesso ausente.")
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]})
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token expirado. Faça login novamente.") from None
    except jwt.InvalidTokenError:
        raise _unauthorized("Token inválido.") from None
    if payload["sub"] != settings.auth_username:
        raise _unauthorized("Usuário do token não é mais válido.")
    return payload["sub"]


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest):
    """Valida usuário e senha e devolve um token de acesso (JWT)."""
    if not authenticate(req.username, req.password):
        logger.warning("Tentativa de login inválida para o usuário '%s'", req.username)
        raise _unauthorized("Usuário ou senha inválidos.")
    token, expires_in = create_access_token(req.username)
    return TokenResponse(access_token=token, expires_in=expires_in, username=req.username)


@router.get("/me", response_model=UserInfo)
def me(username: str = Depends(require_user)):
    """Retorna o usuário dono do token (útil para validar a sessão no frontend)."""
    return UserInfo(username=username)
