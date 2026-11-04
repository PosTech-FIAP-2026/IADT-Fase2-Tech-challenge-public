"""Cliente do Google Maps Platform (Geocoding API + Routes API).

* Geocoding API: converte o nome da cidade em coordenadas reais e endereço formatado.
* Routes API (computeRouteMatrix): distâncias e tempos reais por estrada entre todos os pares.
* Routes API (computeRoutes): geometria (polyline) das rotas de cada veículo para o mapa.
"""
import logging
import re

import httpx

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
ROUTE_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

MATRIX_BLOCK = 25        # 25 origens x 25 destinos = 625 elementos (limite da API)
MAX_INTERMEDIATES = 25   # limite de waypoints intermediários do computeRoutes


class GoogleMapsError(RuntimeError):
    pass


def _waypoint(lat: float, lng: float) -> dict:
    return {"location": {"latLng": {"latitude": lat, "longitude": lng}}}


def _parse_duration(value: str | None) -> float:
    """Converte '1234s' em minutos."""
    if not value:
        return 0.0
    match = re.match(r"([\d.]+)s", value)
    return float(match.group(1)) / 60 if match else 0.0


def decode_polyline(encoded: str) -> list[list[float]]:
    """Decodifica o Encoded Polyline Algorithm Format do Google."""
    points, index, lat, lng = [], 0, 0, 0
    while index < len(encoded):
        for is_lng in (False, True):
            shift, result = 0, 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lng:
                lng += delta
            else:
                lat += delta
        points.append([lat / 1e5, lng / 1e5])
    return points


class GoogleMapsClient:
    def __init__(self, api_key: str, timeout: float = 30.0):
        self.api_key = api_key
        self.timeout = timeout
        self._geocode_cache: dict[str, dict] = {}
        self._reverse_cache: dict[tuple[float, float], str | None] = {}

    def geocode(self, address: str) -> dict:
        if address in self._geocode_cache:
            return self._geocode_cache[address]
        resp = httpx.get(
            GEOCODE_URL,
            params={"address": address, "key": self.api_key, "language": "pt-BR", "region": "br"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "OK" or not data.get("results"):
            raise GoogleMapsError(f"Geocoding falhou para '{address}': {data.get('status')} {data.get('error_message', '')}")
        first = data["results"][0]
        components = first.get("address_components", [])

        def component(kind: str) -> str | None:
            return next((c.get("short_name") for c in components if kind in c.get("types", [])), None)

        result = {
            "lat": first["geometry"]["location"]["lat"],
            "lng": first["geometry"]["location"]["lng"],
            "address": first.get("formatted_address"),
            # Usados para validar o cadastro manual (município no estado certo).
            "types": first.get("types", []),
            "state": component("administrative_area_level_1"),
            "country": component("country"),
        }
        self._geocode_cache[address] = result
        return result

    def reverse_geocode(self, lat: float, lng: float) -> str | None:
        """Município ('Cidade, UF') onde fica o ponto, ou None se não houver."""
        key = (round(lat, 3), round(lng, 3))
        if key in self._reverse_cache:
            return self._reverse_cache[key]
        resp = httpx.get(
            GEOCODE_URL,
            params={"latlng": f"{lat},{lng}", "key": self.api_key, "language": "pt-BR",
                    "result_type": "locality|administrative_area_level_2"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
        name = None
        if results:
            comps = results[0].get("address_components", [])
            city = next((c["long_name"] for c in comps
                         if "locality" in c["types"] or "administrative_area_level_2" in c["types"]), None)
            uf = next((c["short_name"] for c in comps if "administrative_area_level_1" in c["types"]), None)
            name = f"{city}, {uf}" if city and uf else city
        self._reverse_cache[key] = name
        return name

    def route_matrix(self, coords: list[tuple[float, float]]) -> tuple[list[list[float]], list[list[float]]]:
        """Retorna (distâncias km, durações min) NxN via computeRouteMatrix, em blocos de 25x25."""
        n = len(coords)
        dist = [[0.0] * n for _ in range(n)]
        dur = [[0.0] * n for _ in range(n)]
        headers = {
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "originIndex,destinationIndex,distanceMeters,duration,condition,status",
            "Content-Type": "application/json",
        }
        for o_start in range(0, n, MATRIX_BLOCK):
            origins = coords[o_start : o_start + MATRIX_BLOCK]
            for d_start in range(0, n, MATRIX_BLOCK):
                destinations = coords[d_start : d_start + MATRIX_BLOCK]
                body = {
                    "origins": [{"waypoint": _waypoint(*c)} for c in origins],
                    "destinations": [{"waypoint": _waypoint(*c)} for c in destinations],
                    "travelMode": "DRIVE",
                    "routingPreference": "TRAFFIC_UNAWARE",
                }
                resp = httpx.post(ROUTE_MATRIX_URL, json=body, headers=headers, timeout=self.timeout)
                if resp.status_code != 200:
                    raise GoogleMapsError(f"Routes API (matrix) retornou {resp.status_code}: {resp.text[:300]}")
                for el in resp.json():
                    i = o_start + el.get("originIndex", 0)
                    j = d_start + el.get("destinationIndex", 0)
                    if i == j:
                        continue
                    if el.get("condition") != "ROUTE_EXISTS":
                        raise GoogleMapsError(f"Sem rota por estrada entre os pontos {i} e {j}")
                    dist[i][j] = el.get("distanceMeters", 0) / 1000
                    dur[i][j] = _parse_duration(el.get("duration"))
        return dist, dur

    def route_polyline(self, coords: list[tuple[float, float]]) -> list[list[float]]:
        """Geometria da rota que passa por `coords` na ordem dada."""
        if len(coords) < 2:
            return []
        headers = {
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "routes.polyline.encodedPolyline",
            "Content-Type": "application/json",
        }
        points: list[list[float]] = []
        step = MAX_INTERMEDIATES + 1
        for start in range(0, len(coords) - 1, step):
            chunk = coords[start : start + step + 1]
            body = {
                "origin": _waypoint(*chunk[0]),
                "destination": _waypoint(*chunk[-1]),
                "intermediates": [_waypoint(*c) for c in chunk[1:-1]],
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_UNAWARE",
                "polylineQuality": "OVERVIEW",
            }
            resp = httpx.post(ROUTES_URL, json=body, headers=headers, timeout=self.timeout)
            if resp.status_code != 200:
                raise GoogleMapsError(f"Routes API (routes) retornou {resp.status_code}: {resp.text[:300]}")
            routes = resp.json().get("routes") or []
            if routes:
                points.extend(decode_polyline(routes[0]["polyline"]["encodedPolyline"]))
        return points
