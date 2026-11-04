"""Modelo do problema de roteamento (VRP) e função fitness.

Representação genética
----------------------
Cada indivíduo é uma permutação contendo:
  * os índices (>= 0) dos pontos de entrega, e
  * K-1 separadores com valores negativos únicos (-1, -2, ...),
onde K é o número de veículos. Os separadores dividem a permutação em K rotas,
e a i-ésima rota é feita pelo i-ésimo veículo da frota (frota heterogênea).

Exemplo com 5 entregas e 2 veículos: [3, 1, -1, 4, 0, 2]
  -> veículo 1: origem -> 3 -> 1 -> destino
  -> veículo 2: origem -> 4 -> 0 -> 2 -> destino

Como todos os genes são únicos, os operadores clássicos de permutação do código
base (order crossover, swap) continuam válidos sem gerar indivíduos inválidos.
"""
from collections import Counter
from dataclasses import dataclass, field

from app.data.catalog import INCOMPATIBLE_PAIRS, needs_refrigeration

# Restrições "hard" (rígidas): qualquer violação domina o fitness, então toda solução
# que respeita as restrições rígidas é melhor que qualquer uma que as viole.
HARD_PENALTY = 1_000_000.0
PRIORITY_WEIGHTS = {"critical": 1.0, "regular": 0.0}
SOFT, HARD = "soft", "hard"
DAY_MINUTES = 24 * 60

Chromosome = list[int]


@dataclass(frozen=True)
class Vehicle:
    type: str
    name: str
    capacity: float
    autonomy_km: float
    refrigerated: bool = False
    strict_autonomy: bool = False  # autonomia sempre rígida (ex.: carro elétrico)


def uniform_fleet(n: int, capacity: float, autonomy_km: float) -> list[Vehicle]:
    """Frota de veículos iguais (usada em testes e experimentos)."""
    return [Vehicle("generico", "Veículo", capacity, autonomy_km) for _ in range(n)]


@dataclass
class VRPProblem:
    distance_km: list[list[float]]   # matriz NxN de distâncias (km)
    duration_min: list[list[float]]  # matriz NxN de durações (min)
    demands: list[float]
    priorities: list[str]
    origin: int
    destination: int
    vehicles: list[Vehicle]
    groups: list[str] | None = None                         # grupo de medicamento/insumo por local
    windows: list[tuple[float, float]] | None = None        # janela de recebimento (min desde 00:00)
    departure_min: float = 7 * 60                           # horário de saída da origem
    service_min: float = 15                                 # tempo de descarga em cada entrega
    # Abastecimento em rota: todos saem de tanque cheio e abastecem numa cidade da rota antes do
    # trecho que deixaria a autonomia abaixo da reserva (ex.: 15%). Carro elétrico não abastece.
    refuel_enabled: bool = False
    refuel_reserve: float = 0.15                            # fração da autonomia que dispara o abastecimento
    refuel_min: float = 15                                  # duração de cada abastecimento
    priority_weight: float = 0.5
    capacity_mode: str = HARD
    autonomy_mode: str = HARD
    priority_mode: str = SOFT
    time_mode: str = SOFT
    soft_penalty: float = 50.0  # custo (km equivalentes) por kg/km/min excedido em restrição soft
    stops: list[int] = field(init=False)

    def __post_init__(self):
        n = len(self.distance_km)
        self.stops = [i for i in range(n) if i not in (self.origin, self.destination)]

    @property
    def n_vehicles(self) -> int:
        return len(self.vehicles)

    @property
    def separators(self) -> list[int]:
        return [-(i + 1) for i in range(self.n_vehicles - 1)]

    def group(self, i: int) -> str | None:
        return self.groups[i] if self.groups else None

    def can_refuel(self, vehicle: "Vehicle") -> bool:
        """Veículos com autonomia estrita (carro elétrico) não abastecem no caminho."""
        return self.refuel_enabled and not vehicle.strict_autonomy

    def leg_fits(self, vehicle: "Vehicle", km_since_start: float, leg_km: float, next_leg_km: float) -> bool:
        """O veículo consegue fazer o trecho (e o seguinte) respeitando a autonomia?

        Com abastecimento, sempre: abastece nas cidades e, se preciso, no caminho.
        Sem abastecimento, a soma do percurso precisa caber numa única autonomia.
        """
        if self.can_refuel(vehicle):
            return True
        return km_since_start + leg_km + next_leg_km <= vehicle.autonomy_km


@dataclass
class StopTime:
    location: int
    arrival_min: float
    wait_min: float
    late_min: float
    departure_min: float
    refuel: bool = False  # abastece nesta cidade antes de seguir


@dataclass
class RefuelStop:
    """Parada para abastecer: numa cidade da rota ou no caminho de um trecho longo."""

    location: int | None      # cidade da rota onde abastece (None = no caminho)
    start_min: float          # início do abastecimento
    km_since_refuel: float    # km rodados desde o último tanque cheio
    range_left_pct: float     # autonomia restante ao parar (% do tanque)
    leg_from: int | None = None   # trecho em que fica a parada no caminho
    leg_to: int | None = None
    km_into_leg: float = 0.0      # distância desde o início do trecho

    @property
    def en_route(self) -> bool:
        return self.location is None


@dataclass
class RouteEvaluation:
    stops: list[int]  # somente entregas (sem origem/destino)
    vehicle: Vehicle
    distance_km: float
    duration_min: float  # da saída até a chegada ao destino, com esperas e descargas
    load: float
    priority_cost: float
    priority_inversions: int = 0   # entregas críticas visitadas depois de alguma regular
    contamination: int = 0         # entregas em conflito de compatibilidade no mesmo veículo
    refrigeration: int = 0         # entregas refrigeradas em veículo sem refrigeração
    late_min: float = 0.0          # minutos de atraso somados nas janelas de recebimento
    schedule: list[StopTime] = field(default_factory=list)
    end_min: float = 0.0           # horário de chegada ao destino
    stop_groups: list[str] = field(default_factory=list)  # grupo de cada entrega, na ordem
    refuels: list[RefuelStop] = field(default_factory=list)
    autonomy_excess_km: float = 0.0  # km além da autonomia (só sem abastecimento: elétrico ou opção desligada)

    def full_path(self, problem: VRPProblem) -> list[int]:
        if not self.stops:
            return []
        return [problem.origin, *self.stops, problem.destination]

    @property
    def carried_groups(self) -> list[str]:
        """Grupos de medicamentos/insumos transportados pelo veículo (sem repetição)."""
        return sorted(set(self.stop_groups))


def split_routes(chromosome: Chromosome) -> list[list[int]]:
    """Divide o cromossomo nas rotas de cada veículo usando os separadores."""
    routes: list[list[int]] = [[]]
    for gene in chromosome:
        if gene < 0:
            routes.append([])
        else:
            routes[-1].append(gene)
    return routes


def join_routes(routes: list[list[int]]) -> Chromosome:
    """Operação inversa de split_routes."""
    chromosome: Chromosome = []
    for i, route in enumerate(routes):
        if i > 0:
            chromosome.append(-i)
        chromosome.extend(route)
    return chromosome


def count_priority_inversions(stops: list[int], problem: VRPProblem) -> int:
    """Quantas entregas críticas aparecem depois de uma entrega regular no mesmo veículo."""
    seen_regular, inversions = False, 0
    for stop in stops:
        if problem.priorities[stop] == "critical":
            inversions += seen_regular
        else:
            seen_regular = True
    return inversions


def count_contamination(groups: list[str]) -> int:
    """Entregas a remanejar para eliminar pares incompatíveis no mesmo veículo.

    Para cada par incompatível presente (ex.: A e C), conta o menor dos dois grupos,
    o que dá ao AG um sinal gradual de quantas entregas estão no veículo errado.
    """
    counts = Counter(groups)
    return sum(min(counts[a], counts[b]) for a, b in INCOMPATIBLE_PAIRS if counts[a] and counts[b])


def evaluate_route(stops: list[int], problem: VRPProblem, vehicle: Vehicle) -> RouteEvaluation:
    if not stops:
        return RouteEvaluation([], vehicle, 0.0, 0.0, 0.0, 0.0, end_min=problem.departure_min)

    d, dur = problem.distance_km, problem.duration_min
    refuel_ok = problem.can_refuel(vehicle)
    usable_km = vehicle.autonomy_km * (1 - problem.refuel_reserve)

    dist, priority_cost, late = 0.0, 0.0, 0.0
    tank_km = 0.0  # km rodados desde o último tanque cheio (saída = tanque cheio)
    t = problem.departure_min
    schedule: list[StopTime] = []
    refuels: list[RefuelStop] = []
    excess = 0.0
    current = problem.origin
    # Percorre os trechos origem -> entregas -> destino (o destino nunca é uma entrega).
    for i, stop in enumerate([*stops, problem.destination]):
        leg, leg_min = d[current][stop], dur[current][stop]
        en_route_min = 0.0
        if refuel_ok:
            # 1) Se o trecho deixaria a autonomia abaixo da reserva, abastece antes na cidade atual.
            if tank_km > 0 and tank_km + leg > usable_km:
                left_pct = max(0.0, (vehicle.autonomy_km - tank_km) / vehicle.autonomy_km * 100)
                refuels.append(RefuelStop(current, t, tank_km, left_pct))
                t += problem.refuel_min
                schedule[-1].refuel = True
                schedule[-1].departure_min = t
                tank_km = 0.0
            # 2) Trecho maior que a autonomia útil: abastece no caminho a cada vez que chega à reserva.
            km_into = 0.0
            while tank_km + (leg - km_into) > usable_km:
                km_into += usable_km - tank_km
                start = t + leg_min * km_into / leg + en_route_min
                refuels.append(RefuelStop(None, start, usable_km, problem.refuel_reserve * 100,
                                          leg_from=current, leg_to=stop, km_into_leg=km_into))
                en_route_min += problem.refuel_min
                tank_km = 0.0
            tank_km += leg - km_into
        else:
            tank_km += leg
        dist += leg
        t += leg_min + en_route_min
        if i == len(stops):  # chegou ao destino final
            break
        # Entregas críticas atendidas "mais tarde" (maior km acumulado) custam mais.
        priority_cost += PRIORITY_WEIGHTS.get(problem.priorities[stop], 0.0) * dist
        wait = stop_late = 0.0
        if problem.windows:
            start, end = problem.windows[stop]
            if t < start:  # chegou antes da janela: espera abrir
                wait = start - t
            elif t > end:  # chegou depois da janela: atraso
                stop_late = t - end
        arrival = t
        t += wait + problem.service_min
        late += stop_late
        schedule.append(StopTime(stop, arrival, wait, stop_late, t))
        current = stop
    if not refuel_ok:
        excess = max(0.0, dist - vehicle.autonomy_km)

    groups = [problem.group(s) for s in stops] if problem.groups else []
    refrigeration = sum(1 for g in groups if needs_refrigeration(g)) if groups and not vehicle.refrigerated else 0
    return RouteEvaluation(
        stops=list(stops),
        vehicle=vehicle,
        distance_km=dist,
        duration_min=t - problem.departure_min,
        load=sum(problem.demands[s] for s in stops),
        priority_cost=priority_cost,
        priority_inversions=count_priority_inversions(stops, problem),
        contamination=count_contamination(groups) if groups else 0,
        refrigeration=refrigeration,
        late_min=late,
        schedule=schedule,
        end_min=t,
        stop_groups=groups,
        refuels=refuels,
        autonomy_excess_km=excess,
    )


@dataclass
class Violations:
    capacity_kg: float = 0.0
    autonomy_km: float = 0.0          # veículos sem autonomia estrita
    strict_autonomy_km: float = 0.0   # veículos com autonomia sempre rígida (carro elétrico)
    priority_inversions: int = 0
    contamination: int = 0
    refrigeration: int = 0
    late_min: float = 0.0

    def split(self, problem: VRPProblem) -> tuple[float, float]:
        """(violações soft, violações hard). Prioridade soft é tratada só pelo peso."""
        soft = hard = 0.0
        for mode, amount in (
            (problem.capacity_mode, self.capacity_kg),
            (problem.autonomy_mode, self.autonomy_km),
            (problem.time_mode, self.late_min),
        ):
            if mode == HARD:
                hard += amount
            else:
                soft += amount
        if problem.priority_mode == HARD:
            hard += self.priority_inversions
        # Sempre rígidas: segurança sanitária e limite físico do carro elétrico.
        hard += self.contamination + self.refrigeration + self.strict_autonomy_km
        return soft, hard


def violations(routes: list[RouteEvaluation]) -> Violations:
    v = Violations()
    for r in routes:
        v.capacity_kg += max(0.0, r.load - r.vehicle.capacity)
        excess = r.autonomy_excess_km
        if r.vehicle.strict_autonomy:
            v.strict_autonomy_km += excess
        else:
            v.autonomy_km += excess
        v.priority_inversions += r.priority_inversions
        v.contamination += r.contamination
        v.refrigeration += r.refrigeration
        v.late_min += r.late_min
    return v


def evaluate(chromosome: Chromosome, problem: VRPProblem) -> tuple[float, list[RouteEvaluation], bool]:
    """Retorna (fitness, rotas avaliadas, factível). Fitness menor é melhor.

    fitness = distância total
            + priority_weight * Σ(km acumulado até cada entrega crítica)
            + soft_penalty * Σ(violações das restrições soft)
            + HARD_PENALTY * Σ(violações das restrições hard)

    Violações: kg acima da capacidade, km acima da autonomia, minutos de atraso nas
    janelas de recebimento, entregas críticas depois de regulares (prioridade hard),
    conflitos de contaminação e produtos refrigerados em veículo sem refrigeração.
    "Factível" significa que nenhuma restrição hard foi violada.
    """
    routes = [evaluate_route(r, problem, problem.vehicles[i]) for i, r in enumerate(split_routes(chromosome))]
    total_distance = sum(r.distance_km for r in routes)
    priority_cost = sum(r.priority_cost for r in routes)
    soft, hard = violations(routes).split(problem)
    fitness = total_distance + problem.priority_weight * priority_cost + problem.soft_penalty * soft + HARD_PENALTY * hard
    return fitness, routes, hard == 0


def calculate_fitness(chromosome: Chromosome, problem: VRPProblem) -> float:
    return evaluate(chromosome, problem)[0]
