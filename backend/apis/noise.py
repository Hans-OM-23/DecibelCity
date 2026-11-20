"""Puntos de entrada públicos de DecibelCity."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from apis.auth import require_active_user, validate_origin
from schemas.maps import PlaceSuggestion, PublicMapsConfig
from schemas.users import AuthErrorResponse
from schemas.noise import HealthResponse, Hotspot, NoiseReport, NoiseReportCreate, NoiseSourceOption
from services.noise_service import NoiseService
from services.osm_places import PlaceVerificationError

router = APIRouter(prefix="/api", tags=["DecibelCity"])


def get_noise_service(request: Request) -> NoiseService:
    return request.app.state.noise_service


NoiseServiceDependency = Annotated[NoiseService, Depends(get_noise_service)]


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@router.get("/hotspots", response_model=list[Hotspot])
def list_hotspots(service: NoiseServiceDependency) -> list[Hotspot]:
    return service.list_hotspots()


@router.get("/maps/config", response_model=PublicMapsConfig)
def maps_config(request: Request) -> PublicMapsConfig:
    return request.app.state.maps_config


@router.get("/places/search", response_model=list[PlaceSuggestion])
def search_places(
    service: NoiseServiceDependency,
    q: Annotated[str, Query(min_length=3, max_length=120)],
) -> list[PlaceSuggestion]:
    # Sugerencias de Photon (OpenStreetMap) limitadas a Huancayo. No se guardan.
    query = " ".join(q.split())
    if len(query) < 3:
        raise HTTPException(status_code=422, detail="Escribe al menos 3 caracteres.")
    try:
        return service.search_places(query)
    except PlaceVerificationError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from None


@router.get("/noise-sources", response_model=list[NoiseSourceOption])
def list_noise_sources(service: NoiseServiceDependency) -> tuple[NoiseSourceOption, ...]:
    return service.list_sources()


@router.post("/reports", response_model=NoiseReport, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(validate_origin), Depends(require_active_user)],
             responses={401: {"model": AuthErrorResponse}, 403: {"model": AuthErrorResponse}})
def create_report(payload: NoiseReportCreate, service: NoiseServiceDependency) -> NoiseReport:
    try:
        return service.create_report(payload)
    except PlaceVerificationError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from None
