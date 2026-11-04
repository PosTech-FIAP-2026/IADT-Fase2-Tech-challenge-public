"""Provedores de distância: Google Maps (real) ou haversine (fallback offline)."""
import logging
import math

from app.config import settings
from app.data.cities import CITY_POOL
from app.data.geo import normalize
from app.services.google_maps import GoogleMapsClient, GoogleMapsError

logger = logging.getLogger(__name__)

ROAD_FACTOR = 1.3      # estradas são ~30% mais longas que a linha reta
AVG_SPEED_KMH = 60.0


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def haversine_matrix(coords: list[tuple[float, float]]) -> tuple[list[list[float]], list[list[float]]]:
    dist = [[haversine_km(a, b) * ROAD_FACTOR for b in coords] for a in coords]
    dur = [[d / AVG_SPEED_KMH * 60 for d in row] for row in dist]
    return dist, dur


class DistanceService:
    """Fachada que usa o Google quando há chave e cai para haversine em caso de falha."""

    def __init__(self, google: GoogleMapsClient | None = None):
        self.google = google

    @property
    def source(self) -> str:
        return "google_maps" if self.google else "haversine"

    def geocode_city(self, city: dict) -> dict:
        if not self.google:
            return city
        try:
            geo = self.google.geocode(f"{city['name']}, Brasil")
            return {**city, **geo}
        except (GoogleMapsError, Exception) as exc:  # noqa: BLE001 - mantém o fallback
            logger.warning("Geocoding falhou, usando coordenadas locais: %s", exc)
            return city

    def geocode_place(self, city: str, state: str) -> dict | None:
        """Localiza um município pelo nome e UF (cadastro manual).

        Com Google: aceita só resultado que seja município (locality/administrative_area_level_2)
        no Brasil e no estado informado, para não aceitar um lugar errado em silêncio.
        Sem Google: procura no catálogo local de cidades (somente SP).
        """
        name = f"{city.strip()}, {state}"
        if self.google:
            try:
                geo = self.google.geocode(f"{city.strip()}, {state}, Brasil")
            except GoogleMapsError:
                return None  # ZERO_RESULTS e afins: não encontrado
            except Exception as exc:  # noqa: BLE001 - rede/HTTP: tenta o catálogo local
                logger.warning("Geocoding indisponível, usando catálogo local: %s", exc)
            else:
                is_city = any(t in geo.get("types", []) for t in ("locality", "administrative_area_level_2"))
                if geo.get("country") == "BR" and geo.get("state") == state and is_city:
                    return {"name": name, "lat": geo["lat"], "lng": geo["lng"], "address": geo.get("address"),
                            "source": "google_maps"}
                return None
        target = normalize(f"{city}, {state}")
        for c in CITY_POOL:
            if normalize(c["name"]) == target:
                return {"name": c["name"], "lat": c["lat"], "lng": c["lng"], "address": None, "source": "catalogo_local"}
        return None

    def matrix(self, coords: list[tuple[float, float]]) -> tuple[list[list[float]], list[list[float]], str]:
        if self.google:
            try:
                dist, dur = self.google.route_matrix(coords)
                return dist, dur, "google_maps"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Routes API falhou, usando haversine: %s", exc)
        dist, dur = haversine_matrix(coords)
        return dist, dur, "haversine"

    def leg_geometry(self, a: tuple[float, float], b: tuple[float, float]) -> list[list[float]]:
        """Geometria do trecho a -> b pela estrada (Google) ou linha reta (fallback)."""
        if self.google:
            try:
                pts = self.google.route_polyline([a, b])
                if len(pts) >= 2:
                    return pts
            except Exception as exc:  # noqa: BLE001
                logger.warning("Geometria do trecho indisponível, usando linha reta: %s", exc)
        return [list(a), list(b)]

    def place_name(self, lat: float, lng: float) -> str | None:
        """Município do ponto (Google) ou None sem chave/sem resultado."""
        if not self.google:
            return None
        try:
            return self.google.reverse_geocode(lat, lng)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Geocodificação reversa falhou: %s", exc)
            return None

    def polyline(self, coords: list[tuple[float, float]]) -> list[list[float]]:
        if self.google:
            try:
                return self.google.route_polyline(coords)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Polyline indisponível, usando linhas retas: %s", exc)
        return [[lat, lng] for lat, lng in coords]


def build_distance_service() -> DistanceService:
    key = settings.google_maps_api_key
    return DistanceService(GoogleMapsClient(key, timeout=settings.http_timeout_seconds) if key else None)
