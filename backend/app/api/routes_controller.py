"""Controller REST consumida pelo frontend Angular."""
import difflib
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.auth import require_user
from app.config import settings
from app.data.catalog import DEFAULT_FLEET, PRODUCT_GROUPS, TRANSPORT_RULES, VEHICLE_TYPES
from app.data.cities import DEFAULT_CITIES, MAX_RANDOM_CITIES, sample_random_cities
from app.data.geo import STATES, municipalities, normalize
from app.schemas import (
    AskRequest,
    AskResponse,
    CatalogResponse,
    CitiesResponse,
    DefaultOptimizeRequest,
    GeocodeRequest,
    GeocodeResponse,
    OptimizeRequest,
    OptimizeResponse,
    ProductGroupInfo,
    StateInfo,
    UF,
    VehicleTypeInfo,
)
from app.services import llm
from app.services.distance import build_distance_service
from app.services.optimizer_service import InfeasibleProblemError, OptimizerService

# Todas as rotas deste router exigem um token de acesso válido.
router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


@lru_cache
def get_optimizer() -> OptimizerService:
    return OptimizerService(build_distance_service())


@router.get("/health")
def health(service: OptimizerService = Depends(get_optimizer)):
    return {"status": "ok", "distance_source": service.distances.source, "llm_enabled": bool(settings.openrouter_api_key)}


@router.get("/catalog", response_model=CatalogResponse)
def catalog():
    """Cadastro de medicamentos/insumos, regras de transporte e tipos de veículo."""
    return CatalogResponse(
        product_groups=[ProductGroupInfo(code=code, **info) for code, info in PRODUCT_GROUPS.items()],
        vehicle_types=[VehicleTypeInfo(type=t, **info) for t, info in VEHICLE_TYPES.items()],
        transport_rules=TRANSPORT_RULES,
        default_fleet=DEFAULT_FLEET,
    )


@router.get("/cities/default", response_model=CitiesResponse)
def default_cities(service: OptimizerService = Depends(get_optimizer)):
    """As 5 cidades fixas do cenário padrão (índice 0 = hub/origem)."""
    return CitiesResponse(distance_source=service.distances.source, cities=service.enrich_cities(DEFAULT_CITIES))


@router.get("/cities/random", response_model=CitiesResponse)
def random_cities(
    count: int = Query(..., ge=3, le=MAX_RANDOM_CITIES, description="Quantidade de cidades a sortear"),
    service: OptimizerService = Depends(get_optimizer),
):
    """Sorteia cidades reais com prioridade e demanda aleatórias."""
    return CitiesResponse(distance_source=service.distances.source, cities=service.enrich_cities(sample_random_cities(count)))


@router.get("/geo/states", response_model=list[StateInfo])
def states():
    """Estados brasileiros (cadastro manual de cidades)."""
    return [StateInfo(uf=uf, name=name) for uf, name in STATES.items()]


@router.get("/geo/states/{uf}/cities", response_model=list[str])
def state_cities(uf: UF):
    """Municípios do estado, pela API de localidades do IBGE (sugestões no cadastro manual)."""
    try:
        return list(municipalities(uf))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Não foi possível consultar os municípios no IBGE: {exc}") from None


@router.post("/cities/geocode", response_model=GeocodeResponse)
def geocode_city(req: GeocodeRequest, service: OptimizerService = Depends(get_optimizer)):
    """Coordenadas reais de um município (Google Maps; sem chave, catálogo local de SP).

    O nome é conferido na lista oficial de municípios do IBGE: o Google aceita também
    bairros/distritos com o mesmo nome (ex.: "Campinas" em Sumidouro-RJ).
    """
    city = req.city.strip()
    try:
        names = municipalities(req.state)
    except Exception:  # noqa: BLE001 - IBGE fora do ar: segue só com a validação do Google
        names = None
    if names is not None:
        by_norm = {normalize(n): n for n in names}
        canonical = by_norm.get(normalize(city))
        if not canonical:
            close = difflib.get_close_matches(normalize(city), list(by_norm), n=3, cutoff=0.75)
            hint = f" Você quis dizer: {', '.join(by_norm[c] for c in close)}?" if close else ""
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=f"'{city}' não é um município de {STATES[req.state]}.{hint}")
        city = canonical
    found = service.distances.geocode_place(city, req.state)
    if not found:
        hint = "" if service.distances.google else " Sem chave do Google Maps, só cidades do catálogo de SP são aceitas."
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Município '{city}' não encontrado em {STATES[req.state]}.{hint}")
    return GeocodeResponse(**found)


@router.post("/routes/optimize", response_model=OptimizeResponse)
def optimize(req: OptimizeRequest, service: OptimizerService = Depends(get_optimizer)):
    """Otimiza rotas para as cidades informadas, com origem e destino escolhidos.

    Responde 422 quando uma restrição hard não pode ser cumprida.
    """
    return _run(service, req)


@router.post("/routes/optimize/default", response_model=OptimizeResponse)
def optimize_default(req: DefaultOptimizeRequest | None = None, service: OptimizerService = Depends(get_optimizer)):
    """Otimiza o cenário padrão de 5 cidades (origem e destino = São Paulo)."""
    req = req or DefaultOptimizeRequest()
    full = OptimizeRequest(
        locations=service.enrich_cities(DEFAULT_CITIES),
        origin_index=0,
        destination_index=0,
        vehicles=req.vehicles,
        schedule=req.schedule,
        ga=req.ga,
        constraints=req.constraints,
        use_llm=req.use_llm,
        previous_result_ids=req.previous_result_ids,
    )
    return _run(service, full)


def _run(service: OptimizerService, req: OptimizeRequest) -> OptimizeResponse:
    try:
        return service.optimize(req)
    except InfeasibleProblemError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": exc.message, "reasons": exc.reasons},
        ) from None


@router.post("/routes/ask", response_model=AskResponse)
def ask(req: AskRequest, service: OptimizerService = Depends(get_optimizer)):
    """Pergunta em linguagem natural sobre uma otimização já executada."""
    result = service.get_result(req.result_id)
    if not result:
        raise HTTPException(status_code=404, detail="Resultado não encontrado. Execute a otimização novamente.")
    return AskResponse(answer=llm.answer_question(result, req.question, req.audience, service.previous_runs(result)))
