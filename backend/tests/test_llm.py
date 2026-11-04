"""Testes do cliente OpenRouter com respostas simuladas (sem rede)."""
import httpx
import pytest

from app.services import llm

OK_BODY = {"model": "modelo-reserva", "choices": [{"message": {"content": "relatório"}}]}


@pytest.fixture
def fake_openrouter(monkeypatch):
    monkeypatch.setattr(llm.settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(llm.settings, "openrouter_model", "principal:free")
    monkeypatch.setattr(llm.settings, "openrouter_fallback_models", ["reserva:free"])
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    calls = []

    def install(responses):
        def fake_post(url, json, **kwargs):
            calls.append(dict(json))  # cópia: o código altera o payload entre tentativas
            return responses.pop(0)

        monkeypatch.setattr(llm.httpx, "post", fake_post)
        return calls

    return install


def test_retries_after_429_and_sends_fallback_models(fake_openrouter):
    calls = fake_openrouter([
        httpx.Response(429, headers={"Retry-After": "1"}, text="rate limited"),
        httpx.Response(200, json=OK_BODY),
    ])
    out = llm._chat("prompt", max_tokens=10)
    assert out.generated and out.content == "relatório" and out.model == "modelo-reserva"
    assert len(calls) == 2
    assert calls[0]["models"] == ["principal:free", "reserva:free"]


def test_gives_up_after_max_retries(fake_openrouter):
    fake_openrouter([httpx.Response(429, text="rate limited") for _ in range(llm.MAX_RETRIES + 1)])
    with pytest.raises(RuntimeError, match="429"):
        llm._chat("prompt", max_tokens=10)


EMPTY_BODY = {"model": "principal:free", "choices": [{"finish_reason": "length", "message": {"content": None}}]}


def test_retries_when_content_is_null(fake_openrouter):
    calls = fake_openrouter([httpx.Response(200, json=EMPTY_BODY), httpx.Response(200, json=OK_BODY)])
    out = llm._chat("prompt", max_tokens=10)
    assert out.content == "relatório"
    assert len(calls) == 2
    assert calls[0]["reasoning"] == {"effort": "low", "exclude": True}


def test_null_content_raises_clear_error(fake_openrouter):
    fake_openrouter([httpx.Response(200, json=EMPTY_BODY) for _ in range(llm.MAX_RETRIES + 1)])
    with pytest.raises(RuntimeError, match="finish_reason=length"):
        llm._chat("prompt", max_tokens=10)


# ---------------------------------------------------------------- relatórios por público
from fastapi.testclient import TestClient  # noqa: E402

from app.api import auth, routes_controller  # noqa: E402
from app.main import app  # noqa: E402
from app.services.distance import DistanceService  # noqa: E402
from app.services.optimizer_service import OptimizerService  # noqa: E402

SMALL_GA = {"population_size": 30, "generations": 40, "seed": 2}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(auth.settings, "auth_username", "admin")
    monkeypatch.setattr(auth.settings, "auth_password", "fiap@2026")
    monkeypatch.setattr(llm.settings, "openrouter_api_key", "")
    service = OptimizerService(DistanceService(google=None))
    app.dependency_overrides[routes_controller.get_optimizer] = lambda: service
    c = TestClient(app)
    token = c.post("/api/auth/login", json={"username": "admin", "password": "fiap@2026"}).json()["access_token"]
    c.headers["Authorization"] = f"Bearer {token}"
    yield c, service
    app.dependency_overrides.clear()


def test_context_has_parameters_full_fleet_and_convergence(api):
    c, service = api
    body = c.post("/api/routes/optimize/default", json={"ga": SMALL_GA, "use_llm": False}).json()
    ctx = llm.build_context(service.get_result(body["id"]))
    assert ctx["parameters"]["genetic_algorithm"]["generations"] == 40
    assert set(ctx["parameters"]) == {"genetic_algorithm", "constraints", "schedule"}
    assert len(ctx["fleet"]) == 5                      # frota inteira, inclusive veículos não usados
    assert any(not f["used"] for f in ctx["fleet"])
    assert len(ctx["locations"]) == 5 and ctx["locations"][0]["role"] == "origem e destino"
    assert ctx["convergence"]["generations_run"] == 40


def test_reports_use_audience_specific_prompts(monkeypatch, api):
    c, service = api
    body = c.post("/api/routes/optimize/default", json={"ga": SMALL_GA, "use_llm": False}).json()
    result = service.get_result(body["id"])
    seen = []

    def fake_chat(prompt, max_tokens, system=llm.SYSTEM_DRIVER):
        seen.append((system, prompt))
        return llm.LLMOutput(generated=True, provider="openrouter", content="ok")

    monkeypatch.setattr(llm, "_chat", fake_chat)
    technical, driver = llm.generate_reports(result)
    assert technical.content == driver.content == "ok"
    systems = {s for s, _ in seen}
    assert systems == {llm.SYSTEM_TECHNICAL, llm.SYSTEM_DRIVER}
    tech_prompt = next(p for s, p in seen if s == llm.SYSTEM_TECHNICAL)
    assert "Sugestões de ajuste de parâmetros" in tech_prompt and '"mutation_probability"' in tech_prompt

    seen.clear()
    llm.answer_question(result, "Posso aumentar a mutação?", "technical")
    llm.answer_question(result, "Que horas chego em Campinas?", "driver")
    assert [s for s, _ in seen] == [llm.SYSTEM_TECHNICAL, llm.SYSTEM_DRIVER]


def test_ask_endpoint_accepts_audience(api):
    c, _ = api
    rid = c.post("/api/routes/optimize/default", json={"ga": SMALL_GA, "use_llm": False}).json()["id"]
    for audience in ("technical", "driver"):
        resp = c.post("/api/routes/ask", json={"result_id": rid, "question": "Teste?", "audience": audience})
        assert resp.status_code == 200
    assert c.post("/api/routes/ask", json={"result_id": rid, "question": "Teste?", "audience": "outro"}).status_code == 422


def test_heuristic_suggestions_flag_idle_vehicles_and_short_run(api):
    c, service = api
    body = c.post("/api/routes/optimize/default", json={"ga": SMALL_GA, "use_llm": False}).json()
    ctx = llm.build_context(service.get_result(body["id"]))
    params = [p for p, *_ in llm.heuristic_suggestions(ctx)]
    assert "Frota" in params  # o cenário padrão sempre deixa veículos ociosos
    text = llm.fallback_technical(ctx)
    assert "| Parâmetro | Valor atual | Sugerido | Por quê |" in text


TRUNCATED_BODY = {"model": "m", "choices": [{"finish_reason": "length", "message": {"content": "texto cortado"}}]}


def test_truncated_answer_retries_with_bigger_budget(fake_openrouter):
    calls = fake_openrouter([httpx.Response(200, json=TRUNCATED_BODY), httpx.Response(200, json=OK_BODY)])
    out = llm._chat("prompt", max_tokens=1000)
    assert out.content == "relatório"
    assert [c["max_tokens"] for c in calls] == [1000, 2000]


def test_truncated_answer_is_returned_with_note_when_budget_runs_out(fake_openrouter):
    fake_openrouter([httpx.Response(200, json=TRUNCATED_BODY) for _ in range(llm.MAX_RETRIES + 1)])
    out = llm._chat("prompt", max_tokens=llm.MAX_TOKENS_CAP // 2)
    assert out.generated and out.content.startswith("texto cortado") and "interrompida" in out.content


# ---------------------------------------------------------------- histórico de execuções (mesmas cidades)


def test_technical_report_compares_with_previous_runs_of_same_cities(monkeypatch, api):
    c, service = api
    first = c.post("/api/routes/optimize/default", json={"ga": SMALL_GA, "use_llm": False}).json()
    second = c.post("/api/routes/optimize/default", json={
        "ga": {**SMALL_GA, "mutation_probability": 0.5}, "use_llm": False, "previous_result_ids": [first["id"]],
    }).json()
    assert second["previous_result_ids"] == [first["id"]]

    result = service.get_result(second["id"])
    seen = []

    def fake_chat(prompt, max_tokens, system=llm.SYSTEM_DRIVER):
        seen.append((system, prompt))
        return llm.LLMOutput(generated=True, provider="openrouter", content="ok")

    monkeypatch.setattr(llm, "_chat", fake_chat)
    llm.generate_reports(result, service.previous_runs(result))
    tech = next(p for s, p in seen if s == llm.SYSTEM_TECHNICAL)
    driver = next(p for s, p in seen if s == llm.SYSTEM_DRIVER)
    assert "## Comparação com execuções anteriores" in tech and '"previous_runs"' in tech
    assert "genetic_algorithm.mutation_probability: 0.3 → 0.5" in tech
    assert "previous_runs" not in driver  # o histórico é só da análise técnica

    seen.clear()
    assert c.post("/api/routes/ask", json={"result_id": second["id"], "question": "Melhorou?", "audience": "technical"}).status_code == 200
    assert '"previous_runs"' in seen[0][1] and "não sugira valores que já foram testados" in seen[0][1]


def test_history_resets_when_cities_change(api):
    c, _ = api
    first = c.post("/api/routes/optimize/default", json={"ga": SMALL_GA, "use_llm": False}).json()
    cities = c.get("/api/cities/default").json()["cities"]
    other = c.post("/api/routes/optimize", json={
        "locations": cities, "origin_index": 1, "destination_index": 1, "ga": SMALL_GA, "use_llm": False,
        "previous_result_ids": [first["id"]],
    }).json()
    assert other["previous_result_ids"] == []  # outra origem/destino: contexto novo


def test_fallback_technical_shows_history_and_skips_tested_values(api):
    c, service = api
    first = c.post("/api/routes/optimize/default", json={"ga": SMALL_GA, "use_llm": False}).json()
    second = c.post("/api/routes/optimize/default", json={
        "ga": {**SMALL_GA, "generations": 41}, "use_llm": False, "previous_result_ids": [first["id"]],
    }).json()
    result = service.get_result(second["id"])
    ctx = llm.build_context(result) | llm.history_context(result, service.previous_runs(result))
    text = llm.fallback_technical(ctx)
    assert "## Comparação com execuções anteriores" in text
    assert "| 2 (atual) | genetic_algorithm.generations: 40 → 41 |" in text
    # Toda sugestão com valor já usado antes some da tabela de sugestões.
    for param, _, suggested, _ in llm.heuristic_suggestions(ctx):
        if llm._already_tested(ctx, param, suggested):
            assert f"| {param} |" not in text.split("## Sugestões")[1].split("Já testado")[0]
