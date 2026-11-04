"""Experimentos com diferentes configurações do algoritmo genético.

Uso (a partir de backend/):
    python -m scripts.experiments --cities 20 --vehicles 3 --runs 3

Compara AG (várias configurações) com vizinho mais próximo e solução aleatória.
Usa Google Maps se GOOGLE_MAPS_API_KEY estiver no .env; senão, haversine.
"""
import argparse
import random
import statistics
import time

from app.data.cities import sample_random_cities
from app.services.distance import build_distance_service
from app.services.genetic_algorithm import run_genetic_algorithm
from app.services.heuristics import nearest_neighbor_solution, random_average_solution
from app.services.vrp import VRPProblem, evaluate, uniform_fleet

CONFIGS = [
    {"name": "pop50_gen200_mut0.1", "population_size": 50, "generations": 200, "mutation_probability": 0.1},
    {"name": "pop100_gen300_mut0.3", "population_size": 100, "generations": 300, "mutation_probability": 0.3},
    {"name": "pop200_gen500_mut0.5", "population_size": 200, "generations": 500, "mutation_probability": 0.5},
    {"name": "pop100_gen300_mut0.8", "population_size": 100, "generations": 300, "mutation_probability": 0.8},
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cities", type=int, default=15)
    parser.add_argument("--vehicles", type=int, default=3)
    parser.add_argument("--capacity", type=float, default=200)
    parser.add_argument("--autonomy", type=float, default=800)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    cities = sample_random_cities(args.cities, random.Random(args.seed))
    service = build_distance_service()
    dist, dur, source = service.matrix([(c["lat"], c["lng"]) for c in cities])
    problem = VRPProblem(
        distance_km=dist,
        duration_min=dur,
        demands=[c["demand"] for c in cities],
        priorities=[c["priority"] for c in cities],
        origin=0,
        destination=0,
        vehicles=uniform_fleet(args.vehicles, args.capacity, args.autonomy),
    )
    print(f"Cidades: {args.cities} | origem: {cities[0]['name']} | fonte das distâncias: {source}\n")
    print(f"{'Abordagem':<26}{'Fitness médio':>15}{'Melhor':>12}{'Distância (km)':>16}{'Factível':>10}{'Tempo (ms)':>12}")

    def row(name, fitnesses, distances, feasible, ms):
        print(f"{name:<26}{statistics.mean(fitnesses):>15.1f}{min(fitnesses):>12.1f}"
              f"{statistics.mean(distances):>16.1f}{feasible:>10}{ms:>12.0f}")

    for strategy_name, chromosome in (
        ("Vizinho mais próximo", nearest_neighbor_solution(problem)),
        ("Aleatória (média)", random_average_solution(problem, seed=args.seed)),
    ):
        fit, routes, ok = evaluate(chromosome, problem)
        row(strategy_name, [fit], [sum(r.distance_km for r in routes)], str(ok), 0)

    for cfg in CONFIGS:
        fits, dists, oks, times = [], [], [], []
        for run in range(args.runs):
            t0 = time.perf_counter()
            result = run_genetic_algorithm(
                problem,
                population_size=cfg["population_size"],
                generations=cfg["generations"],
                mutation_probability=cfg["mutation_probability"],
                seed=args.seed + run,
            )
            times.append((time.perf_counter() - t0) * 1000)
            fit, routes, ok = evaluate(result.best, problem)
            fits.append(fit)
            dists.append(sum(r.distance_km for r in routes))
            oks.append(ok)
        row(f"AG {cfg['name']}", fits, dists, f"{sum(oks)}/{len(oks)}", statistics.mean(times))


if __name__ == "__main__":
    main()
