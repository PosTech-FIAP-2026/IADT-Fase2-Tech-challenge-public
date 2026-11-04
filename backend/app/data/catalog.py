"""Cadastro fixo de grupos de medicamentos/insumos, regras de transporte e tipos de veículo."""

PRODUCT_GROUPS: dict[str, dict] = {
    "A": {
        "name": "Medicamentos/Insumos A",
        "refrigerated": False,
        "incompatible_with": ["C"],
        "rule": "Não pode ser transportado junto com Medicamentos/Insumos C (risco de contaminação).",
    },
    "B": {
        "name": "Medicamentos/Insumos B",
        "refrigerated": True,
        "incompatible_with": ["A", "C", "D", "E"],  # deve viajar sozinho
        "rule": "Deve ser transportado sozinho, pois necessita refrigeração (somente caminhão com refrigeração).",
    },
    "C": {
        "name": "Medicamentos/Insumos C",
        "refrigerated": False,
        "incompatible_with": ["A"],
        "rule": "Não pode ser transportado junto com Medicamentos/Insumos A (risco de contaminação).",
    },
    "D": {"name": "Medicamentos/Insumos D", "refrigerated": False, "incompatible_with": [], "rule": ""},
    "E": {"name": "Medicamentos/Insumos E", "refrigerated": False, "incompatible_with": [], "rule": ""},
}

# Regras exibidas em destaque na tela e enviadas à LLM.
TRANSPORT_RULES: list[str] = [
    "Medicamentos/Insumos A não pode ser transportado junto com Medicamentos/Insumos C (risco de contaminação).",
    "Medicamentos/Insumos B deve ser transportado sozinho, pois necessita refrigeração "
    "(somente em caminhão com refrigeração).",
]

# Pares incompatíveis (sem repetição), derivados do cadastro: {("A","B"), ("A","C"), ...}
INCOMPATIBLE_PAIRS: set[tuple[str, str]] = {
    tuple(sorted((g, other))) for g, info in PRODUCT_GROUPS.items() for other in info["incompatible_with"]
}

VEHICLE_TYPES: dict[str, dict] = {
    "moto": {"name": "Moto", "autonomy_km": 140, "capacity": 15, "refrigerated": False, "strict_autonomy": False},
    "carro": {"name": "Carro", "autonomy_km": 500, "capacity": 250, "refrigerated": False, "strict_autonomy": False},
    "carro_eletrico": {
        "name": "Carro elétrico", "autonomy_km": 300, "capacity": 300, "refrigerated": False,
        "strict_autonomy": True,  # nunca pode estourar a autonomia (sem recarga no caminho)
    },
    "caminhao_refrigerado": {
        "name": "Caminhão com refrigeração", "autonomy_km": 600, "capacity": 1000, "refrigerated": True,
        "strict_autonomy": False,
    },
    "caminhao": {
        "name": "Caminhão sem refrigeração", "autonomy_km": 700, "capacity": 1000, "refrigerated": False,
        "strict_autonomy": False,
    },
}

# Frota padrão: um veículo de cada tipo.
DEFAULT_FLEET: list[str] = list(VEHICLE_TYPES)

# Janelas de recebimento usadas no sorteio de cidades (início, fim).
TIME_WINDOWS: list[tuple[str, str]] = [
    ("07:00", "12:00"), ("08:00", "12:00"), ("08:00", "18:00"), ("10:00", "16:00"),
    ("13:00", "18:00"), ("14:00", "20:00"), ("07:00", "19:00"),
]


def incompatible(g1: str, g2: str) -> bool:
    return tuple(sorted((g1, g2))) in INCOMPATIBLE_PAIRS


def needs_refrigeration(group: str) -> bool:
    return PRODUCT_GROUPS.get(group, {}).get("refrigerated", False)
