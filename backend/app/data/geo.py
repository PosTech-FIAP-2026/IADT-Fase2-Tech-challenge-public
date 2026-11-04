"""Estados brasileiros e municípios (API pública de localidades do IBGE) para o cadastro manual."""
import logging
import unicodedata
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)

IBGE_MUNICIPIOS_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"

STATES: dict[str, str] = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia", "CE": "Ceará",
    "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso",
    "MS": "Mato Grosso do Sul", "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
    "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
    "SE": "Sergipe", "TO": "Tocantins",
}


def normalize(text: str) -> str:
    """Minúsculas e sem acentos, para comparar nomes de cidades."""
    decomposed = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


@lru_cache(maxsize=32)
def municipalities(uf: str) -> tuple[str, ...]:
    """Nomes dos municípios do estado, em ordem alfabética (cacheado por UF)."""
    resp = httpx.get(IBGE_MUNICIPIOS_URL.format(uf=uf), params={"orderBy": "nome"}, timeout=20)
    resp.raise_for_status()
    return tuple(m["nome"] for m in resp.json())
