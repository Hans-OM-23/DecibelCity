"""Búsqueda (Photon) y verificación (Nominatim) de lugares de OpenStreetMap, desde el servidor.

Ninguno de los dos servicios requiere clave. Los servidores públicos tienen políticas de uso
justo: identificación de la aplicación, una petición por segundo en Nominatim y atribución
visible de OpenStreetMap. Ambas URL se pueden sustituir por instancias propias.
"""

import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from schemas.maps import MapBounds, PlaceSuggestion

# Identificador OSM compacto: N (nodo), W (vía) o R (relación) seguido del número.
OSM_PLACE_ID_PATTERN = re.compile(r"[NWR][0-9]{1,19}")
# Rango 25 o más: plazas, calles, edificios y comercios. Excluye barrios, distritos y ciudades.
MIN_PLACE_RANK = 25
GENERIC_PLACE_VALUES = {
    "city", "town", "village", "hamlet", "suburb", "quarter", "neighbourhood", "city_block",
    "county", "district", "state", "region", "province", "country", "continent", "island",
    "islet", "municipality", "borough", "locality",
}
TIMEOUT = httpx.Timeout(8.0, connect=4.0)


@dataclass(frozen=True)
class VerifiedPlace:
    place_id: str
    address: str
    latitude: float
    longitude: float


class PlaceVerificationError(Exception):
    """Error seguro para mostrar al usuario, sin la respuesta del proveedor."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


class PlaceVerifier(Protocol):
    def verify(self, place_id: str) -> VerifiedPlace: ...


class PlaceSearcher(Protocol):
    def search(self, query: str) -> list[PlaceSuggestion]: ...


class _Throttle:
    """Separa las peticiones al menos `interval` segundos (uso justo de servidores públicos)."""

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            delay = self._last + self._interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


def _is_coordinate(value: object, limit: float) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and -limit <= value <= limit


class NominatimVerifier:
    def __init__(self, base_url: str, user_agent: str, client: httpx.Client, *, min_interval: float = 1.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent
        self._client = client
        self._bounds = MapBounds()
        self._throttle = _Throttle(min_interval)

    def verify(self, place_id: str) -> VerifiedPlace:
        if not OSM_PLACE_ID_PATTERN.fullmatch(place_id):
            raise PlaceVerificationError("Selecciona un lugar válido de las sugerencias.")
        self._throttle.wait()
        try:
            response = self._client.get(
                f"{self._base_url}/lookup",
                params={"osm_ids": place_id, "format": "jsonv2", "addressdetails": "1", "accept-language": "es"},
                headers={"User-Agent": self._user_agent},
                timeout=TIMEOUT,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise PlaceVerificationError("El servicio de mapas no respondió a tiempo. Intenta nuevamente.", 503) from None
        except httpx.RequestError:
            raise PlaceVerificationError("No se pudo verificar la ubicación. Intenta nuevamente.", 502) from None

        if response.status_code in (403, 429) or response.status_code >= 500:
            raise PlaceVerificationError("La verificación de ubicaciones no está disponible. Intenta más tarde.", 503)
        if response.status_code != 200:
            raise PlaceVerificationError("No se pudo verificar la ubicación. Intenta nuevamente.", 502)
        try:
            data = response.json()
        except ValueError:
            raise PlaceVerificationError("El servicio de mapas devolvió una respuesta que no se pudo validar.", 502) from None
        if not isinstance(data, list):
            raise PlaceVerificationError("El servicio de mapas devolvió una respuesta que no se pudo validar.", 502)
        if not data:
            raise PlaceVerificationError("El lugar seleccionado no existe o ya no está disponible.")
        if len(data) != 1:
            raise PlaceVerificationError("El servicio de mapas devolvió un lugar que no se pudo verificar.", 502)
        return self._validate_place(data[0], place_id)

    def _validate_place(self, data: object, requested_id: str) -> VerifiedPlace:
        invalid_message = "Selecciona una calle, plaza o establecimiento específico dentro de Huancayo."
        if not isinstance(data, dict):
            raise PlaceVerificationError("El servicio de mapas devolvió una respuesta que no se pudo validar.", 502)
        osm_type, osm_id = data.get("osm_type"), data.get("osm_id")
        if not isinstance(osm_type, str) or type(osm_id) is not int or f"{osm_type[:1].upper()}{osm_id}" != requested_id:
            raise PlaceVerificationError("El servicio de mapas devolvió un lugar que no se pudo verificar.", 502)
        address = data.get("display_name")
        if not isinstance(address, str) or not address.strip() or len(address.strip()) > 1000:
            raise PlaceVerificationError(invalid_message)
        rank = data.get("place_rank")
        if type(rank) is not int or rank < MIN_PLACE_RANK or data.get("category") == "boundary":
            raise PlaceVerificationError(invalid_message)
        if data.get("category") == "place" and data.get("type") in GENERIC_PLACE_VALUES:
            raise PlaceVerificationError(invalid_message)
        try:
            latitude, longitude = float(data.get("lat")), float(data.get("lon"))  # Nominatim las envía como texto.
        except (TypeError, ValueError):
            raise PlaceVerificationError(invalid_message) from None
        if not _is_coordinate(latitude, 90) or not _is_coordinate(longitude, 180):
            raise PlaceVerificationError(invalid_message)
        details = data.get("address")
        is_peru = isinstance(details, dict) and details.get("country_code") == "pe"
        if not is_peru or not self._bounds.contains(latitude, longitude):
            raise PlaceVerificationError("El lugar debe estar dentro del área de cobertura de Huancayo, Perú.")
        return VerifiedPlace(requested_id, address.strip(), latitude, longitude)


class PhotonSearcher:
    def __init__(self, base_url: str, user_agent: str, client: httpx.Client, *, limit: int = 8) -> None:
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent
        self._client = client
        self._bounds = MapBounds()
        self._limit = limit

    def search(self, query: str) -> list[PlaceSuggestion]:
        bounds = self._bounds
        try:
            response = self._client.get(
                f"{self._base_url}/api/",
                params={
                    "q": query, "limit": str(self._limit * 2),
                    "bbox": f"{bounds.west},{bounds.south},{bounds.east},{bounds.north}",
                },
                headers={"User-Agent": self._user_agent},
                timeout=TIMEOUT,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise PlaceVerificationError("La búsqueda de lugares no respondió a tiempo. Intenta nuevamente.", 503) from None
        except httpx.RequestError:
            raise PlaceVerificationError("No se pudo conectar con la búsqueda de lugares. Intenta nuevamente.", 502) from None
        if response.status_code in (403, 429) or response.status_code >= 500:
            raise PlaceVerificationError("La búsqueda de lugares no está disponible. Intenta más tarde.", 503)
        if response.status_code != 200:
            raise PlaceVerificationError("No se pudo completar la búsqueda de lugares.", 502)
        try:
            features = response.json().get("features")
        except (ValueError, AttributeError):
            raise PlaceVerificationError("La búsqueda devolvió una respuesta que no se pudo validar.", 502) from None
        if not isinstance(features, list):
            raise PlaceVerificationError("La búsqueda devolvió una respuesta que no se pudo validar.", 502)
        suggestions: list[PlaceSuggestion] = []
        seen: set[str] = set()
        for feature in features:
            suggestion = self._to_suggestion(feature)
            if suggestion and suggestion.place_id not in seen:
                seen.add(suggestion.place_id)
                suggestions.append(suggestion)
            if len(suggestions) == self._limit:
                break
        return suggestions

    def _to_suggestion(self, feature: object) -> PlaceSuggestion | None:
        if not isinstance(feature, dict):
            return None
        properties, geometry = feature.get("properties"), feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            return None
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            return None
        longitude, latitude = coordinates
        if not _is_coordinate(latitude, 90) or not _is_coordinate(longitude, 180):
            return None
        if properties.get("countrycode") != "PE" or not self._bounds.contains(latitude, longitude):
            return None
        key, value = properties.get("osm_key"), properties.get("osm_value")
        if key == "boundary" or (key == "place" and value in GENERIC_PLACE_VALUES):
            return None
        place_id = f"{properties.get('osm_type', '')}{properties.get('osm_id', '')}"
        if not OSM_PLACE_ID_PATTERN.fullmatch(place_id):
            return None
        street = " ".join(str(part) for part in (properties.get("street"), properties.get("housenumber")) if part)
        name = str(properties.get("name") or street or "").strip()
        if not name:
            return None
        details = [street if street != name else "", properties.get("district") or properties.get("locality"), properties.get("city")]
        address = ", ".join(dict.fromkeys(str(part).strip() for part in details if part and str(part).strip()))
        return PlaceSuggestion(place_id=place_id, name=name[:200], address=address[:400],
                               lat=float(latitude), lng=float(longitude))
