"""Catálogo de cidades reais usadas no problema.

As coordenadas abaixo são aproximadas e servem apenas como fallback quando não há
chave do Google Maps. Com a chave configurada, cada cidade é geocodificada pela
Geocoding API e as distâncias reais por estrada vêm da Routes API.
"""
import random

from app.data.catalog import PRODUCT_GROUPS, TIME_WINDOWS

# Cenário padrão: 5 cidades fixas. A primeira é o hub (Hospital Central),
# usado como origem e destino padrão.
# Grupo de medicamento/insumo e janela de recebimento são fixos neste cenário.
DEFAULT_CITIES: list[dict] = [
    {"name": "São Paulo, SP", "lat": -23.5505, "lng": -46.6333, "priority": "regular", "demand": 0,
     "product_group": "D", "window_start": "00:00", "window_end": "23:59"},
    {"name": "Campinas, SP", "lat": -22.9056, "lng": -47.0608, "priority": "critical", "demand": 30,
     "product_group": "A", "window_start": "08:00", "window_end": "12:00"},
    {"name": "Santos, SP", "lat": -23.9608, "lng": -46.3336, "priority": "regular", "demand": 40,
     "product_group": "C", "window_start": "09:00", "window_end": "17:00"},
    {"name": "Sorocaba, SP", "lat": -23.5015, "lng": -47.4526, "priority": "critical", "demand": 25,
     "product_group": "B", "window_start": "07:00", "window_end": "11:00"},
    {"name": "São José dos Campos, SP", "lat": -23.2237, "lng": -45.9009, "priority": "regular", "demand": 35,
     "product_group": "D", "window_start": "13:00", "window_end": "18:00"},
]

# Pool de cidades para o modo aleatório (estado de SP e arredores).
CITY_POOL: list[dict] = [
    {"name": "São Paulo, SP", "lat": -23.5505, "lng": -46.6333},
    {"name": "Campinas, SP", "lat": -22.9056, "lng": -47.0608},
    {"name": "Santos, SP", "lat": -23.9608, "lng": -46.3336},
    {"name": "Sorocaba, SP", "lat": -23.5015, "lng": -47.4526},
    {"name": "São José dos Campos, SP", "lat": -23.2237, "lng": -45.9009},
    {"name": "Jundiaí, SP", "lat": -23.1857, "lng": -46.8978},
    {"name": "Guarulhos, SP", "lat": -23.4543, "lng": -46.5337},
    {"name": "Osasco, SP", "lat": -23.5329, "lng": -46.7917},
    {"name": "Santo André, SP", "lat": -23.6639, "lng": -46.5383},
    {"name": "São Bernardo do Campo, SP", "lat": -23.6914, "lng": -46.5646},
    {"name": "Mogi das Cruzes, SP", "lat": -23.5208, "lng": -46.1854},
    {"name": "Taubaté, SP", "lat": -23.0264, "lng": -45.5553},
    {"name": "Jacareí, SP", "lat": -23.3053, "lng": -45.9658},
    {"name": "Piracicaba, SP", "lat": -22.7253, "lng": -47.6492},
    {"name": "Limeira, SP", "lat": -22.5647, "lng": -47.4017},
    {"name": "Americana, SP", "lat": -22.7374, "lng": -47.3331},
    {"name": "Indaiatuba, SP", "lat": -23.0882, "lng": -47.2234},
    {"name": "Itu, SP", "lat": -23.2642, "lng": -47.2992},
    {"name": "Bragança Paulista, SP", "lat": -22.9527, "lng": -46.5419},
    {"name": "Atibaia, SP", "lat": -23.1171, "lng": -46.5563},
    {"name": "Guarujá, SP", "lat": -23.9888, "lng": -46.2567},
    {"name": "São Vicente, SP", "lat": -23.9631, "lng": -46.3919},
    {"name": "Praia Grande, SP", "lat": -24.0058, "lng": -46.4028},
    {"name": "Bertioga, SP", "lat": -23.8545, "lng": -46.1386},
    {"name": "Caraguatatuba, SP", "lat": -23.6203, "lng": -45.4131},
    {"name": "Ubatuba, SP", "lat": -23.4336, "lng": -45.0838},
    {"name": "Registro, SP", "lat": -24.4875, "lng": -47.8436},
    {"name": "Itapetininga, SP", "lat": -23.5917, "lng": -48.0531},
    {"name": "Botucatu, SP", "lat": -22.8858, "lng": -48.4450},
    {"name": "Rio Claro, SP", "lat": -22.4149, "lng": -47.5651},
    {"name": "São Carlos, SP", "lat": -22.0087, "lng": -47.8909},
    {"name": "Araraquara, SP", "lat": -21.7845, "lng": -48.1780},
    {"name": "Ribeirão Preto, SP", "lat": -21.1775, "lng": -47.8103},
    {"name": "Franca, SP", "lat": -20.5386, "lng": -47.4008},
    {"name": "Bauru, SP", "lat": -22.3246, "lng": -49.0871},
    {"name": "Jaú, SP", "lat": -22.2936, "lng": -48.5592},
    {"name": "Marília, SP", "lat": -22.2171, "lng": -49.9501},
    {"name": "São João da Boa Vista, SP", "lat": -21.9707, "lng": -46.7944},
    {"name": "Mogi Guaçu, SP", "lat": -22.3675, "lng": -46.9428},
    {"name": "Sumaré, SP", "lat": -22.8219, "lng": -47.2669},
    {"name": "Hortolândia, SP", "lat": -22.8583, "lng": -47.2200},
    {"name": "Valinhos, SP", "lat": -22.9706, "lng": -46.9958},
    {"name": "Vinhedo, SP", "lat": -23.0300, "lng": -46.9750},
    {"name": "Salto, SP", "lat": -23.2003, "lng": -47.2869},
    {"name": "Votorantim, SP", "lat": -23.5467, "lng": -47.4378},
    {"name": "Cotia, SP", "lat": -23.6039, "lng": -46.9192},
    {"name": "Barueri, SP", "lat": -23.5057, "lng": -46.8790},
    {"name": "Suzano, SP", "lat": -23.5425, "lng": -46.3108},
    {"name": "Itaquaquecetuba, SP", "lat": -23.4864, "lng": -46.3483},
    {"name": "Pindamonhangaba, SP", "lat": -22.9246, "lng": -45.4613},
    {"name": "Guaratinguetá, SP", "lat": -22.8075, "lng": -45.1925},
    {"name": "Lorena, SP", "lat": -22.7311, "lng": -45.1247},
]

MAX_RANDOM_CITIES = len(CITY_POOL)


def sample_random_cities(count: int, rng: random.Random | None = None) -> list[dict]:
    """Sorteia `count` cidades do pool com prioridade, demanda (kg), grupo e janela aleatórios."""
    rng = rng or random.Random()
    chosen = rng.sample(CITY_POOL, count)
    cities = []
    for city in chosen:
        start, end = rng.choice(TIME_WINDOWS)
        cities.append({
            **city,
            "priority": "critical" if rng.random() < 0.3 else "regular",
            "demand": rng.randint(5, 50),
            "product_group": rng.choice(list(PRODUCT_GROUPS)),
            "window_start": start,
            "window_end": end,
        })
    return cities
