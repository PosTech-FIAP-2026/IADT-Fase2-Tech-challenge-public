"""Integração com LLM via OpenRouter (API compatível com OpenAI Chat Completions).

Para cada otimização são gerados dois textos, com públicos diferentes:
  * técnico (equipe que testa o sistema): diagnóstico da execução, convergência do
    algoritmo genético e sugestões de ajuste de parâmetros com justificativa;
  * motoristas: instruções práticas e sem jargão para quem faz o trajeto.
As perguntas em linguagem natural também são separadas por público.
Os dois recebem o mesmo contexto completo: parâmetros executados, frota inteira
(inclusive veículos não usados), cidades, rotas, horários e comparativo.
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import httpx

from app.config import settings
from app.schemas import LLMOutput, OptimizeResponse

logger = logging.getLogger(__name__)

Audience = Literal["technical", "driver"]

_COMMON_RULES = """- Use EXCLUSIVAMENTE os dados do JSON fornecido. Não invente distâncias, horários, cidades, cargas ou veículos.
- Escreva em português do Brasil, em Markdown.
- Distâncias em km e tempos em minutos/horas, arredondados."""

SYSTEM_TECHNICAL = f"""Você é um especialista em otimização de rotas (VRP) e algoritmos genéticos, analisando uma \
execução do sistema para a equipe de desenvolvimento que está testando e calibrando os parâmetros.
Regras:
{_COMMON_RULES}
- Pode usar termos técnicos (fitness, convergência, mutação, torneio, elitismo, restrição hard/soft).
- Toda sugestão deve citar o valor atual do parâmetro (em "parameters"), o valor sugerido e o porquê, \
apoiado em evidências dos dados: convergência (convergence), comparativo com heurísticas (comparison), \
violações (soft_violations), ocupação da frota (fleet), esperas e atrasos (routes).
- Se os parâmetros atuais já parecem adequados para este cenário, diga isso; não sugira mudanças sem evidência.
- Diferencie o que é ajuste do algoritmo genético, das restrições e da operação (frota, horário de saída).
- Considere as paradas para abastecimento (refuel_stops, parameters.schedule.refuel_*): tempo extra, impacto \
nas janelas de horário e se outro tipo de veículo evitaria abastecer."""

SYSTEM_DRIVER = f"""Você é o coordenador de logística de um hospital passando as instruções do dia para os \
motoristas e equipes de entrega de medicamentos e insumos.
Regras:
{_COMMON_RULES}
- Linguagem simples e direta, frases curtas. NÃO use termos técnicos do sistema (algoritmo, fitness, \
geração, penalidade, soft, hard, heurística).
- Para cada veículo: o que carregar (grupos de medicamentos/insumos e peso), horário de saída, a ordem das \
paradas com horário previsto de chegada e a janela de recebimento, e a volta/chegada final.
- Destaque entregas críticas (priority = "critical") e os cuidados: conferência de lote e assinatura do recebedor.
- Explique os cuidados das regras de transporte (transport_rules): não misturar grupos incompatíveis \
(risco de contaminação) e manter a cadeia de frio nas cargas refrigeradas.
- Avise quando houver espera pela abertura da janela ou previsão de atraso, dizendo o que fazer.
- Paradas para abastecimento (refuel_stops), na ordem: diga onde (place) e em que horário abastecer, e que leva \
cerca de parameters.schedule.refuel_minutes minutos; o veículo saiu de tanque cheio. Paradas com en_route = true \
ficam no caminho de um trecho longo (between): o local é o município estimado pela rota, então oriente a \
abastecer no posto mais próximo dessa região e não deixar o tanque passar da reserva."""

TECHNICAL_PROMPT = """Analise a execução abaixo e produza um relatório técnico com exatamente estas seções:

## Diagnóstico da execução
(4 a 6 linhas: cenário, solução viável ou não, distância e tempo, veículos usados x disponíveis, \
tempo de execução do AG)

## Qualidade da solução
(tabela comparando algoritmo genético, vizinho mais próximo e aleatória; interprete a diferença)

## Convergência do algoritmo genético
(use "convergence" e "execution": em que geração houve a última melhora, quantas gerações estagnadas, \
se a execução parou antes do limite pelo critério de estagnação (stopped_by_stagnation, \
parâmetro stagnation_generations) e o que isso indica sobre população, gerações, mutação e esse critério)

## Restrições, frota e operação
(violações aceitas, ocupação de capacidade e autonomia por veículo, veículos ociosos, esperas e atrasos)

## Sugestões de ajuste de parâmetros
(tabela Markdown com colunas: Parâmetro | Valor atual | Sugerido | Por quê | Efeito esperado; \
de 3 a 6 linhas, ordenadas da mais para a menos relevante)

## Próximos testes recomendados
(2 a 4 experimentos concretos para validar as sugestões)
{history_instructions}

Dados (JSON):
```json
{context}
```"""

DRIVER_PROMPT = """Com base na programação abaixo, escreva as instruções do dia para os motoristas, \
com exatamente estas seções:

## Resumo do dia
(2 a 4 linhas: quantos veículos saem, horário de saída, origem e destino final)

## Instruções por veículo
(uma subseção "### Veículo N — <tipo>" para cada veículo que sai; lista do que carregar; \
lista numerada das paradas com cidade, horário previsto, janela de recebimento e o que entregar; paradas para abastecimento (⛽ cidade, horário e duração); \
cuidados específicos desse veículo; horário previsto de volta/chegada final)

## Veículos que não saem hoje
(liste os veículos da frota sem entregas, ou diga que todos saem)

## Cuidados gerais
(checklist curto: separação dos grupos de medicamentos, cadeia de frio, conferência e assinatura, \
o que fazer em caso de atraso)

Dados (JSON):
```json
{context}
```"""

QUESTION_PROMPTS: dict[str, str] = {
    "technical": """Responda à pergunta técnica sobre esta execução usando somente os dados abaixo. \
Quando fizer sentido, cite parâmetros (valor atual e sugerido) e explique o porquê. \
Se a resposta não puder ser obtida dos dados, diga isso claramente. Seja objetivo (até 12 linhas).{history_instructions}

Dados (JSON):
```json
{context}
```

Pergunta: {question}""",
    "driver": """Responda à pergunta do motorista sobre o trajeto e as entregas usando somente os dados abaixo, \
em linguagem simples e sem termos técnicos. Se a resposta não estiver nos dados, diga que precisa \
confirmar com a coordenação. Seja breve (até 8 linhas).

Dados (JSON):
```json
{context}
```

Pergunta: {question}""",
}

SYSTEM_PROMPTS: dict[str, str] = {"technical": SYSTEM_TECHNICAL, "driver": SYSTEM_DRIVER}

HISTORY_REPORT_INSTRUCTIONS = """
Há execuções anteriores com as mesmas cidades (previous_runs, da mais antiga à mais recente; a execução atual \
vem depois delas) e a lista do que mudou da última para a atual (changes_vs_last_run). Inclua também, logo \
depois de "Diagnóstico da execução", a seção:

## Comparação com execuções anteriores
(tabela Markdown: Execução | Parâmetros alterados | Distância (km) | Fitness | Factível | Atrasos (min) | Tempo do AG (ms), \
uma linha por execução anterior e a atual por último; depois, 2 a 4 linhas dizendo quais mudanças melhoraram ou \
pioraram o resultado e por quê)

Nas sugestões e nos próximos testes, leve em conta o que já foi testado: não sugira um valor que já aparece em \
previous_runs[].parameters, e não repita sugestões anteriores (technical_suggestions) que já foram aplicadas sem \
melhora. Quando uma direção já foi testada, diga isso e proponha outra.
"""

HISTORY_QUESTION_INSTRUCTIONS = """
Os dados incluem as execuções anteriores com as mesmas cidades (previous_runs) e o que mudou da última para a \
atual (changes_vs_last_run): use-os para comparar resultados e não sugira valores que já foram testados."""


# ---------------------------------------------------------------- contexto


def convergence_stats(history: list[float]) -> dict:
    """Resumo da curva do melhor fitness por geração."""
    if not history:
        return {}
    first, last = history[0], history[-1]
    last_improvement = max((i for i in range(1, len(history)) if history[i] < history[i - 1]), default=0)
    generations = len(history) - 1
    return {
        "initial_best_fitness": round(first, 2),
        "final_best_fitness": round(last, 2),
        "improvement_pct": round((first - last) / first * 100, 2) if first else 0.0,
        "last_improvement_generation": last_improvement,
        "generations_run": generations,
        "stagnant_generations_at_end": generations - last_improvement,
    }


def build_context(result: OptimizeResponse) -> dict:
    """Contexto completo (sem polylines) enviado à LLM nos dois públicos."""
    locs = result.locations

    def role(i: int) -> str:
        if i == result.origin_index and i == result.destination_index:
            return "origem e destino"
        if i == result.origin_index:
            return "origem"
        return "destino" if i == result.destination_index else "entrega"

    fleet, routes = [], []
    for v in result.vehicles:
        used = len(v.stops) >= 2
        fleet.append({
            "vehicle": v.vehicle_id,
            "type": v.vehicle_name,
            "capacity_kg": v.capacity,
            "autonomy_km": v.autonomy_km,
            "refrigerated": v.refrigerated,
            "autonomy_always_hard": v.strict_autonomy,
            "used": used,
            "load_kg": v.load,
            "distance_km": round(v.distance_km, 1),
            "capacity_use_pct": round(v.load / v.capacity * 100, 1) if v.capacity else None,
            "autonomy_use_pct": round(v.distance_km / v.autonomy_km * 100, 1) if v.autonomy_km else None,
        })
        if not used:
            continue
        routes.append({
            "vehicle": v.vehicle_id,
            "vehicle_type": v.vehicle_name,
            "refrigerated": v.refrigerated,
            "product_groups": v.groups,
            "departure": result.schedule.departure_time,
            "return_or_arrival_at_destination": v.return_time,
            "deliveries": [
                {
                    "city": locs[st.location].name,
                    "priority": locs[st.location].priority,
                    "product_group": locs[st.location].product_group,
                    "demand_kg": locs[st.location].demand,
                    "window": f"{locs[st.location].window_start}-{locs[st.location].window_end}",
                    "arrival": st.arrival,
                    "wait_min": st.wait_min,
                    "late_min": st.late_min,
                    "refuel_here": st.refuel,
                }
                for st in v.schedule
            ],
            "refuel_stops": [
                {
                    "place": f.place,
                    "en_route": f.en_route,
                    "between": f"{locs[f.leg_from].name} → {locs[f.leg_to].name}" if f.en_route else None,
                    "km_into_leg": f.km_into_leg if f.en_route else None,
                    "after_city": locs[f.leg_from].name if f.en_route else locs[f.location].name,
                    "time": f.time,
                    "range_left_pct": f.range_left_pct,
                    "km_since_full_tank": f.km_since_refuel,
                }
                for f in v.refuels
            ],
            "distance_km": round(v.distance_km, 1),
            "duration_min": round(v.duration_min),
            "load_kg": v.load,
            "capacity_ok": v.capacity_ok,
            "autonomy_ok": v.autonomy_ok,
            "priority_ok": v.priority_ok,
            "contamination_ok": v.contamination_ok,
            "time_ok": v.time_ok,
        })

    return {
        "execution": {
            "origin": locs[result.origin_index].name,
            "destination": locs[result.destination_index].name,
            "distance_source": result.distance_source,
            "total_distance_km": round(result.total_distance_km, 1),
            "total_duration_min": round(result.total_duration_min),
            "feasible_hard_constraints": result.feasible,
            "best_fitness": result.fitness,
            "ga_execution_time_ms": round(result.execution_time_ms),
            "generations_executed": result.generations,
            "generations_limit": result.ga.generations,
            "stopped_by_stagnation": result.stopped_by_stagnation,
            "vehicles_available": len(result.vehicles),
            "vehicles_used": len(routes),
        },
        "parameters": {
            "genetic_algorithm": result.ga.model_dump(),
            "constraints": result.constraints.model_dump(),
            "schedule": result.schedule.model_dump(),
        },
        "convergence": convergence_stats(result.fitness_history),
        "comparison": result.comparison.model_dump(),
        "transport_rules": result.transport_rules,
        "soft_violations": result.soft_violations,
        "fleet": fleet,
        "locations": [
            {
                "city": loc.name,
                "role": role(i),
                "product_group": loc.product_group,
                "priority": loc.priority,
                "demand_kg": loc.demand,
                "window": f"{loc.window_start}-{loc.window_end}",
            }
            for i, loc in enumerate(locs)
        ],
        "routes": routes,
    }


def _context_json(result: OptimizeResponse) -> str:
    return json.dumps(build_context(result), ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- histórico de execuções (LLM técnica)

SUGGESTIONS_MAX_CHARS = 1500


def run_parameters(result: OptimizeResponse) -> dict[str, object]:
    """Tudo o que o usuário pode mudar entre execuções com as mesmas cidades, achatado em 'grupo.campo'."""
    params: dict[str, object] = {}
    for group, cfg in (("genetic_algorithm", result.ga), ("constraints", result.constraints), ("schedule", result.schedule)):
        params |= {f"{group}.{k}": v for k, v in cfg.model_dump().items()}
    params["fleet"] = ", ".join(v.vehicle_name for v in result.vehicles)
    for i, loc in enumerate(result.locations):
        if i not in (result.origin_index, result.destination_index):
            params[f"city.{loc.name}"] = (f"grupo {loc.product_group}, {loc.priority}, {loc.demand:g} kg, "
                                          f"janela {loc.window_start}-{loc.window_end}")
    return params


def parameter_changes(before: OptimizeResponse, after: OptimizeResponse) -> list[str]:
    old, new = run_parameters(before), run_parameters(after)
    return [f"{k}: {old.get(k)} → {new.get(k)}" for k in new if old.get(k) != new.get(k)]


def _suggestions_section(report: LLMOutput | None) -> str | None:
    """Trecho de sugestões do relatório técnico anterior (para a LLM não repetir o que já sugeriu)."""
    if not report:
        return None
    text = report.content
    start = text.find("## Sugestões de ajuste")
    if start < 0:
        return None
    end = text.find("\n## ", start + 3)
    section = text[start:end if end > 0 else None].strip()
    return section[:SUGGESTIONS_MAX_CHARS]


def run_summary(result: OptimizeResponse, number: int, previous: OptimizeResponse | None) -> dict:
    stops = [st for v in result.vehicles for st in v.schedule]
    return {
        "run": number,
        "changes_vs_previous_run": parameter_changes(previous, result) if previous else None,
        "parameters": {k: v for k, v in run_parameters(result).items() if not k.startswith("city.")},
        "results": {
            "total_distance_km": round(result.total_distance_km, 1),
            "total_duration_min": round(result.total_duration_min),
            "fitness": result.fitness,
            "feasible_hard_constraints": result.feasible,
            "vehicles_used": sum(len(v.stops) >= 2 for v in result.vehicles),
            "soft_violations": len(result.soft_violations),
            "late_deliveries": sum(st.late_min > 0 for st in stops),
            "total_late_min": round(sum(st.late_min for st in stops)),
            "total_wait_min": round(sum(st.wait_min for st in stops)),
            "refuel_stops": sum(len(v.refuels) for v in result.vehicles),
            "generations_executed": result.generations,
            "stopped_by_stagnation": result.stopped_by_stagnation,
            "last_improvement_generation": convergence_stats(result.fitness_history).get("last_improvement_generation"),
            "ga_execution_time_ms": round(result.execution_time_ms),
            "improvement_vs_nearest_neighbor_pct": result.comparison.improvement_vs_nearest_neighbor_pct,
            "improvement_vs_random_pct": result.comparison.improvement_vs_random_pct,
        },
        "technical_suggestions": _suggestions_section(result.llm_technical),
    }


def history_context(result: OptimizeResponse, previous: list[OptimizeResponse]) -> dict:
    """Execuções anteriores com as mesmas cidades, resumidas, e o que mudou da última para a atual."""
    if not previous:
        return {}
    runs = [run_summary(r, i, previous[i - 2] if i > 1 else None) for i, r in enumerate(previous, 1)]
    return {
        "previous_runs": runs,
        "current_run": len(previous) + 1,
        "changes_vs_last_run": parameter_changes(previous[-1], result),
    }


# ---------------------------------------------------------------- chamada ao OpenRouter

MAX_RETRIES = 3
MAX_RETRY_WAIT_SECONDS = 10.0
REPORT_MAX_TOKENS = 8000   # relatórios longos; modelos com raciocínio também gastam do orçamento
MAX_TOKENS_CAP = 16000     # teto ao dobrar o orçamento quando a resposta vem truncada
TRUNCATED_NOTE = "\n\n_(Resposta interrompida pelo limite de tamanho da LLM.)_"


def _retry_wait(resp: httpx.Response, attempt: int) -> float:
    """Usa o Retry-After do OpenRouter quando houver; senão, backoff exponencial (2s, 4s, ...)."""
    try:
        wait = float(resp.headers.get("Retry-After", ""))
    except ValueError:
        wait = 2.0 * 2**attempt
    return min(wait, MAX_RETRY_WAIT_SECONDS)


def _chat(user_prompt: str, max_tokens: int, system: str = SYSTEM_DRIVER) -> LLMOutput:
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY não configurada")
    payload = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        # Modelos com raciocínio podem gastar todo o max_tokens pensando e devolver content=null.
        # Esforço baixo limita esse gasto; modelos sem raciocínio ignoram o parâmetro.
        "reasoning": {"effort": "low", "exclude": True},
    }
    models = [settings.openrouter_model, *settings.openrouter_fallback_models]
    if len(models) > 1:
        # O OpenRouter tenta os modelos em ordem quando o anterior falha ou está limitado (429).
        payload["models"] = models
    else:
        payload["model"] = models[0]

    last_error = ""
    truncated: LLMOutput | None = None
    for attempt in range(MAX_RETRIES + 1):
        resp = httpx.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "X-Title": "Rotas Medicas - Tech Challenge",
            },
            json=payload,
            timeout=settings.http_timeout_seconds,
        )
        if resp.status_code in (429, 502, 503):
            last_error = f"OpenRouter retornou {resp.status_code}: {resp.text[:300]}"
            wait = _retry_wait(resp, attempt)
        elif resp.status_code != 200:
            raise RuntimeError(f"OpenRouter retornou {resp.status_code}: {resp.text[:300]}")
        else:
            data = resp.json()
            if "error" in data:
                raise RuntimeError(f"OpenRouter: {data['error']}")
            choice = (data.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content")
            has_text = isinstance(content, str) and content.strip()
            if has_text and choice.get("finish_reason") == "length" and payload["max_tokens"] < MAX_TOKENS_CAP:
                # Texto cortado no limite de tokens (o raciocínio do modelo também consome o orçamento):
                # tenta de novo com o dobro, guardando o parcial caso as tentativas acabem.
                truncated = LLMOutput(generated=True, provider="openrouter", model=data.get("model", models[0]),
                                      content=content + TRUNCATED_NOTE)
                payload["max_tokens"] = min(MAX_TOKENS_CAP, payload["max_tokens"] * 2)
                last_error = f"resposta truncada (finish_reason=length); novo limite {payload['max_tokens']} tokens"
                wait = 0.0
            elif has_text:
                if choice.get("finish_reason") == "length":  # já no teto: devolve o texto avisando que foi cortado
                    content += TRUNCATED_NOTE
                return LLMOutput(generated=True, provider="openrouter", model=data.get("model", models[0]), content=content)
            # Resposta vazia: típico de modelo que esgotou o max_tokens raciocinando (finish_reason="length").
            last_error = (
                f"OpenRouter devolveu resposta vazia (modelo={data.get('model')}, "
                f"finish_reason={choice.get('finish_reason')})"
            )
            wait = 1.0
        if attempt < MAX_RETRIES:
            logger.warning("%s; nova tentativa em %.0fs", last_error, wait)
            time.sleep(wait)
    if truncated:
        return truncated
    raise RuntimeError(last_error)


# ---------------------------------------------------------------- relatórios e perguntas


def _report(audience: Audience, context_json: str, ctx: dict) -> LLMOutput:
    if audience == "technical":
        extra = HISTORY_REPORT_INSTRUCTIONS if ctx.get("previous_runs") else ""
        prompt = TECHNICAL_PROMPT.format(context=context_json, history_instructions=extra)
    else:
        prompt = DRIVER_PROMPT.format(context=context_json)
    try:
        return _chat(prompt, max_tokens=REPORT_MAX_TOKENS, system=SYSTEM_PROMPTS[audience])
    except Exception as exc:  # noqa: BLE001 - qualquer falha cai no template
        logger.warning("LLM indisponível (%s), usando template: %s", audience, exc)
        content = fallback_technical(ctx) if audience == "technical" else fallback_driver(ctx)
        return LLMOutput(generated=False, provider="template", content=content, error=str(exc))


def generate_reports(result: OptimizeResponse, previous: list[OptimizeResponse] = ()) -> tuple[LLMOutput, LLMOutput]:
    """Gera (relatório técnico, instruções para motoristas) em paralelo.

    `previous`: execuções anteriores com as mesmas cidades; só a análise técnica as recebe, para comparar.
    """
    ctx = build_context(result)
    context_json = json.dumps(ctx, ensure_ascii=False, indent=1)
    tech_ctx = ctx | history_context(result, list(previous))
    tech_json = json.dumps(tech_ctx, ensure_ascii=False, indent=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        technical = pool.submit(_report, "technical", tech_json, tech_ctx)
        driver = pool.submit(_report, "driver", context_json, ctx)
        return technical.result(), driver.result()


def answer_question(
    result: OptimizeResponse, question: str, audience: Audience = "driver", previous: list[OptimizeResponse] = ()
) -> LLMOutput:
    try:
        ctx = build_context(result)
        history = history_context(result, list(previous)) if audience == "technical" else {}
        prompt = QUESTION_PROMPTS[audience].format(
            context=json.dumps(ctx | history, ensure_ascii=False, indent=1),
            question=question,
            history_instructions=HISTORY_QUESTION_INSTRUCTIONS if history else "",
        )
        return _chat(prompt, max_tokens=3000, system=SYSTEM_PROMPTS[audience])
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM indisponível para pergunta (%s): %s", audience, exc)
        return LLMOutput(
            generated=False,
            provider="template",
            content="Não foi possível consultar a LLM. Configure OPENROUTER_API_KEY no backend para habilitar perguntas.",
            error=str(exc),
        )


# ---------------------------------------------------------------- templates (sem LLM)


def heuristic_suggestions(ctx: dict) -> list[tuple[str, str, str, str]]:
    """Sugestões de ajuste por regras simples: (parâmetro, atual, sugerido, por quê)."""
    ga = ctx["parameters"]["genetic_algorithm"]
    cons = ctx["parameters"]["constraints"]
    sched = ctx["parameters"]["schedule"]
    conv = ctx.get("convergence") or {}
    comp = ctx["comparison"]
    out: list[tuple[str, str, str, str]] = []

    gens, last = conv.get("generations_run", 0), conv.get("last_improvement_generation", 0)
    stagnation = ga.get("stagnation_generations", 0)
    if ctx["execution"].get("stopped_by_stagnation"):
        # Parou sozinho: sobra de gerações já não custa tempo. Só sugere mais paciência se a parada foi muito cedo.
        if stagnation and stagnation < 50 and len(ctx["locations"]) > 10:
            out.append(("Parada por estagnação", str(stagnation), str(stagnation * 2),
                        f"Parou na geração {gens} após {stagnation} gerações sem melhora; em cenários com mais cidades, "
                        "mais paciência dá chance à mutação de achar soluções melhores."))
    elif gens and last >= 0.9 * gens:
        out.append(("Gerações", str(ga["generations"]), str(ga["generations"] * 2),
                    f"A última melhora foi na geração {last} de {gens}: o AG ainda estava melhorando quando parou."))
    elif gens >= 100 and last <= 0.3 * gens:
        why = (
            "A melhor solução já era a semente do vizinho mais próximo (geração 0): neste cenário as gerações não trouxeram ganho."
            if last == 0
            else f"Sem melhora desde a geração {last}: as gerações restantes só gastaram tempo."
        )
        out.append(("Gerações", str(ga["generations"]), str(max(50, int(last * 1.5) + 20)), why))
    if comp["genetic_algorithm"]["fitness"] >= comp["nearest_neighbor"]["fitness"] and len(ctx["locations"]) > 6:
        out.append(("Probabilidade de mutação", str(ga["mutation_probability"]),
                    str(min(0.8, round(ga["mutation_probability"] + 0.2, 2))),
                    "O AG não superou o vizinho mais próximo; mais mutação aumenta a diversidade e ajuda a sair de ótimos locais."))

    idle = [f["type"] for f in ctx["fleet"] if not f["used"]]
    if idle:
        out.append(("Frota", f"{len(ctx['fleet'])} veículos", f"{len(ctx['fleet']) - len(idle)} veículos",
                    f"Sem entregas: {', '.join(idle)}. Retirá-los não muda a solução e simplifica o cenário."))

    waits = [d["wait_min"] for r in ctx["routes"] for d in r["deliveries"][:1] if d["wait_min"] > 30]
    if waits and all(len(r["deliveries"]) and r["deliveries"][0]["wait_min"] > 30 for r in ctx["routes"]):
        out.append(("Horário de saída", sched["departure_time"], f"~{int(min(waits))} min mais tarde",
                    "Todos os veículos esperam a janela abrir na primeira parada."))
    late = [v for v in ctx["soft_violations"] if "janela" in v]
    if late:
        out.append(("Horário de saída / janela", f"{sched['departure_time']} / {cons['time_window']}",
                    "sair mais cedo ou janela rígida",
                    f"{len(late)} entrega(s) com atraso foram aceitas como violação flexível."))
    if ctx["soft_violations"] and cons["soft_penalty"] < 100 and not late:
        out.append(("Penalidade soft", str(cons["soft_penalty"]), str(cons["soft_penalty"] * 4),
                    "Há excessos aceitos de capacidade/autonomia; penalidade maior reduz esses excessos."))
    if cons["priority"] == "soft" and any(not r["priority_ok"] for r in ctx["routes"]):
        out.append(("Peso da prioridade", str(ga["priority_weight"]), str(round(ga["priority_weight"] * 2, 2)),
                    "Há entregas críticas feitas depois de regulares no mesmo veículo."))
    return out


# Sugestões por regra que apontam para um valor de parâmetro (para não repetir valores já testados).
SUGGESTION_PARAMS = {
    "Gerações": "genetic_algorithm.generations",
    "Probabilidade de mutação": "genetic_algorithm.mutation_probability",
    "Parada por estagnação": "genetic_algorithm.stagnation_generations",
    "Penalidade soft": "constraints.soft_penalty",
    "Peso da prioridade": "genetic_algorithm.priority_weight",
}


def _already_tested(ctx: dict, param: str, suggested: str) -> bool:
    key = SUGGESTION_PARAMS.get(param)
    if not key:
        return False
    return any(str(r["parameters"].get(key)) == suggested for r in ctx.get("previous_runs", []))


def _history_table(ctx: dict) -> list[str]:
    ex, cur = ctx["execution"], ctx["current_run"]
    late_now = round(sum(d["late_min"] for r in ctx["routes"] for d in r["deliveries"]))
    rows = [
        (r["run"], "; ".join(r["changes_vs_previous_run"] or []) or ("—" if r["run"] == 1 else "nenhum"),
         r["results"]["total_distance_km"], r["results"]["fitness"], r["results"]["feasible_hard_constraints"],
         r["results"]["total_late_min"], r["results"]["ga_execution_time_ms"])
        for r in ctx["previous_runs"]
    ]
    rows.append((f"{cur} (atual)", "; ".join(ctx["changes_vs_last_run"]) or "nenhum", ex["total_distance_km"],
                 ex["best_fitness"], ex["feasible_hard_constraints"], late_now, ex["ga_execution_time_ms"]))
    return [
        "## Comparação com execuções anteriores",
        "| Execução | Parâmetros alterados | Distância (km) | Fitness | Factível | Atrasos (min) | Tempo do AG (ms) |",
        "|---|---|---|---|---|---|---|",
        *[f"| {n} | {c} | {d} | {f} | {'sim' if ok else 'não'} | {late} | {ms} |" for n, c, d, f, ok, late, ms in rows],
        "",
    ]


def fallback_technical(ctx: dict) -> str:
    ex, comp, conv = ctx["execution"], ctx["comparison"], ctx.get("convergence") or {}
    lines = [
        "## Diagnóstico da execução",
        f"{ex['origin']} → {ex['destination']} · {ex['total_distance_km']} km · "
        f"veículos usados {ex['vehicles_used']}/{ex['vehicles_available']} · "
        f"restrições rígidas {'atendidas' if ex['feasible_hard_constraints'] else 'violadas'} · "
        f"AG em {ex['ga_execution_time_ms']} ms",
        "",
        *(_history_table(ctx) if ctx.get("previous_runs") else []),
        "## Qualidade da solução",
        "| Abordagem | Distância (km) | Fitness | Factível |",
        "|---|---|---|---|",
        *[
            f"| {name} | {comp[k]['distance_km']:.1f} | {comp[k]['fitness']:.1f} | {'sim' if comp[k]['feasible'] else 'não'} |"
            for name, k in (("Algoritmo genético", "genetic_algorithm"), ("Vizinho mais próximo", "nearest_neighbor"),
                            ("Aleatória (média)", "random_average"))
        ],
        "",
        "## Convergência do algoritmo genético",
        f"Melhora de {conv.get('improvement_pct', 0)}% no fitness; última melhora na geração "
        f"{conv.get('last_improvement_generation', 0)} de {conv.get('generations_run', 0)}.",
        "",
        "## Sugestões de ajuste de parâmetros",
    ]
    suggestions = heuristic_suggestions(ctx)
    tested = [(p, s) for p, _, s, _ in suggestions if _already_tested(ctx, p, s)]
    suggestions = [x for x in suggestions if (x[0], x[2]) not in tested]
    if suggestions:
        lines += ["| Parâmetro | Valor atual | Sugerido | Por quê |", "|---|---|---|---|"]
        lines += [f"| {p} | {a} | {s} | {w} |" for p, a, s, w in suggestions]
    else:
        lines.append("Os parâmetros atuais parecem adequados para este cenário.")
    if tested:
        lines.append("")
        lines.append("Já testado nas execuções anteriores (não sugerido de novo): "
                     + "; ".join(f"{p} = {s}" for p, s in tested) + ".")
    lines += ["", "_Análise gerada por regras (LLM não disponível)._"]
    return "\n".join(lines)


def fallback_driver(ctx: dict) -> str:
    ex = ctx["execution"]
    lines = [
        "## Resumo do dia",
        f"Saem **{ex['vehicles_used']}** veículo(s) de {ex['origin']} às "
        f"**{ctx['parameters']['schedule']['departure_time']}**, com destino final em {ex['destination']}.",
        "",
        "**Regras de transporte:** " + " ".join(ctx.get("transport_rules", [])),
        "",
        "## Instruções por veículo",
    ]
    for r in ctx["routes"]:
        cold = " ❄️ manter refrigerado" if r["refrigerated"] else ""
        lines.append(f"### Veículo {r['vehicle']} — {r['vehicle_type']}{cold}")
        lines.append(f"Carregar: grupos {', '.join(r['product_groups'])}, total {r['load_kg']:g} kg. "
                     f"Saída às {r['departure']}.")
        minutes = ctx["parameters"]["schedule"].get("refuel_minutes", 15)
        en_route = [f for f in r.get("refuel_stops", []) if f["en_route"]]

        def on_the_way(after_city: str) -> list[str]:
            return [f"   ⛽ No caminho ({f['between']}, km {f['km_into_leg']:.0f}): abastecer em {f['place']} "
                    f"por volta das {f['time']} (cerca de {minutes:g} min)." for f in en_route if f["after_city"] == after_city]

        lines += on_the_way(ex["origin"])
        for n, stop in enumerate(r["deliveries"], 1):
            flag = " ⚠️ CRÍTICO — conferir lote e colher assinatura" if stop["priority"] == "critical" else ""
            extra = ""
            if stop["wait_min"]:
                extra = f" (aguardar abertura, cerca de {stop['wait_min']:.0f} min)"
            if stop["late_min"]:
                extra = f" (previsão de atraso de {stop['late_min']:.0f} min: avisar a unidade)"
            lines.append(f"{n}. {stop['city']} — chegar às {stop['arrival']} (recebe {stop['window']}){extra}: "
                         f"entregar grupo {stop['product_group']}, {stop['demand_kg']:g} kg{flag}")
            if stop.get("refuel_here"):
                lines.append(f"   ⛽ Abastecer em {stop['city']} antes de seguir (cerca de {minutes:g} min).")
            lines += on_the_way(stop["city"])
        lines.append(f"Chegada final prevista: {r['return_or_arrival_at_destination']}.")
        lines.append("")
    idle = [f"Veículo {f['vehicle']} ({f['type']})" for f in ctx["fleet"] if not f["used"]]
    lines += ["## Veículos que não saem hoje", ", ".join(idle) if idle else "Todos os veículos saem.", ""]
    lines += [
        "## Cuidados gerais",
        "- Não misture grupos incompatíveis no mesmo veículo.",
        "- Carga refrigerada: mantenha a cadeia de frio do carregamento à entrega.",
        "- Confira lote e quantidade e colha a assinatura do recebedor.",
        "- Em caso de atraso, avise a unidade e a coordenação.",
        "",
        "_Instruções geradas por template (LLM não disponível)._",
    ]
    return "\n".join(lines)
