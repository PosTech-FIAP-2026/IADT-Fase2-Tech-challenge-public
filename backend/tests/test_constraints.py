"""Restrições soft x hard: fitness, ordem de prioridade e respostas da API."""
import pytest
from fastapi.testclient import TestClient

from app.api import auth, routes_controller
from app.main import app
from app.services.distance import DistanceService
from app.data.catalog import DEFAULT_FLEET
from app.services.optimizer_service import OptimizerService
from app.services.vrp import HARD, SOFT, VRPProblem, calculate_fitness, evaluate, uniform_fleet

# Exemplo: A (crítica, 40 kg, 50 km da origem), B (regular, 70 kg, 90 km da origem, 60 km de A).
DIST = [[0, 50, 90], [50, 0, 60], [90, 60, 0]]


def problem(capacity=100, autonomy_km=300, n_vehicles=2, **kw):
    base = dict(
        distance_km=DIST, duration_min=DIST, demands=[0, 40, 70], priorities=["regular", "critical", "regular"],
        origin=0, destination=0, vehicles=uniform_fleet(n_vehicles, capacity, autonomy_km), priority_weight=0.5,
    )
    base.update(kw)
    return VRPProblem(**base)


ONE_VEHICLE = [1, 2, -1]   # origem -> A -> B -> origem (110 kg, 200 km)
TWO_VEHICLES = [1, -1, 2]  # origem -> A -> origem; origem -> B -> origem (280 km)


def test_hard_capacity_rejects_overload():
    p = problem(capacity_mode=HARD)
    assert calculate_fitness(TWO_VEHICLES, p) < calculate_fitness(ONE_VEHICLE, p)
    assert evaluate(ONE_VEHICLE, p)[2] is False


def test_soft_capacity_accepts_cheap_overload():
    # 10 kg excedidos x 5 = 50 de custo < 80 km economizados ao usar um só veículo.
    p = problem(capacity_mode=SOFT, soft_penalty=5)
    assert calculate_fitness(ONE_VEHICLE, p) < calculate_fitness(TWO_VEHICLES, p)
    assert evaluate(ONE_VEHICLE, p)[2] is True  # soft não torna a solução inviável


def test_soft_capacity_with_high_penalty_behaves_like_hard():
    p = problem(capacity_mode=SOFT, soft_penalty=1000)
    assert calculate_fitness(TWO_VEHICLES, p) < calculate_fitness(ONE_VEHICLE, p)


def test_hard_priority_requires_critical_first():
    p = problem(capacity=1000, priority_mode=HARD, priority_weight=0)
    critical_last = [2, 1, -1]
    _, routes, feasible = evaluate(critical_last, p)
    assert routes[0].priority_inversions == 1 and feasible is False
    assert evaluate([1, 2, -1], p)[2] is True


def test_hard_autonomy_counts_return_trip():
    p = problem(capacity=1000, autonomy_km=150, autonomy_mode=HARD)
    assert evaluate(ONE_VEHICLE, p)[2] is False   # 200 km
    assert evaluate(TWO_VEHICLES, p)[2] is False  # B: 180 km
    p_soft = problem(capacity=1000, autonomy_km=150, autonomy_mode=SOFT)
    assert evaluate(TWO_VEHICLES, p_soft)[2] is True


# ---------------------------------------------------------------- API
FAST_GA = {"population_size": 40, "generations": 60, "seed": 3}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth.settings, "auth_username", "admin")
    monkeypatch.setattr(auth.settings, "auth_password", "fiap@2026")
    service = OptimizerService(DistanceService(google=None))
    app.dependency_overrides[routes_controller.get_optimizer] = lambda: service
    c = TestClient(app)
    token = c.post("/api/auth/login", json={"username": "admin", "password": "fiap@2026"}).json()["access_token"]
    c.headers["Authorization"] = f"Bearer {token}"
    yield c
    app.dependency_overrides.clear()


def fleet(*types):
    return [{"type": t} for t in types]


def optimize_default(client, vehicles, constraints):
    return client.post("/api/routes/optimize/default",
                       json={"ga": FAST_GA, "use_llm": False, "vehicles": vehicles, "constraints": constraints})


def test_api_hard_capacity_impossible_returns_422_with_reason(client):
    # Demanda total do cenário padrão = 130 kg > 3 motos x 15 kg.
    resp = optimize_default(client, fleet("moto", "moto", "moto"), {"capacity": "hard"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "rígidas" in detail["message"]
    assert any("demanda total" in r.lower() for r in detail["reasons"])


def test_api_soft_capacity_returns_route_and_lists_violation(client):
    # B vai sozinho no caminhão refrigerado; A e C ficam em motos diferentes e excedem os 15 kg.
    resp = optimize_default(client, fleet("caminhao_refrigerado", "moto", "moto"),
                            {"capacity": "soft", "autonomy": "soft", "soft_penalty": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["feasible"] is True
    assert body["constraints"]["capacity"] == "soft"
    assert any("acima da capacidade" in v for v in body["soft_violations"])
    assert any(not v["capacity_ok"] for v in body["vehicles"] if v["stops"])


def test_api_hard_autonomy_impossible_names_the_city(client):
    locations = [
        {"name": "São Paulo, SP", "lat": -23.5505, "lng": -46.6333},
        {"name": "Campinas, SP", "lat": -22.9056, "lng": -47.0608, "demand": 5},
    ]
    # Sem abastecimento, a moto (140 km) não faz ida e volta São Paulo-Campinas (~220 km).
    resp = client.post("/api/routes/optimize", json={
        "locations": locations, "vehicles": fleet("moto"), "ga": FAST_GA, "use_llm": False,
        "constraints": {"autonomy": "hard"}, "schedule": {"refuel_enabled": False},
    })
    assert resp.status_code == 422
    assert any("Campinas" in r and "autonomia" in r for r in resp.json()["detail"]["reasons"])

    # Com abastecimento, ela abastece em Campinas antes de voltar.
    resp = client.post("/api/routes/optimize", json={
        "locations": locations, "vehicles": fleet("moto"), "ga": FAST_GA, "use_llm": False,
        "constraints": {"autonomy": "hard"},
    })
    assert resp.status_code == 200, resp.text
    moto = resp.json()["vehicles"][0]
    assert moto["autonomy_ok"] and [f["location"] for f in moto["refuels"]] == [1]
    assert moto["schedule"][0]["refuel"] is True


def test_api_hard_priority_orders_critical_first(client):
    resp = optimize_default(client, fleet(*DEFAULT_FLEET), {"priority": "hard"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for v in body["vehicles"]:
        prios = [body["locations"][i]["priority"] for i in v["stops"][1:-1]]
        assert prios == sorted(prios)  # "critical" < "regular": todas as críticas primeiro
        assert v["priority_ok"] is True
