"""Orquestra: distâncias reais -> algoritmo genético -> comparativo -> polylines -> LLM."""
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from app.data.catalog import PRODUCT_GROUPS, TRANSPORT_RULES, VEHICLE_TYPES, incompatible, needs_refrigeration
from app.schemas import (
    Comparison,
    Location,
    OptimizeRequest,
    OptimizeResponse,
    RefuelInfo,
    StopSchedule,
    StrategyResult,
    VehicleRoute,
    hhmm_to_min,
)
from app.services import llm
from app.services.distance import DistanceService, haversine_km
from app.services.genetic_algorithm import run_genetic_algorithm
from app.services.heuristics import nearest_neighbor_solution, random_average_solution
from app.services.vrp import DAY_MINUTES, HARD, SOFT, Chromosome, RouteEvaluation, Vehicle, VRPProblem, evaluate

MAX_STORED_RESULTS = 50
MAX_PREVIOUS_RUNS = 5  # execuções anteriores enviadas como contexto à LLM técnica
MODE_LABEL = {HARD: "rígida", SOFT: "flexível"}


def scenario_key(result: OptimizeResponse) -> tuple:
    """Identifica o cenário pelas cidades e por origem/destino: mudou alguma, começa um histórico novo."""
    locs = result.locations
    return tuple(sorted(loc.name for loc in locs)), locs[result.origin_index].name, locs[result.destination_index].name


class InfeasibleProblemError(Exception):
    """Nenhuma solução respeita as restrições hard configuradas."""

    def __init__(self, message: str, reasons: list[str]):
        super().__init__(message)
        self.message = message
        self.reasons = reasons


def fmt_time(minutes: float) -> str:
    """Minutos desde 00:00 -> 'HH:MM' (com '+1d' quando passa da meia-noite)."""
    m = int(round(minutes))
    day, rest = divmod(m, DAY_MINUTES)
    text = f"{rest // 60:02d}:{rest % 60:02d}"
    return f"{text} (+{day}d)" if day else text


def point_along(geometry: list[list[float]], fraction: float) -> tuple[float, float]:
    """Ponto (lat, lng) a uma fração do comprimento da linha (0 = início, 1 = fim)."""
    seg = [haversine_km(tuple(a), tuple(b)) for a, b in zip(geometry, geometry[1:])]
    target = max(0.0, min(1.0, fraction)) * sum(seg)
    for (a, b), km in zip(zip(geometry, geometry[1:]), seg):
        if target <= km and km > 0:
            f = target / km
            return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        target -= km
    return tuple(geometry[-1])


def build_fleet(types: list[str]) -> list[Vehicle]:
    return [Vehicle(type=t, **VEHICLE_TYPES[t]) for t in types]


def _eligible(problem: VRPProblem, stop: int) -> list[Vehicle]:
    """Veículos que podem levar a entrega quanto à refrigeração."""
    if needs_refrigeration(problem.group(stop) or ""):
        return [v for v in problem.vehicles if v.refrigerated]
    return list(problem.vehicles)


def check_hard_constraints(problem: VRPProblem, names: list[str]) -> list[str]:
    """Detecta, antes de rodar o AG, restrições hard impossíveis de cumprir."""
    reasons: list[str] = []
    groups = {problem.group(s) for s in problem.stops} - {None}

    # Contaminação e refrigeração (sempre rígidas).
    refrigerated = [v for v in problem.vehicles if v.refrigerated]
    if any(needs_refrigeration(g) for g in groups) and not refrigerated:
        cities = ", ".join(names[s] for s in problem.stops if needs_refrigeration(problem.group(s) or ""))
        reasons.append(f"Há entregas que exigem refrigeração ({cities}), mas a frota não tem caminhão com refrigeração.")
    alone = [g for g in groups if set(PRODUCT_GROUPS[g]["incompatible_with"]) >= set(PRODUCT_GROUPS) - {g}]
    rest = groups - set(alone)
    needed = len(alone) + (2 if any(incompatible(a, b) for a in rest for b in rest if a < b) else (1 if rest else 0))
    if needed > problem.n_vehicles:
        reasons.append(
            f"As regras de transporte exigem ao menos {needed} veículos separados "
            f"(grupos que viajam sozinhos e grupos incompatíveis), mas a frota tem {problem.n_vehicles}."
        )

    if problem.capacity_mode == HARD:
        for s in problem.stops:
            cap = max((v.capacity for v in _eligible(problem, s)), default=0)
            if cap and problem.demands[s] > cap:
                reasons.append(
                    f"{names[s]} demanda {problem.demands[s]:g} kg, mais que o maior veículo apto ({cap:g} kg)."
                )
        total = sum(problem.demands[s] for s in problem.stops)
        fleet = sum(v.capacity for v in problem.vehicles)
        if total > fleet:
            reasons.append(f"A demanda total ({total:g} kg) é maior que a capacidade da frota ({fleet:g} kg).")
        cold = sum(problem.demands[s] for s in problem.stops if needs_refrigeration(problem.group(s) or ""))
        cold_cap = sum(v.capacity for v in refrigerated)
        if refrigerated and cold > cold_cap:
            reasons.append(f"A carga refrigerada ({cold:g} kg) excede a capacidade dos caminhões refrigerados ({cold_cap:g} kg).")

    for s in problem.stops:
        go, back = problem.distance_km[problem.origin][s], problem.distance_km[s][problem.destination]
        eligible = _eligible(problem, s)

        def reaches(v: Vehicle) -> bool:
            if problem.autonomy_mode == SOFT and not v.strict_autonomy:
                return True
            # Com abastecimento (na cidade e no caminho), qualquer trecho é alcançável.
            return True if problem.can_refuel(v) else go + back <= v.autonomy_km

        if eligible and not any(reaches(v) for v in eligible):
            detail = f"ida e volta somam {go + back:.0f} km"
            reasons.append(
                f"Atender {names[s]} é impossível: {detail}, acima da autonomia de todos os veículos aptos "
                f"(máx. {max(v.autonomy_km for v in eligible):g} km)."
            )

    if problem.time_mode == HARD and problem.windows:
        for s in problem.stops:
            earliest = problem.departure_min + problem.duration_min[problem.origin][s]
            if earliest > problem.windows[s][1]:
                reasons.append(
                    f"{names[s]}: mesmo indo direto, a chegada seria às {fmt_time(earliest)}, "
                    f"depois do fim da janela ({fmt_time(problem.windows[s][1])})."
                )
    return reasons


class OptimizerService:
    def __init__(self, distance_service: DistanceService):
        self.distances = distance_service
        self._results: OrderedDict[str, OptimizeResponse] = OrderedDict()

    def get_result(self, result_id: str) -> OptimizeResponse | None:
        return self._results.get(result_id)

    def previous_runs(self, result: OptimizeResponse) -> list[OptimizeResponse]:
        """Execuções anteriores (ainda em memória) que servem de contexto para a LLM técnica."""
        return [r for rid in result.previous_result_ids if (r := self._results.get(rid))]

    def _history(self, result: OptimizeResponse, ids: list[str]) -> list[OptimizeResponse]:
        """Das execuções informadas, só as com as mesmas cidades, origem e destino (as mais recentes)."""
        key = scenario_key(result)
        same = [r for rid in ids if (r := self._results.get(rid)) and scenario_key(r) == key]
        return same[-MAX_PREVIOUS_RUNS:]

    def _store(self, result: OptimizeResponse) -> None:
        self._results[result.id] = result
        while len(self._results) > MAX_STORED_RESULTS:
            self._results.popitem(last=False)

    def optimize(self, req: OptimizeRequest) -> OptimizeResponse:
        locations = req.locations
        coords = [(loc.lat, loc.lng) for loc in locations]
        dist, dur, source = self.distances.matrix(coords)
        started = time.perf_counter()

        problem = VRPProblem(
            distance_km=dist,
            duration_min=dur,
            demands=[loc.demand for loc in locations],
            priorities=[loc.priority for loc in locations],
            origin=req.origin_index,
            destination=req.destination_index,
            vehicles=build_fleet([v.type for v in req.vehicles]),
            groups=[loc.product_group for loc in locations],
            windows=[(hhmm_to_min(loc.window_start), hhmm_to_min(loc.window_end)) for loc in locations],
            departure_min=hhmm_to_min(req.schedule.departure_time),
            service_min=req.schedule.service_minutes,
            refuel_enabled=req.schedule.refuel_enabled,
            refuel_reserve=req.schedule.refuel_reserve_pct / 100,
            refuel_min=req.schedule.refuel_minutes,
            priority_weight=req.ga.priority_weight,
            capacity_mode=req.constraints.capacity,
            autonomy_mode=req.constraints.autonomy,
            priority_mode=req.constraints.priority,
            time_mode=req.constraints.time_window,
            soft_penalty=req.constraints.soft_penalty,
        )
        names = [loc.name for loc in locations]
        reasons = check_hard_constraints(problem, names)
        if reasons:
            raise InfeasibleProblemError(
                "Não existe solução que respeite as restrições rígidas. "
                "Ajuste a frota ou os parâmetros, ou marque a restrição como flexível (soft).",
                reasons,
            )

        ga = run_genetic_algorithm(
            problem,
            population_size=req.ga.population_size,
            generations=req.ga.generations,
            mutation_probability=req.ga.mutation_probability,
            tournament_size=req.ga.tournament_size,
            elite_size=req.ga.elite_size,
            seed=req.ga.seed,
            stagnation_generations=req.ga.stagnation_generations,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

        fitness, routes, feasible = evaluate(ga.best, problem)
        if not feasible:
            diagnosis = diagnose_cold_chain(problem, names)
            raise InfeasibleProblemError(
                "O algoritmo genético não encontrou uma solução que respeite as restrições rígidas. "
                "Aumente a população ou as gerações, ajuste a frota, ou marque a restrição como flexível (soft).",
                ([diagnosis] if diagnosis else []) + describe_violations(routes, problem, names, HARD),
            )

        vehicles = [self._vehicle_route(vid, r, problem, coords, names) for vid, r in enumerate(routes, 1)]
        result = OptimizeResponse(
            id=uuid.uuid4().hex,
            distance_source=source,
            locations=locations,
            origin_index=req.origin_index,
            destination_index=req.destination_index,
            vehicles=vehicles,
            total_distance_km=round(sum(v.distance_km for v in vehicles), 2),
            total_duration_min=round(sum(v.duration_min for v in vehicles), 1),
            fitness=round(fitness, 2),
            feasible=feasible,
            ga=req.ga,
            constraints=req.constraints,
            schedule=req.schedule,
            transport_rules=TRANSPORT_RULES,
            soft_violations=describe_violations(routes, problem, names, SOFT),
            fitness_history=[round(f, 2) for f in ga.history],
            generations=ga.generations,
            stopped_by_stagnation=ga.stopped_by_stagnation,
            execution_time_ms=round(elapsed_ms, 1),
            comparison=self._compare(problem, ga.best, req.ga.seed),
        )
        history = self._history(result, req.previous_result_ids)
        result.previous_result_ids = [r.id for r in history]
        if req.use_llm:
            result.llm_technical, result.llm_driver = llm.generate_reports(result, history)
        self._store(result)
        return result

    def _vehicle_route(self, vid: int, r: RouteEvaluation, problem: VRPProblem, coords, names: list[str]) -> VehicleRoute:
        path = r.full_path(problem)
        v = r.vehicle
        return VehicleRoute(
            vehicle_id=vid,
            vehicle_type=v.type,
            vehicle_name=v.name,
            refrigerated=v.refrigerated,
            strict_autonomy=v.strict_autonomy,
            stops=path,
            distance_km=round(r.distance_km, 2),
            duration_min=round(r.duration_min, 1),
            load=r.load,
            capacity=v.capacity,
            autonomy_km=v.autonomy_km,
            groups=r.carried_groups,
            schedule=[
                StopSchedule(
                    location=st.location,
                    arrival=fmt_time(st.arrival_min),
                    departure=fmt_time(st.departure_min),
                    wait_min=round(st.wait_min, 1),
                    late_min=round(st.late_min, 1),
                    refuel=st.refuel,
                )
                for st in r.schedule
            ],
            refuels=self._refuel_infos(r, problem, coords, names),
            return_time=fmt_time(r.end_min) if r.stops else None,
            capacity_ok=r.load <= v.capacity,
            autonomy_ok=r.autonomy_excess_km == 0,
            priority_ok=r.priority_inversions == 0,
            contamination_ok=r.contamination == 0 and r.refrigeration == 0,
            time_ok=r.late_min == 0,
            polyline=self.distances.polyline([coords[i] for i in path]) if path else [],
        )

    def _refuel_infos(self, r: RouteEvaluation, problem: VRPProblem, coords, names: list[str]) -> list[RefuelInfo]:
        """Paradas para abastecer, com o local de cada parada no caminho.

        Para paradas no meio de um trecho longo, acha o ponto na geometria real do trecho
        (Routes API; linha reta sem Google) e o município desse ponto (geocodificação reversa).
        """
        en_route = [f for f in r.refuels if f.en_route]
        points: dict[int, tuple[float, float]] = {}
        for leg in {(f.leg_from, f.leg_to) for f in en_route}:
            a, b = leg
            geometry = self.distances.leg_geometry(coords[a], coords[b])
            leg_km = problem.distance_km[a][b]
            for f in en_route:
                if (f.leg_from, f.leg_to) == leg:
                    points[id(f)] = point_along(geometry, f.km_into_leg / leg_km)
        with ThreadPoolExecutor(max_workers=8) as pool:
            places = dict(zip(points, pool.map(lambda pt: self.distances.place_name(*pt), points.values())))

        infos = []
        for f in r.refuels:
            common = dict(time=fmt_time(f.start_min), km_since_refuel=round(f.km_since_refuel, 1),
                          range_left_pct=round(f.range_left_pct, 1))
            if not f.en_route:
                lat, lng = coords[f.location]
                infos.append(RefuelInfo(location=f.location, place=names[f.location], lat=lat, lng=lng, **common))
                continue
            lat, lng = points[id(f)]
            fallback = f"km {f.km_into_leg:.0f} do trecho {names[f.leg_from]} → {names[f.leg_to]}"
            infos.append(RefuelInfo(
                location=None, en_route=True, place=places.get(id(f)) or fallback, leg_from=f.leg_from,
                leg_to=f.leg_to, km_into_leg=round(f.km_into_leg, 1), lat=lat, lng=lng, **common,
            ))
        return infos

    @staticmethod
    def _compare(problem: VRPProblem, best: Chromosome, seed: int | None) -> Comparison:
        def strategy(chromosome: Chromosome) -> StrategyResult:
            fit, routes, ok = evaluate(chromosome, problem)
            return StrategyResult(distance_km=round(sum(r.distance_km for r in routes), 2), fitness=round(fit, 2), feasible=ok)

        ga = strategy(best)
        nn = strategy(nearest_neighbor_solution(problem))
        rnd = strategy(random_average_solution(problem, seed=seed))

        def improvement(other: StrategyResult) -> float:
            return round((other.fitness - ga.fitness) / other.fitness * 100, 2) if other.fitness else 0.0

        return Comparison(
            genetic_algorithm=ga,
            nearest_neighbor=nn,
            random_average=rnd,
            improvement_vs_nearest_neighbor_pct=improvement(nn),
            improvement_vs_random_pct=improvement(rnd),
        )

    def enrich_cities(self, cities: list[dict]) -> list[Location]:
        """Geocodifica as cidades (Google) quando disponível."""
        return [Location(**self.distances.geocode_city(c)) for c in cities]


def _amount(x: float) -> str:
    """Formata excessos: 1 casa decimal abaixo de 10 (evita mostrar "0 km")."""
    return f"{x:.1f}".replace(".", ",") if x < 10 else f"{x:.0f}"


def diagnose_cold_chain(problem: VRPProblem, names: list[str]) -> str | None:
    """Verifica se a carga refrigerada cabe nos veículos refrigerados (capacidade e autonomia).

    Atribuição gulosa (vizinho mais próximo) só com veículos refrigerados; se sobrar
    entrega, explica quais cidades ficaram de fora.
    """
    cold = [s for s in problem.stops if needs_refrigeration(problem.group(s) or "")]
    fridges = [v for v in problem.vehicles if v.refrigerated]
    if not cold or not fridges:
        return None
    d, dest = problem.distance_km, problem.destination
    left = set(cold)
    for v in sorted(fridges, key=lambda v: -v.capacity):
        cur, load, dist = problem.origin, 0.0, 0.0
        range_hard = problem.autonomy_mode == HARD or v.strict_autonomy
        limit_kg = v.capacity if problem.capacity_mode == HARD else float("inf")
        while True:
            ok = [s for s in left if load + problem.demands[s] <= limit_kg
                  and (not range_hard or problem.leg_fits(v, dist, d[cur][s], d[s][dest]))]
            if not ok:
                break
            nxt = min(ok, key=lambda s: d[cur][s])
            left.remove(nxt)
            load, dist, cur = load + problem.demands[nxt], dist + d[cur][nxt], nxt
    if not left:
        return None
    cities = ", ".join(names[s] for s in sorted(left))
    return (
        f"A carga refrigerada não cabe nos {len(fridges)} caminhão(ões) com refrigeração da frota "
        f"(capacidade/autonomia): sobram {cities}. Adicione caminhões refrigerados ou torne a autonomia flexível."
    )


def describe_violations(routes: list[RouteEvaluation], problem: VRPProblem, names: list[str], mode: str) -> list[str]:
    """Descreve, por veículo, as violações das restrições no modo indicado.

    Contaminação, refrigeração e autonomia do carro elétrico são sempre rígidas.
    """
    label = MODE_LABEL[mode]
    out = []
    for vid, r in enumerate(routes, 1):
        if not r.stops:
            continue
        who = f"Veículo {vid} ({r.vehicle.name})"
        if problem.capacity_mode == mode and r.load > r.vehicle.capacity:
            out.append(f"{who}: {_amount(r.load - r.vehicle.capacity)} kg acima da capacidade (restrição {label}).")
        autonomy_mode = HARD if r.vehicle.strict_autonomy else problem.autonomy_mode
        if autonomy_mode == mode and r.autonomy_excess_km > 0:
            extra = " — carro elétrico: sempre rígida" if r.vehicle.strict_autonomy else ""
            out.append(f"{who}: {_amount(r.autonomy_excess_km)} km acima da autonomia (restrição {label}{extra}).")
        if problem.priority_mode == mode and r.priority_inversions:
            late = [names[s] for i, s in enumerate(r.stops)
                    if problem.priorities[s] == "critical" and any(problem.priorities[p] != "critical" for p in r.stops[:i])]
            out.append(f"{who}: entrega crítica depois de regular ({', '.join(late)}) (restrição {label}).")
        if problem.time_mode == mode and r.late_min:
            for st in r.schedule:
                if st.late_min:
                    out.append(
                        f"{who}: chega em {names[st.location]} às {fmt_time(st.arrival_min)}, "
                        f"{st.late_min:.0f} min depois do fim da janela ({fmt_time(problem.windows[st.location][1])}) "
                        f"(restrição {label})."
                    )
        if mode == HARD and r.contamination:
            groups = sorted(set(r.stop_groups))
            out.append(f"{who}: transporta grupos incompatíveis juntos ({', '.join(groups)}) — risco de contaminação.")
        if mode == HARD and r.refrigeration:
            out.append(f"{who}: leva produto que exige refrigeração em veículo sem refrigeração.")
    return out
