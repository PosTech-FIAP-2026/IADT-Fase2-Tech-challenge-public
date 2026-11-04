"""Heurísticas de referência para comparar com o algoritmo genético."""
import random

from app.data.catalog import incompatible, needs_refrigeration
from app.services.vrp import Chromosome, Vehicle, VRPProblem, calculate_fitness, join_routes


def _can_carry(problem: VRPProblem, vehicle: Vehicle, carried: set[str], stop: int) -> bool:
    """O veículo pode levar a entrega sem violar refrigeração nem contaminação?"""
    group = problem.group(stop)
    if group is None:
        return True
    if needs_refrigeration(group) and not vehicle.refrigerated:
        return False
    return not any(incompatible(group, g) for g in carried)


def nearest_neighbor_solution(problem: VRPProblem) -> Chromosome:
    """Vizinho mais próximo, veículo a veículo.

    Os veículos são preenchidos do maior para o menor. Cada um parte da origem e
    visita sempre a entrega mais próxima que ainda cabe (capacidade e autonomia,
    contando o trecho até o destino; com abastecimento, basta cada trecho caber num
    tanque) e é compatível com o que já carrega. Entregas
    que não couberem em nenhum veículo vão para o de maior capacidade.
    """
    unvisited = set(problem.stops)
    routes: list[list[int]] = [[] for _ in problem.vehicles]
    order = sorted(range(problem.n_vehicles), key=lambda i: -problem.vehicles[i].capacity)

    for vi in order:
        vehicle = problem.vehicles[vi]
        current, load, dist, carried = problem.origin, 0.0, 0.0, set()
        while True:
            candidates = [
                s for s in unvisited
                if load + problem.demands[s] <= vehicle.capacity
                and problem.leg_fits(vehicle, dist, problem.distance_km[current][s], problem.distance_km[s][problem.destination])
                and _can_carry(problem, vehicle, carried, s)
            ]
            # Veículo refrigerado atende primeiro a carga que exige refrigeração.
            cold = [s for s in candidates if needs_refrigeration(problem.group(s) or "")]
            if vehicle.refrigerated and cold:
                candidates = cold
            if not candidates:
                break
            nxt = min(candidates, key=lambda s: problem.distance_km[current][s])
            routes[vi].append(nxt)
            unvisited.remove(nxt)
            load += problem.demands[nxt]
            dist += problem.distance_km[current][nxt]
            if problem.group(nxt):
                carried.add(problem.group(nxt))
            current = nxt

    if unvisited:  # sobrou entrega: solução inviável, mas completa
        routes[order[0]].extend(sorted(unvisited, key=lambda s: problem.distance_km[problem.origin][s]))
    return join_routes(routes)


def random_average_solution(problem: VRPProblem, samples: int = 200, seed: int | None = None) -> Chromosome:
    """Retorna a solução aleatória com fitness mais próximo da média de `samples` sorteios."""
    rng = random.Random(seed)
    genes = problem.stops + problem.separators
    candidates = [rng.sample(genes, len(genes)) for _ in range(samples)]
    scores = [calculate_fitness(c, problem) for c in candidates]
    mean = sum(scores) / len(scores)
    return min(zip(candidates, scores), key=lambda cs: abs(cs[1] - mean))[0]
