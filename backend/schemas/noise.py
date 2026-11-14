"""Modelos del dominio acústico y validación de reportes."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


# Identificador OpenStreetMap: N (nodo), W (vía) o R (relación) y su número, p. ej. "W813046785".
OsmPlaceId = Annotated[str, StringConstraints(min_length=2, max_length=20, pattern=r"^[NWR][0-9]{1,19}$")]


class NoiseSource(StrEnum):
    TRAFFIC = "traffic"
    COMMERCE = "commerce"
    CONSTRUCTION = "construction"
    ENTERTAINMENT = "entertainment"
    OTHER = "other"


class NoiseSourceOption(BaseModel):
    value: NoiseSource
    label: str


class Hotspot(BaseModel):
    id: str
    name: str
    area: str
    district: str
    sector: str
    decibels: float
    source: NoiseSource
    source_label: str
    description: str
    time_label: str
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    reference_limit: float
    illustrative: bool = True
    # Posición aproximada tomada de OpenStreetMap; el nivel de ruido sigue siendo ilustrativo.
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class NoiseReportCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    location: str = Field(min_length=5, max_length=1000)
    place_id: OsmPlaceId
    source: NoiseSource
    description: str = Field(min_length=10, max_length=500)
    decibels: float | None = Field(default=None, ge=30, le=120, strict=True)


class NoiseReport(NoiseReportCreate):
    # La dirección verificada puede ser más extensa que el texto del formulario.
    location: str = Field(min_length=1, max_length=1000)
    id: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    created_at: datetime
    status: Literal["received"] = "received"


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "DecibelCity API"
