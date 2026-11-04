"""Testes de autenticação: login, token obrigatório e token expirado/adulterado."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api import auth
from app.main import app

PROTECTED = [
    ("get", "/api/health", None),
    ("get", "/api/cities/default", None),
    ("get", "/api/cities/random?count=5", None),
    ("post", "/api/routes/optimize/default", {}),
    ("post", "/api/routes/optimize", {}),
    ("post", "/api/routes/ask", {"result_id": "x", "question": "teste"}),
    ("get", "/api/auth/me", None),
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth.settings, "auth_username", "admin")
    monkeypatch.setattr(auth.settings, "auth_password", "fiap@2026")
    monkeypatch.setattr(auth.settings, "jwt_secret", "segredo-de-teste")
    return TestClient(app)


def login(client, username="admin", password="fiap@2026"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


@pytest.mark.parametrize("method,url,body", PROTECTED)
def test_routes_require_token(client, method, url, body):
    resp = getattr(client, method)(url, json=body) if body is not None else getattr(client, method)(url)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_login_success_returns_working_token(client):
    resp = login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer" and body["username"] == "admin" and body["expires_in"] > 0
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.get("/api/auth/me", headers=headers).json() == {"username": "admin"}
    assert client.get("/api/health", headers=headers).status_code == 200


@pytest.mark.parametrize("username,password", [("admin", "errada"), ("outro", "fiap@2026"), ("", "")])
def test_login_rejects_invalid_credentials(client, username, password):
    assert login(client, username, password).status_code in (401, 422)


def test_expired_token_is_rejected(client):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    token = jwt.encode({"sub": "admin", "iat": past, "exp": past + timedelta(minutes=1)}, "segredo-de-teste", algorithm="HS256")
    resp = client.get("/api/health", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401 and "expirado" in resp.json()["detail"]


def test_token_signed_with_other_secret_is_rejected(client):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    token = jwt.encode({"sub": "admin", "exp": future}, "outro-segredo", algorithm="HS256")
    assert client.get("/api/health", headers={"Authorization": f"Bearer {token}"}).status_code == 401
