"""Grupos de medicamentos/insumos, frota heterogênea e janelas de horário."""
import pytest
from fastapi.testclient import TestClient

from app.api import auth, routes_controller
from app.data.catalog import DEFAULT_FLEET, VEHICLE_TYPES
from app.main import app
from app.services.distance import DistanceService
from app.services.optimizer_service import OptimizerService, build_fleet, fmt_time
from app.services.vrp import HARD, SOFT, VRPProblem, count_contamination, evaluate

# ---------------------------------------------------------------- regras de compatibilidade


@pytest.mark.parametrize("groups,expected", [
    (["A", "D", "E"], 0),
    (["A", "C"], 1),          # A e C juntos: contaminação
    (["A", "C", "C"], 1),     # basta tirar o A
    (["B", "B"], 0),          # B com B pode
    (["B", "D"], 1),          # B deve viajar sozinho
    (["B", "A", "C"], 3),     # B+A, B+C e A+C
])
def test_count_contamination(groups, expected):
    assert count_contamination(groups) == expected


# Origem 0; entregas 1 e 2 a 50 km da origem e a 10 km entre si (100 min = 100 km neste exemplo).
DIST = [[0, 50, 50], [50, 0, 10], [50, 10, 0]]


def problem(vehicles, groups=("D", "D", "D"), windows=None, **kw):
    return VRPProblem(
        distance_km=DIST, duration_min=DIST, demands=[0, 10, 10], priorities=["regular"] * 3,
        origin=0, destination=0, vehicles=build_fleet(vehicles), groups=list(groups),
        windows=windows, departure_min=8 * 60, service_min=0, **kw,
    )


def test_contamination_is_always_hard():
    p = problem(["carro", "carro"], groups=("D", "A", "C"), capacity_mode=SOFT, autonomy_mode=SOFT)
    assert evaluate([1, 2, -1], p)[2] is False   # A e C no mesmo carro
    assert evaluate([1, -1, 2], p)[2] is True    # separados


def test_refrigerated_group_needs_refrigerated_vehicle():
    p = problem(["carro", "caminhao_refrigerado"], groups=("D", "B", "D"))
    assert evaluate([1, -1, 2], p)[2] is False   # B no carro comum
    assert evaluate([2, -1, 1], p)[2] is True    # B sozinho no refrigerado


def test_electric_car_autonomy_is_always_hard():
    far = [[0, 200, 200], [200, 0, 10], [200, 10, 0]]  # 400 km ida e volta > 300 km do elétrico
    common = dict(distance_km=far, duration_min=far, demands=[0, 1, 1], priorities=["regular"] * 3,
                  origin=0, destination=0, autonomy_mode=SOFT)
    electric = VRPProblem(vehicles=build_fleet(["carro_eletrico"]), **common)
    car = VRPProblem(vehicles=build_fleet(["moto"]), **common)
    assert evaluate([1, 2], electric)[2] is False  # elétrico nunca estoura a autonomia
    assert evaluate([1, 2], car)[2] is True        # moto com autonomia soft pode


def test_time_window_wait_and_lateness():
    # Saída 08:00; chega na entrega 1 às 08:50 (janela 09:00-10:00) -> espera 10 min.
    # Entrega 2 às 09:10 (janela 08:00-09:05) -> 5 min de atraso.
    windows = [(0, 1439), (9 * 60, 10 * 60), (8 * 60, 9 * 60 + 5)]
    hard = problem(["carro"], windows=windows, time_mode=HARD)
    _, routes, feasible = evaluate([1, 2], hard)
    first, second = routes[0].schedule
    assert first.wait_min == 10 and first.late_min == 0
    assert second.arrival_min == 9 * 60 + 10 and second.late_min == 5
    assert feasible is False
    assert evaluate([1, 2], problem(["carro"], windows=windows, time_mode=SOFT))[2] is True


def test_fmt_time():
    assert fmt_time(7 * 60 + 5) == "07:05"
    assert fmt_time(24 * 60 + 30) == "00:30 (+1d)"


# ---------------------------------------------------------------- API
FAST_GA = {"population_size": 60, "generations": 120, "seed": 7}


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


def test_catalog(client):
    body = client.get("/api/catalog").json()
    assert [g["code"] for g in body["product_groups"]] == ["A", "B", "C", "D", "E"]
    assert body["product_groups"][1]["refrigerated"] is True
    assert len(body["transport_rules"]) == 2
    types = {v["type"]: v for v in body["vehicle_types"]}
    assert types["carro_eletrico"]["strict_autonomy"] is True
    assert types["moto"]["capacity"] == 15 and types["caminhao_refrigerado"]["refrigerated"] is True
    assert body["default_fleet"] == list(VEHICLE_TYPES)


def test_random_cities_have_group_and_window(client):
    cities = client.get("/api/cities/random", params={"count": 10}).json()["cities"]
    assert all(c["product_group"] in "ABCDE" and c["window_start"] < c["window_end"] for c in cities)


def test_default_scenario_respects_transport_rules(client):
    resp = client.post("/api/routes/optimize/default", json={"ga": FAST_GA, "use_llm": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["feasible"] is True and len(body["transport_rules"]) == 2
    for v in body["vehicles"]:
        assert v["contamination_ok"] is True
        assert not {"A", "C"} <= set(v["groups"])
        if "B" in v["groups"]:
            assert v["groups"] == ["B"] and v["refrigerated"] is True
        if v["stops"]:
            assert len(v["schedule"]) == len(v["stops"]) - 2 and v["return_time"]


def test_fleet_without_refrigeration_is_rejected(client):
    resp = client.post("/api/routes/optimize/default",
                       json={"ga": FAST_GA, "use_llm": False, "vehicles": [{"type": "caminhao"}] * 3})
    assert resp.status_code == 422
    assert any("refrigeração" in r for r in resp.json()["detail"]["reasons"])


def test_fleet_too_small_for_segregation_is_rejected(client):
    # Cenário padrão tem B (sozinho) + A e C (separados): exige 3 veículos.
    resp = client.post("/api/routes/optimize/default",
                       json={"ga": FAST_GA, "use_llm": False,
                             "vehicles": [{"type": "caminhao_refrigerado"}, {"type": "caminhao"}]})
    assert resp.status_code == 422
    assert any("ao menos 3 veículos" in r for r in resp.json()["detail"]["reasons"])


def test_invalid_time_window_is_rejected(client):
    resp = client.post("/api/routes/optimize", json={
        "locations": [
            {"name": "Origem", "lat": -23.55, "lng": -46.63},
            {"name": "X", "lat": -22.9, "lng": -47.06, "window_start": "14:00", "window_end": "09:00"},
        ],
        "use_llm": False,
    })
    assert resp.status_code == 422


def test_default_fleet_constant_matches_catalog():
    assert DEFAULT_FLEET == list(VEHICLE_TYPES)


def test_repair_moves_misplaced_deliveries():
    from app.services.genetic_algorithm import repair
    from app.services.vrp import split_routes
    # Entrega 1 = B (refrigerada), 2 = A, 3 = C. Veículos: carro, caminhão refrigerado, carro.
    dist = [[0, 10, 10, 10], [10, 0, 5, 5], [10, 5, 0, 5], [10, 5, 5, 0]]
    p = VRPProblem(distance_km=dist, duration_min=dist, demands=[0, 1, 1, 1], priorities=["regular"] * 4,
                   origin=0, destination=0, vehicles=build_fleet(["carro", "caminhao_refrigerado", "carro"]),
                   groups=["D", "B", "A", "C"])
    fixed = repair([1, 2, 3, -1, -2], p)  # tudo no carro 1: B sem refrigeração e A com C
    routes = split_routes(fixed)
    assert sorted(fixed) == sorted([1, 2, 3, -1, -2])
    assert routes[1] == [1]                       # B foi para o refrigerado, sozinho
    assert {2, 3} & set(routes[0]) != {2, 3}      # A e C não ficaram juntos
    assert evaluate(fixed, p)[2] is True


# ---------------------------------------------------------------- abastecimento em rota

LINE = [[0, 100, 200, 300], [100, 0, 100, 200], [200, 100, 0, 100], [300, 200, 100, 0]]  # cidades em linha, 100 km


def line_problem(vehicles, **kw):
    return VRPProblem(distance_km=LINE, duration_min=LINE, demands=[0, 1, 1, 1], priorities=["regular"] * 4,
                      origin=0, destination=0, vehicles=build_fleet(vehicles), departure_min=8 * 60,
                      service_min=0, **kw)


def test_refuel_when_next_leg_would_go_below_reserve():
    # Carro: 500 km de autonomia, reserva 15% -> pode rodar 425 km entre abastecimentos.
    # Rota 0 -> 1 -> 2 -> 3 -> 0: 100 + 100 + 100 + 300 = 600 km.
    p = line_problem(["carro"], refuel_enabled=True, refuel_reserve=0.15, refuel_min=15)
    _, routes, feasible = evaluate([1, 2, 3], p)
    r = routes[0]
    assert feasible and r.autonomy_excess_km == 0
    # Em 3, já rodou 300 km; o trecho de 300 km até a origem passaria de 425 -> abastece em 3.
    assert [f.location for f in r.refuels] == [3]
    assert r.refuels[0].range_left_pct == pytest.approx(40.0)
    assert r.schedule[-1].refuel and r.schedule[-1].departure_min - r.schedule[-1].arrival_min == 15
    assert r.duration_min == 600 + 15  # viagem + abastecimento


def test_without_refuel_total_distance_must_fit_one_tank():
    p = line_problem(["carro"], refuel_enabled=False)
    _, routes, feasible = evaluate([1, 2, 3], p)
    assert not feasible and routes[0].autonomy_excess_km == 100 and routes[0].refuels == []


def test_electric_car_never_refuels():
    p = line_problem(["carro_eletrico"], refuel_enabled=True)  # 300 km de autonomia
    _, routes, feasible = evaluate([1, 2, 3], p)
    assert not feasible and routes[0].refuels == [] and routes[0].autonomy_excess_km == 300


def test_long_leg_refuels_en_route_several_times():
    # Moto: 140 km de autonomia, reserva 15% -> abastece a cada 119 km. Rota 0 -> 1 -> 2 -> 0 (100, 100, 200 km).
    p = line_problem(["moto"], refuel_enabled=True)
    _, routes, feasible = evaluate([1, 2], p)
    r = routes[0]
    assert feasible and r.autonomy_excess_km == 0
    # Abastece nas cidades 1 e 2 e, no trecho 2 -> 0 (200 km), mais uma vez no caminho (km 119).
    assert [f.location for f in r.refuels] == [1, 2, None]
    stop = r.refuels[-1]
    assert stop.en_route and (stop.leg_from, stop.leg_to) == (2, 0) and stop.km_into_leg == pytest.approx(119)
    assert stop.range_left_pct == pytest.approx(15)


def test_very_long_leg_never_arrives_empty():
    # Carro (500 km, abastece a cada 425 km): origem -> entrega a 2000 km -> destino na mesma cidade.
    # O trecho longo gera 4 paradas no caminho, e nenhuma chega com o tanque vazio.
    far3 = [[0, 2000, 2000], [2000, 0, 0], [2000, 0, 0]]
    p = VRPProblem(distance_km=far3, duration_min=far3, demands=[0, 1, 0], priorities=["regular"] * 3,
                   origin=0, destination=2, vehicles=build_fleet(["carro"]), departure_min=0, service_min=0,
                   refuel_enabled=True, refuel_reserve=0.15, refuel_min=15)
    _, routes, feasible = evaluate([1], p)
    r = routes[0]
    en_route = [f for f in r.refuels if f.en_route]
    assert feasible and [round(f.km_into_leg) for f in en_route] == [425, 850, 1275, 1700]
    assert all(f.range_left_pct == pytest.approx(15) for f in en_route)
    # Chega à entrega com 2000 min de viagem + 4 abastecimentos de 15 min.
    assert r.schedule[0].arrival_min == pytest.approx(2000 + 4 * 15)


def test_point_along_geometry():
    from app.services.optimizer_service import point_along
    line = [[0.0, 0.0], [0.0, 1.0], [0.0, 2.0]]
    assert point_along(line, 0.5) == pytest.approx((0.0, 1.0))
    assert point_along(line, 0.25) == pytest.approx((0.0, 0.5))
    assert point_along(line, 1.0) == pytest.approx((0.0, 2.0))


def test_api_long_leg_lists_en_route_refuels(client):
    # Porto Alegre -> Fortaleza (~3.900 km estimados) de carro, ida e volta: várias paradas no caminho.
    locations = [
        {"name": "Porto Alegre, RS", "lat": -30.0346, "lng": -51.2177},
        {"name": "Fortaleza, CE", "lat": -3.7319, "lng": -38.5267, "demand": 10},
    ]
    resp = client.post("/api/routes/optimize", json={
        "locations": locations, "vehicles": [{"type": "carro"}], "ga": FAST_GA, "use_llm": False,
    })
    assert resp.status_code == 200, resp.text
    v = resp.json()["vehicles"][0]
    en_route = [f for f in v["refuels"] if f["en_route"]]
    assert v["autonomy_ok"] and len(en_route) >= 8
    assert all(f["range_left_pct"] == 15 for f in en_route)
    # Sem Google nos testes, o local vem como "km X do trecho A → B" e as coordenadas ficam na linha reta.
    assert en_route[0]["place"].startswith("km 425 do trecho Porto Alegre, RS")
    assert en_route[0]["lat"] is not None
