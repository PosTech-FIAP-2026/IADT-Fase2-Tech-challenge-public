"""Cadastro manual: estados, municípios (IBGE) e geocodificação de cidades."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import auth, routes_controller
from app.data import geo
from app.main import app
from app.services.distance import DistanceService
from app.services.optimizer_service import OptimizerService


class FakeGoogle:
    """Simula o GoogleMapsClient.geocode com uma resposta fixa."""

    def __init__(self, result=None, error: Exception | None = None):
        self.result, self.error = result, error

    def geocode(self, address):
        if self.error:
            raise self.error
        return self.result


IBGE = {"SP": ("Adamantina", "Campinas", "São José dos Campos", "Santos"), "MG": ("Belo Horizonte", "Betim"),
        "RJ": ("Rio de Janeiro", "Sumidouro")}


@pytest.fixture
def make_client(monkeypatch):
    # Lista de municípios do IBGE simulada (sem rede nos testes).
    monkeypatch.setattr(routes_controller, "municipalities", lambda uf: IBGE.get(uf, ()))
    monkeypatch.setattr(auth.settings, "auth_username", "admin")
    monkeypatch.setattr(auth.settings, "auth_password", "fiap@2026")

    def build(google=None):
        service = OptimizerService(DistanceService(google=google))
        app.dependency_overrides[routes_controller.get_optimizer] = lambda: service
        c = TestClient(app)
        token = c.post("/api/auth/login", json={"username": "admin", "password": "fiap@2026"}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"
        return c

    yield build
    app.dependency_overrides.clear()


def test_states(make_client):
    states = make_client().get("/api/geo/states").json()
    assert len(states) == 27 and {"uf": "SP", "name": "São Paulo"} in states


def test_geocode_from_local_catalog_ignores_case_and_accents(make_client):
    c = make_client()
    resp = c.post("/api/cities/geocode", json={"city": "sao jose dos campos", "state": "SP"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "São José dos Campos, SP" and body["source"] == "catalogo_local"


def test_geocode_unknown_city_without_google_explains(make_client):
    # Município real (está no IBGE), mas fora do catálogo local usado sem Google.
    resp = make_client().post("/api/cities/geocode", json={"city": "Adamantina", "state": "SP"})
    assert resp.status_code == 404 and "catálogo de SP" in resp.json()["detail"]


def test_geocode_rejects_invalid_state(make_client):
    assert make_client().post("/api/cities/geocode", json={"city": "Campinas", "state": "XX"}).status_code == 422


GOOD = {"lat": -19.92, "lng": -43.94, "address": "Belo Horizonte, MG, Brasil", "types": ["locality", "political"],
        "state": "MG", "country": "BR"}


def test_geocode_with_google(make_client):
    resp = make_client(FakeGoogle(GOOD)).post("/api/cities/geocode", json={"city": "Belo Horizonte", "state": "MG"})
    assert resp.status_code == 200
    assert resp.json() == {"name": "Belo Horizonte, MG", "lat": -19.92, "lng": -43.94,
                           "address": "Belo Horizonte, MG, Brasil", "source": "google_maps"}


@pytest.mark.parametrize("override", [
    {"state": "SP"},                                      # achou em outro estado
    {"types": ["administrative_area_level_1"]},           # achou o estado, não um município
    {"country": "PT"},                                    # fora do Brasil
])
def test_geocode_with_google_rejects_wrong_place(make_client, override):
    c = make_client(FakeGoogle({**GOOD, **override}))
    assert c.post("/api/cities/geocode", json={"city": "Belo Horizonte", "state": "MG"}).status_code == 404


def test_state_cities_from_ibge(make_client, monkeypatch):
    c = make_client()
    monkeypatch.setattr(routes_controller, "municipalities", geo.municipalities)  # função real, HTTP simulado
    geo.municipalities.cache_clear()
    request = httpx.Request("GET", "https://ibge")
    monkeypatch.setattr(geo.httpx, "get", lambda url, **kw: httpx.Response(
        200, json=[{"nome": "Abaeté"}, {"nome": "Belo Horizonte"}], request=request))
    assert c.get("/api/geo/states/MG/cities").json() == ["Abaeté", "Belo Horizonte"]
    geo.municipalities.cache_clear()


def test_state_cities_ibge_failure_returns_502(make_client, monkeypatch):
    c = make_client()
    monkeypatch.setattr(routes_controller, "municipalities", geo.municipalities)
    geo.municipalities.cache_clear()

    def boom(url, **kw):
        raise httpx.ConnectError("sem rede")

    monkeypatch.setattr(geo.httpx, "get", boom)
    assert c.get("/api/geo/states/MG/cities").status_code == 502
    geo.municipalities.cache_clear()


def test_geocode_rejects_name_that_is_not_a_municipality(make_client):
    # O Google acharia o bairro "Campinas" em Sumidouro-RJ; o IBGE diz que não é município do RJ.
    c = make_client(FakeGoogle({**GOOD, "state": "RJ"}))
    resp = c.post("/api/cities/geocode", json={"city": "Campinas", "state": "RJ"})
    assert resp.status_code == 404 and "não é um município" in resp.json()["detail"]


def test_geocode_suggests_close_names_and_uses_official_name(make_client):
    c = make_client(FakeGoogle(GOOD))
    resp = c.post("/api/cities/geocode", json={"city": "Belo Horisonte", "state": "MG"})
    assert resp.status_code == 404 and "Você quis dizer: Belo Horizonte?" in resp.json()["detail"]
    ok = c.post("/api/cities/geocode", json={"city": "belo horizonte", "state": "MG"}).json()
    assert ok["name"] == "Belo Horizonte, MG"  # grafia oficial do IBGE


def test_geocode_works_when_ibge_is_down(make_client, monkeypatch):
    c = make_client(FakeGoogle(GOOD))

    def down(uf):
        raise httpx.ConnectError("IBGE fora do ar")

    monkeypatch.setattr(routes_controller, "municipalities", down)
    assert c.post("/api/cities/geocode", json={"city": "Belo Horizonte", "state": "MG"}).status_code == 200
