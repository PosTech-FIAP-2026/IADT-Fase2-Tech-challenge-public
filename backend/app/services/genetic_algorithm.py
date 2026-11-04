"""Algoritmo genético para o VRP médico.

Adaptado de https://github.com/FIAP/genetic_algorithm_tsp:
  * order_crossover e a mutação por troca vêm do código base;
  * a mutação ganhou a variante de inversão de segmento (TODO do código base);
  * a população inicial é semeada com a heurística do vizinho mais próximo
    (TODO do código base);
  * seleção por torneio + elitismo configurável;
  * o fitness deixou de ser só distância euclidiana e passou a considerar
    distância real, prioridade, capacidade, autonomia, janelas de horário,
    compatibilidade dos medicamentos/insumos e frota heterogênea;
  * operador de reparo: entregas que quebram regras de contaminação ou
    refrigeração são movidas para o veículo compatível mais barato.
"""
import random
from dataclasses import dataclass

from app.data.catalog import incompatible, needs_refrigeration
from app.services.heuristics import nearest_neighbor_solution
from app.services.vrp import Chromosome, VRPProblem, calculate_fitness, join_routes, split_routes


def generate_random_population(problem: VRPProblem, population_size: int, rng: random.Random) -> list[Chromosome]:
    genes = problem.stops + problem.separators
    return [rng.sample(genes, len(genes)) for _ in range(population_size)]


def _insertion_cost(route: list[int], pos: int, stop: int, problem: VRPProblem) -> float:
    d = problem.distance_km
    prev = route[pos - 1] if pos > 0 else problem.origin
    nxt = route[pos] if pos < len(route) else problem.destination
    return d[prev][stop] + d[stop][nxt] - d[prev][nxt]


def _route_length(route: list[int], problem: VRPProblem) -> float:
    d = problem.distance_km
    path = [problem.origin, *route, problem.destination]
    return sum(d[a][b] for a, b in zip(path, path[1:])) if route else 0.0


def repair(chromosome: Chromosome, problem: VRPProblem) -> Chromosome:
    """Move para outro veículo as entregas que violam contaminação ou refrigeração.

    Em cada veículo, as entregas são lidas na ordem: uma entrega que exige refrigeração
    num veículo comum, ou que é incompatível com algo já carregado, é retirada e
    reinserida no veículo compatível e na posição que menos aumentam a distância
    (preferindo onde ainda cabe na capacidade e na autonomia). Sem veículo compatível, ela volta
    ao veículo original e a violação continua penalizada no fitness.
    """
    if not problem.groups:
        return chromosome
    group = problem.group
    routes = split_routes(chromosome)
    misplaced: list[tuple[int, int]] = []  # (entrega, veículo de origem)
    for vi, route in enumerate(routes):
        vehicle = problem.vehicles[vi]
        kept: list[int] = []
        for s in route:
            g = group(s)
            if (needs_refrigeration(g) and not vehicle.refrigerated) or any(incompatible(g, group(k)) for k in kept):
                misplaced.append((s, vi))
            else:
                kept.append(s)
        routes[vi] = kept
    if not misplaced:
        return chromosome

    for s, origin_vi in misplaced:
        g = group(s)
        best = None  # (estoura capacidade/autonomia?, custo, veículo, posição)
        for vi, route in enumerate(routes):
            vehicle = problem.vehicles[vi]
            if needs_refrigeration(g) and not vehicle.refrigerated:
                continue
            if any(incompatible(g, group(k)) for k in route):
                continue
            over_cap = sum(problem.demands[k] for k in route) + problem.demands[s] > vehicle.capacity
            length = _route_length(route, problem)
            refuels = problem.can_refuel(vehicle)
            for pos in range(len(route) + 1):
                delta = _insertion_cost(route, pos, s, problem)
                # Com abastecimento (nas cidades e no caminho), a autonomia não limita a inserção.
                over_range = not refuels and length + delta > vehicle.autonomy_km
                over = over_cap or over_range
                key = (over, delta, vi, pos)
                if best is None or key < best:
                    best = key
        if best is None:
            routes[origin_vi].append(s)
        else:
            routes[best[2]].insert(best[3], s)
    return join_routes(routes)


def order_crossover(parent1: Chromosome, parent2: Chromosome, rng: random.Random) -> Chromosome:
    """Order Crossover (OX) — mesma lógica do código base."""
    length = len(parent1)
    if length < 2:
        return list(parent1)
    start_index = rng.randint(0, length - 1)
    end_index = rng.randint(start_index + 1, length)

    child = parent1[start_index:end_index]
    in_child = set(child)
    remaining_positions = [i for i in range(length) if i < start_index or i >= end_index]
    remaining_genes = [gene for gene in parent2 if gene not in in_child]
    for position, gene in zip(remaining_positions, remaining_genes):
        child.insert(position, gene)
    return child


def swap_mutation(solution: Chromosome, rng: random.Random) -> Chromosome:
    mutated = list(solution)
    i, j = rng.sample(range(len(mutated)), 2)
    mutated[i], mutated[j] = mutated[j], mutated[i]
    return mutated


def inversion_mutation(solution: Chromosome, rng: random.Random) -> Chromosome:
    """Inverte um segmento (equivalente a um movimento 2-opt)."""
    mutated = list(solution)
    i, j = sorted(rng.sample(range(len(mutated)), 2))
    mutated[i : j + 1] = reversed(mutated[i : j + 1])
    return mutated


def mutate(solution: Chromosome, mutation_probability: float, rng: random.Random) -> Chromosome:
    if len(solution) < 2 or rng.random() >= mutation_probability:
        return list(solution)
    if rng.random() < 0.5:
        return swap_mutation(solution, rng)
    return inversion_mutation(solution, rng)


def tournament_selection(population: list[Chromosome], fitness: list[float], k: int, rng: random.Random) -> Chromosome:
    contenders = rng.sample(range(len(population)), min(k, len(population)))
    best = min(contenders, key=lambda idx: fitness[idx])
    return population[best]


def sort_population(population: list[Chromosome], fitness: list[float]) -> tuple[list[Chromosome], list[float]]:
    order = sorted(range(len(population)), key=lambda i: fitness[i])
    return [population[i] for i in order], [fitness[i] for i in order]


@dataclass
class GAResult:
    best: Chromosome
    best_fitness: float
    history: list[float]
    generations: int                 # gerações efetivamente executadas (len(history) - 1)
    stopped_by_stagnation: bool = False


def run_genetic_algorithm(
    problem: VRPProblem,
    population_size: int = 100,
    generations: int = 300,
    mutation_probability: float = 0.3,
    tournament_size: int = 3,
    elite_size: int = 2,
    seed: int | None = None,
    stagnation_generations: int = 0,
) -> GAResult:
    """Executa o AG.

    stagnation_generations: critério de parada antecipada; encerra quando o melhor fitness
    fica esse número de gerações sem melhorar (0 = desativado, roda todas as gerações).
    """
    rng = random.Random(seed)
    population = [repair(ind, problem) for ind in generate_random_population(problem, population_size - 1, rng)]
    population.append(nearest_neighbor_solution(problem))

    history: list[float] = []
    elite_size = min(elite_size, population_size)
    best_so_far = float("inf")
    without_improvement = 0

    for _ in range(generations):
        fitness = [calculate_fitness(ind, problem) for ind in population]
        population, fitness = sort_population(population, fitness)
        history.append(fitness[0])

        if fitness[0] < best_so_far - 1e-9:
            best_so_far, without_improvement = fitness[0], 0
        else:
            without_improvement += 1
        if stagnation_generations and without_improvement >= stagnation_generations:
            # A população já está ordenada: o melhor indivíduo é o primeiro.
            return GAResult(best=population[0], best_fitness=fitness[0], history=history,
                            generations=len(history) - 1, stopped_by_stagnation=True)

        new_population = population[:elite_size]  # ELITISMO
        while len(new_population) < population_size:
            parent1 = tournament_selection(population, fitness, tournament_size, rng)
            parent2 = tournament_selection(population, fitness, tournament_size, rng)
            child = order_crossover(parent1, parent2, rng)
            child = mutate(child, mutation_probability, rng)
            child = repair(child, problem)
            new_population.append(child)
        population = new_population

    fitness = [calculate_fitness(ind, problem) for ind in population]
    population, fitness = sort_population(population, fitness)
    history.append(fitness[0])
    return GAResult(best=population[0], best_fitness=fitness[0], history=history, generations=len(history) - 1)
