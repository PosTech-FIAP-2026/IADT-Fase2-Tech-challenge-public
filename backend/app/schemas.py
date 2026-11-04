"""Modelos (DTOs) da API."""
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.data.catalog import DEFAULT_FLEET

Priority = Literal["critical", "regular"]
ConstraintMode = Literal["soft", "hard"]
ProductGroup = Literal["A", "B", "C", "D", "E"]
VehicleType = Literal["moto", "carro", "carro_eletrico", "caminhao_refrigerado", "caminhao"]

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def hhmm_to_min(value: str) -> int:
    h, m = value.split(":")
    return int(h) * 60 + int(m)


def _check_hhmm(value: str) -> str:
    if not _HHMM.match(value):
        raise ValueError("horário deve estar no formato HH:MM (00:00 a 23:59)")
    return value


class Location(BaseModel):
    name: str
    lat: float
    lng: float
    priority: Priority = "regular"
    demand: float = Field(0, ge=0, description="Carga a entregar no ponto (kg)")
    product_group: ProductGroup = Field("D", description="Grupo de medicamentos/insumos entregue no ponto")
    window_start: str = Field("00:00", description="Início da janela de recebimento (HH:MM)")
    window_end: str = Field("23:59", description="Fim da janela de recebimento (HH:MM)")
    address: str | None = None

    @field_validator("window_start", "window_end")
    @classmethod
    def _valid_time(cls, v: str) -> str:
        return _check_hhmm(v)

    @model_validator(mode="after")
    def _check_window(self):
        if hhmm_to_min(self.window_start) >= hhmm_to_min(self.window_end):
            raise ValueError(f"{self.name}: o início da janela deve ser antes do fim")
        return self


class VehicleIn(BaseModel):
    """Veículo da frota; capacidade e autonomia vêm do cadastro do tipo."""

    type: VehicleType


def default_fleet() -> list[VehicleIn]:
    return [VehicleIn(type=t) for t in DEFAULT_FLEET]


class ScheduleConfig(BaseModel):
    departure_time: str = Field("07:00", description="Horário de saída dos veículos da origem (HH:MM)")
    service_minutes: float = Field(15, ge=0, le=240, description="Tempo de descarga em cada entrega (min)")
    refuel_enabled: bool = Field(
        True, description="Abastecer em rota: os veículos saem de tanque cheio e abastecem numa cidade da rota "
        "quando a autonomia chegaria à reserva (carro elétrico não abastece)",
    )
    refuel_reserve_pct: float = Field(15, ge=0, le=50, description="Reserva (% da autonomia) que dispara o abastecimento")
    refuel_minutes: float = Field(15, ge=0, le=120, description="Duração de cada abastecimento (min)")

    @field_validator("departure_time")
    @classmethod
    def _valid_time(cls, v: str) -> str:
        return _check_hhmm(v)


class GAConfig(BaseModel):
    population_size: int = Field(100, ge=10, le=1000)
    generations: int = Field(300, ge=1, le=5000)
    mutation_probability: float = Field(0.3, ge=0, le=1)
    tournament_size: int = Field(3, ge=2, le=20)
    elite_size: int = Field(2, ge=1, le=50)
    priority_weight: float = Field(0.5, ge=0, description="Peso da penalidade por atraso em entregas críticas")
    seed: int | None = Field(42, ge=0, description="Semente aleatória (mesma semente = mesmo resultado); null = aleatória")
    stagnation_generations: int = Field(
        100, ge=0, le=5000,
        description="Para antes do fim se o melhor fitness ficar esse número de gerações sem melhorar (0 = desativado)",
    )


class ConstraintsConfig(BaseModel):
    """Modo de cada restrição configurável.

    Contaminação/refrigeração e a autonomia do carro elétrico são sempre rígidas.
    """

    capacity: ConstraintMode = Field(
        "hard", description="hard: nunca excede a capacidade; soft: excesso é permitido, com custo soft_penalty por kg"
    )
    autonomy: ConstraintMode = Field(
        "hard",
        description="hard: nunca excede a autonomia; soft: excesso é permitido, com custo soft_penalty por km "
        "(exceto carro elétrico, sempre hard)",
    )
    priority: ConstraintMode = Field(
        "soft",
        description="soft: entregas críticas tendem a vir antes (peso priority_weight); "
        "hard: em cada veículo, todas as críticas são feitas antes de qualquer regular",
    )
    time_window: ConstraintMode = Field(
        "soft",
        description="hard: nunca chega depois do fim da janela; soft: atraso permitido, com custo soft_penalty por minuto",
    )
    soft_penalty: float = Field(
        50, ge=0, description="Custo, em km equivalentes, de cada kg, km ou minuto excedido em restrições soft"
    )


class OptimizeRequest(BaseModel):
    locations: list[Location] = Field(..., min_length=2)
    origin_index: int = Field(0, ge=0)
    destination_index: int = Field(0, ge=0)
    vehicles: list[VehicleIn] = Field(default_factory=default_fleet, min_length=1, max_length=10)
    schedule: ScheduleConfig = ScheduleConfig()
    ga: GAConfig = GAConfig()
    constraints: ConstraintsConfig = ConstraintsConfig()
    use_llm: bool = True
    previous_result_ids: list[str] = Field(
        default_factory=list, max_length=50,
        description="Execuções anteriores desta sessão; as que tiverem as mesmas cidades viram contexto da LLM técnica",
    )

    @model_validator(mode="after")
    def _check_indices(self):
        n = len(self.locations)
        if self.origin_index >= n or self.destination_index >= n:
            raise ValueError("origin_index/destination_index fora do intervalo de locations")
        stops = n - (1 if self.origin_index == self.destination_index else 2)
        if stops < 1:
            raise ValueError("É necessário ao menos um ponto de entrega além da origem/destino")
        return self


class DefaultOptimizeRequest(BaseModel):
    vehicles: list[VehicleIn] = Field(default_factory=default_fleet, min_length=1, max_length=10)
    schedule: ScheduleConfig = ScheduleConfig()
    ga: GAConfig = GAConfig()
    constraints: ConstraintsConfig = ConstraintsConfig()
    use_llm: bool = True
    previous_result_ids: list[str] = Field(
        default_factory=list, max_length=50,
        description="Execuções anteriores desta sessão; as que tiverem as mesmas cidades viram contexto da LLM técnica",
    )


class StopSchedule(BaseModel):
    location: int
    arrival: str = Field(description="Chegada prevista (HH:MM; '+1d' se passar da meia-noite)")
    departure: str
    wait_min: float = Field(description="Espera até a janela abrir")
    late_min: float = Field(description="Atraso em relação ao fim da janela")
    refuel: bool = Field(False, description="Abastece nesta cidade antes de seguir")


class RefuelInfo(BaseModel):
    location: int | None = Field(description="Índice em locations da cidade da rota onde abastece (null = no caminho)")
    en_route: bool = Field(False, description="Parada no caminho de um trecho longo (não numa cidade de entrega)")
    place: str = Field(description="Onde abastecer: cidade da rota ou município no caminho (estimado pela rota)")
    leg_from: int | None = Field(None, description="Trecho da parada no caminho: cidade de partida (índice)")
    leg_to: int | None = Field(None, description="Trecho da parada no caminho: cidade de chegada (índice)")
    km_into_leg: float = Field(0, description="Km desde o início do trecho até a parada no caminho")
    lat: float | None = None
    lng: float | None = None
    time: str = Field(description="Início do abastecimento (HH:MM)")
    km_since_refuel: float = Field(description="Km rodados desde o último tanque cheio")
    range_left_pct: float = Field(description="Autonomia restante ao parar (% do tanque)")


class VehicleRoute(BaseModel):
    vehicle_id: int
    vehicle_type: VehicleType | Literal["generico"]
    vehicle_name: str
    refrigerated: bool
    strict_autonomy: bool
    stops: list[int] = Field(description="Índices em locations, incluindo origem e destino")
    distance_km: float
    duration_min: float = Field(description="Da saída até a chegada ao destino, com esperas e descargas")
    load: float
    capacity: float
    autonomy_km: float
    groups: list[str] = Field(default_factory=list, description="Grupos de medicamentos/insumos transportados")
    schedule: list[StopSchedule] = Field(default_factory=list)
    refuels: list[RefuelInfo] = Field(default_factory=list, description="Paradas para abastecimento, na ordem")
    return_time: str | None = None
    capacity_ok: bool
    autonomy_ok: bool
    priority_ok: bool = Field(description="Todas as entregas críticas do veículo vêm antes das regulares")
    contamination_ok: bool = Field(description="Sem produtos incompatíveis juntos e refrigeração adequada")
    time_ok: bool = Field(description="Todas as entregas dentro da janela de recebimento")
    polyline: list[list[float]] = Field(default_factory=list, description="[[lat, lng], ...]")


class StrategyResult(BaseModel):
    distance_km: float
    fitness: float
    feasible: bool


class Comparison(BaseModel):
    genetic_algorithm: StrategyResult
    nearest_neighbor: StrategyResult
    random_average: StrategyResult
    improvement_vs_nearest_neighbor_pct: float
    improvement_vs_random_pct: float


class LLMOutput(BaseModel):
    generated: bool
    provider: str
    model: str | None = None
    content: str
    error: str | None = None


class OptimizeResponse(BaseModel):
    id: str
    distance_source: str
    locations: list[Location]
    origin_index: int
    destination_index: int
    vehicles: list[VehicleRoute]
    total_distance_km: float
    total_duration_min: float
    fitness: float
    feasible: bool = Field(description="Nenhuma restrição hard foi violada")
    ga: GAConfig
    constraints: ConstraintsConfig
    schedule: ScheduleConfig
    transport_rules: list[str]
    soft_violations: list[str] = Field(default_factory=list, description="Violações aceitas em restrições soft")
    fitness_history: list[float]
    generations: int = Field(description="Gerações efetivamente executadas")
    stopped_by_stagnation: bool = Field(False, description="Parou antes do limite pelo critério de estagnação")
    execution_time_ms: float
    comparison: Comparison
    llm_technical: LLMOutput | None = Field(None, description="Análise técnica e sugestões de parâmetros (equipe de testes)")
    llm_driver: LLMOutput | None = Field(None, description="Instruções para os motoristas")
    previous_result_ids: list[str] = Field(
        default_factory=list,
        description="Execuções anteriores com as mesmas cidades usadas como contexto pela LLM técnica (da mais antiga à mais recente)",
    )


class AskRequest(BaseModel):
    result_id: str
    question: str = Field(..., min_length=2, max_length=1000)
    audience: Literal["technical", "driver"] = Field(
        "driver", description="technical: perguntas sobre execução e parâmetros; driver: perguntas sobre o trajeto"
    )


class AskResponse(BaseModel):
    answer: LLMOutput


UF = Literal[
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR",
    "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
]


class StateInfo(BaseModel):
    uf: UF
    name: str


class GeocodeRequest(BaseModel):
    city: str = Field(..., min_length=2, max_length=80)
    state: UF


class GeocodeResponse(BaseModel):
    name: str = Field(description="Nome no formato 'Cidade, UF'")
    lat: float
    lng: float
    address: str | None = None
    source: Literal["google_maps", "catalogo_local"]


class CitiesResponse(BaseModel):
    distance_source: str
    cities: list[Location]


class ProductGroupInfo(BaseModel):
    code: ProductGroup
    name: str
    refrigerated: bool
    incompatible_with: list[str]
    rule: str


class VehicleTypeInfo(BaseModel):
    type: VehicleType
    name: str
    autonomy_km: float
    capacity: float
    refrigerated: bool
    strict_autonomy: bool


class CatalogResponse(BaseModel):
    product_groups: list[ProductGroupInfo]
    vehicle_types: list[VehicleTypeInfo]
    transport_rules: list[str]
    default_fleet: list[VehicleType]
