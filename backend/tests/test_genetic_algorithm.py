import random

from app.services.distance import haversine_matrix
from app.services.genetic_algorithm import (
    inversion_mutation,
    mutate,
    order_crossover,
    run_genetic_algorithm,
    swap_mutation,
)
from app.services.heuristics import nearest_neighbor_solution
from app.services.vrp import VRPProblem, calculate_fitness, evaluate, join_routes, split_routes, uniform_fleet


def make_problem(n_stops=8, vehicles=2, capacity=1000.0, autonomy=10_000.0, seed=1):
    rng = random.Random(seed)
    coords = [(-23.5 + rng.uniform(-1, 1), -46.6 + rng.uniform(-1, 1)) for _ in range(n_stops + 1)]
    dist, dur = haversine_matrix(coords)
    return VRPProblem(
        distance_km=dist,
        duration_min=dur,
        demands=[0] + [10] * n_stops,
        priorities=["regular"] + ["critical" if i % 3 == 0 else "regular" for i in range(n_stops)],
        origin=0,
        destination=0,
        vehicles=uniform_fleet(vehicles, capacity, autonomy),
    )


def is_valid(chromosome, problem):
    return sorted(chromosome) == sorted(problem.stops + problem.separators)


def test_split_and_join_are_inverse():
    routes = [[3, 1], [], [4, 0, 2]]
    assert split_routes(join_routes(routes)) == routes


def test_order_crossover_keeps_permutation():
    rng = random.Random(0)
    p1 = [1, 2, 3, 4, 5, -1, 6]
    p2 = [6, -1, 5, 4, 3, 2, 1]
    for _ in range(100):
        assert sorted(order_crossover(p1, p2, rng)) == sorted(p1)


def test_mutations_keep_permutation():
    rng = random.Random(0)
    sol = [1, 2, 3, -1, 4, 5]
    for _ in range(100):
        assert sorted(swap_mutation(sol, rng)) == sorted(sol)
        assert sorted(inversion_mutation(sol, rng)) == sorted(sol)
    assert mutate(sol, 0.0, rng) == sol


def test_nearest_neighbor_is_valid():
    problem = make_problem()
    assert is_valid(nearest_neighbor_solution(problem), problem)


def test_ga_is_not_worse_than_nearest_neighbor():
    problem = make_problem(n_stops=10)
    result = run_genetic_algorithm(problem, population_size=60, generations=150, seed=42)
    assert is_valid(result.best, problem)
    assert result.best_fitness <= calculate_fitness(nearest_neighbor_solution(problem), problem) + 1e-9
    # elitismo: o melhor fitness nunca piora entre gerações
    assert all(b <= a + 1e-9 for a, b in zip(result.history, result.history[1:]))


def test_capacity_constraint_forces_multiple_vehicles():
    # 8 entregas de 10 kg, capacidade 40 kg -> precisa de 2 veículos
    problem = make_problem(n_stops=8, vehicles=2, capacity=40)
    result = run_genetic_algorithm(problem, population_size=80, generations=200, seed=7)
    _, routes, feasible = evaluate(result.best, problem)
    assert feasible
    assert all(r.load <= 40 for r in routes)
    assert sum(1 for r in routes if r.stops) == 2


def test_autonomy_violation_is_penalized():
    problem = make_problem(n_stops=4, vehicles=1, autonomy=1.0)
    fitness, _, feasible = evaluate(problem.stops, problem)
    assert not feasible
    assert fitness > 1000


def test_priority_prefers_critical_first():
    # Mesma distância total nas duas ordens; com peso de prioridade, a crítica deve vir primeiro.
    dist = [[0, 10, 20], [10, 0, 25], [20, 25, 0]]
    problem = VRPProblem(
        distance_km=dist,
        duration_min=dist,
        demands=[0, 1, 1],
        priorities=["regular", "regular", "critical"],
        origin=0,
        destination=0,
        vehicles=uniform_fleet(1, 10, 1000),
        priority_weight=5.0,
    )
    assert calculate_fitness([2, 1], problem) < calculate_fitness([1, 2], problem)


def test_open_route_with_distinct_destination():
    problem = make_problem(n_stops=5, vehicles=1)
    p = VRPProblem(
        distance_km=problem.distance_km,
        duration_min=problem.duration_min,
        demands=problem.demands,
        priorities=problem.priorities,
        origin=0,
        destination=3,
        vehicles=uniform_fleet(1, 1000, 10_000),
    )
    assert 0 not in p.stops and 3 not in p.stops
    result = run_genetic_algorithm(p, population_size=30, generations=30, seed=1)
    _, routes, _ = evaluate(result.best, p)
    assert routes[0].full_path(p)[0] == 0 and routes[0].full_path(p)[-1] == 3


def test_stagnation_stops_early():
    problem = make_problem(n_stops=6)
    full = run_genetic_algorithm(problem, population_size=30, generations=300, seed=1, stagnation_generations=0)
    early = run_genetic_algorithm(problem, population_size=30, generations=300, seed=1, stagnation_generations=20)
    assert full.generations == 300 and not full.stopped_by_stagnation
    assert early.stopped_by_stagnation and early.generations < 300
    assert len(early.history) == early.generations + 1
    # As últimas 20 gerações não melhoraram o melhor fitness.
    assert early.history[-1] == early.history[-21]
    assert early.best_fitness == early.history[-1]


def test_same_seed_gives_same_result():
    problem = make_problem(n_stops=8)
    a = run_genetic_algorithm(problem, population_size=30, generations=50, seed=42)
    b = run_genetic_algorithm(problem, population_size=30, generations=50, seed=42)
    assert a.best == b.best and a.history == b.history
