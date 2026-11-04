"""Testes da controller sem dependências externas (Google/OpenRouter desligados)."""
import pytest
from fastapi.testclient import TestClient

from app.api import auth, routes_controller
from app.main import app
from app.services.distance import DistanceService
from app.services.google_maps import decode_polyline
from app.services.optimizer_service import OptimizerService

FAST_GA = {"population_size": 30, "generations": 30, "seed": 1}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth.settings, "auth_username", "admin")
    monkeypatch.setattr(auth.settings, "auth_password", "fiap@2026")
    service = OptimizerService(DistanceService(google=None))
    app.dependency_overrides[routes_controller.get_optimizer] = lambda: service
    monkeypatch.setattr(routes_controller.llm.settings, "openrouter_api_key", "", raising=False)
    test_client = TestClient(app)
    token = test_client.post("/api/auth/login", json={"username": "admin", "password": "fiap@2026"}).json()["access_token"]
    test_client.headers["Authorization"] = f"Bearer {token}"
    yield test_client
    app.dependency_overrides.clear()


def test_health(client):
    assert client.get("/api/health").json()["distance_source"] == "haversine"


def test_default_cities(client):
    cities = client.get("/api/cities/default").json()["cities"]
    assert len(cities) == 5
    assert cities[0]["name"].startswith("São Paulo")


def test_random_cities(client):
    cities = client.get("/api/cities/random", params={"count": 8}).json()["cities"]
    assert len(cities) == 8
    assert len({c["name"] for c in cities}) == 8


def test_random_cities_validates_count(client):
    assert client.get("/api/cities/random", params={"count": 1}).status_code == 422


def test_optimize_default(client):
    resp = client.post("/api/routes/optimize/default", json={"ga": FAST_GA})
    assert resp.status_code == 200
    body = resp.json()
    visited = sorted(i for v in body["vehicles"] for i in v["stops"][1:-1])
    assert visited == [1, 2, 3, 4]
    assert body["llm_technical"]["provider"] == "template"
    assert "Sugestões de ajuste de parâmetros" in body["llm_technical"]["content"]
    assert body["llm_driver"]["provider"] == "template"
    assert "Instruções por veículo" in body["llm_driver"]["content"]
    assert body["ga"]["population_size"] == FAST_GA["population_size"]
    assert body["comparison"]["genetic_algorithm"]["fitness"] <= body["comparison"]["nearest_neighbor"]["fitness"]


def test_optimize_with_origin_and_destination(client):
    cities = client.get("/api/cities/random", params={"count": 6}).json()["cities"]
    # Frota e restrições folgadas: qualquer sorteio de cidades de SP é viável
    # (B sozinho no refrigerado; A e C podem ir em caminhões diferentes).
    vehicles = [{"type": t} for t in ("caminhao_refrigerado", "caminhao", "caminhao", "caminhao")]
    resp = client.post(
        "/api/routes/optimize",
        json={"locations": cities, "origin_index": 2, "destination_index": 5, "vehicles": vehicles,
              "constraints": {"autonomy": "soft", "time_window": "soft"}, "ga": FAST_GA, "use_llm": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    used = [v for v in body["vehicles"] if v["stops"]]
    assert all(v["stops"][0] == 2 and v["stops"][-1] == 5 for v in used)
    assert body["llm_technical"] is None and body["llm_driver"] is None


def test_optimize_rejects_invalid_index(client):
    cities = client.get("/api/cities/random", params={"count": 3}).json()["cities"]
    resp = client.post("/api/routes/optimize", json={"locations": cities, "origin_index": 9})
    assert resp.status_code == 422


def test_ask_unknown_result(client):
    assert client.post("/api/routes/ask", json={"result_id": "nope", "question": "Qual a rota?"}).status_code == 404


def test_ask_without_llm_key(client):
    rid = client.post("/api/routes/optimize/default", json={"ga": FAST_GA, "use_llm": False}).json()["id"]
    answer = client.post("/api/routes/ask", json={"result_id": rid, "question": "Qual a rota?"}).json()["answer"]
    assert answer["generated"] is False


def test_decode_polyline():
    # Exemplo da documentação do Google
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]]


def test_default_seed_is_reproducible_and_reports_stagnation(client):
    body = {"ga": {"population_size": 30, "generations": 400, "stagnation_generations": 15}, "use_llm": False}
    a = client.post("/api/routes/optimize/default", json=body).json()
    b = client.post("/api/routes/optimize/default", json=body).json()
    assert a["ga"]["seed"] == 42
    assert a["fitness"] == b["fitness"] and a["fitness_history"] == b["fitness_history"]
    assert a["stopped_by_stagnation"] is True and a["generations"] < 400
