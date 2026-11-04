"""Configurações da aplicação lidas de variáveis de ambiente (.env)."""
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass
class Settings:
    google_maps_api_key: str = field(default_factory=lambda: os.getenv("GOOGLE_MAPS_API_KEY", "").strip())
    openrouter_api_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", "").strip())
    openrouter_model: str = field(default_factory=lambda: os.getenv("OPENROUTER_MODEL", "anthropic/claude-haiku-5.5"))
    openrouter_fallback_models: list[str] = field(
        default_factory=lambda: [m.strip() for m in os.getenv("OPENROUTER_FALLBACK_MODELS", "").split(",") if m.strip()]
    )
    openrouter_base_url: str = field(
        default_factory=lambda: os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    )
    cors_origins: list[str] = field(
        default_factory=lambda: [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:4200").split(",") if o.strip()]
    )
    http_timeout_seconds: float = 60.0

    # Autenticação: usuário fixo (hard coded), sobrescrevível pelo .env.
    auth_username: str = field(default_factory=lambda: os.getenv("AUTH_USERNAME", "admin"))
    auth_password: str = field(default_factory=lambda: os.getenv("AUTH_PASSWORD", "fiap@2026"))
    # Sem JWT_SECRET no .env, gera um segredo aleatório a cada start (tokens antigos deixam de valer).
    jwt_secret: str = field(default_factory=lambda: os.getenv("JWT_SECRET", "").strip() or secrets.token_urlsafe(48))
    jwt_expire_minutes: int = field(default_factory=lambda: int(os.getenv("JWT_EXPIRE_MINUTES", "480")))


settings = Settings()
