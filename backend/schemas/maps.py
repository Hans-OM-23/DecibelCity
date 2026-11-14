"""Configuración pública del mapa y área operativa de DecibelCity."""

from pydantic import BaseModel, Field


class MapCenter(BaseModel):
    lat: float = -12.067
    lng: float = -75.210


class MapBounds(BaseModel):
    south: float = -12.16
    west: float = -75.30
    north: float = -11.95
    east: float = -75.12

    def contains(self, latitude: float, longitude: float) -> bool:
        return self.south <= latitude <= self.north and self.west <= longitude <= self.east


class PublicMapsConfig(BaseModel):
    """Datos públicos para el navegador. OpenStreetMap no utiliza claves."""

    tile_url: str
    attribution: str
    center: MapCenter
    bounds: MapBounds


class PlaceSuggestion(BaseModel):
    """Sugerencia de búsqueda. Solo orienta: el lugar se verifica de nuevo al guardar."""

    place_id: str
    name: str
    address: str
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
