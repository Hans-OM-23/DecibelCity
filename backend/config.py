"""Configuración de la API mediante variables de entorno."""

import os
from dataclasses import dataclass, field

DEFAULT_USER_AGENT = "DecibelCity/0.1 (prototipo educativo de reportes de ruido)"


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    cors_origins: tuple[str, ...]
    database_schema: str = "public"
    # Servicios abiertos de OpenStreetMap. Ninguno requiere clave.
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    photon_url: str = "https://photon.komoot.io"
    geocoder_user_agent: str = DEFAULT_USER_AGENT
    map_tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    map_attribution: str = '&copy; <a href="https://www.openstreetmap.org/copyright">colaboradores de OpenStreetMap</a>'

    @classmethod
    def from_env(cls) -> "Settings":
        origins = os.getenv(
            "DECIBELCITY_CORS_ORIGINS",
            "http://localhost:4200,http://127.0.0.1:4200",
        )
        defaults = cls(database_url="", cors_origins=())
        return cls(
            database_url=os.getenv("DECIBELCITY_DATABASE_URL", "postgresql://decibelcity@127.0.0.1:5432/decibelcity"),
            database_schema=os.getenv("DECIBELCITY_DATABASE_SCHEMA", "public"),
            cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
            nominatim_url=_url("DECIBELCITY_NOMINATIM_URL", defaults.nominatim_url),
            photon_url=_url("DECIBELCITY_PHOTON_URL", defaults.photon_url),
            geocoder_user_agent=os.getenv("DECIBELCITY_GEOCODER_USER_AGENT", "").strip() or defaults.geocoder_user_agent,
            map_tile_url=os.getenv("DECIBELCITY_MAP_TILE_URL", "").strip() or defaults.map_tile_url,
            map_attribution=os.getenv("DECIBELCITY_MAP_ATTRIBUTION", "").strip() or defaults.map_attribution,
        )


def _url(name: str, default: str) -> str:
    return (os.getenv(name, "").strip() or default).rstrip("/")
