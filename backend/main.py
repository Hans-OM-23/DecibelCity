"""Aplicación FastAPI. Ejecutar con: uv run uvicorn main:app --reload."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apis.noise import router
from config import Settings
from repositories.noise_repository import NoiseRepository
from schemas.maps import MapBounds, MapCenter, PublicMapsConfig
from services.noise_service import NoiseService
from services.osm_places import NominatimVerifier, PhotonSearcher, PlaceSearcher, PlaceVerifier


def create_app(
    settings: Settings | None = None,
    place_verifier: PlaceVerifier | None = None,
    place_searcher: PlaceSearcher | None = None,
) -> FastAPI:
    configuration = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        with httpx.Client() as http_client:
            verifier = place_verifier or NominatimVerifier(
                configuration.nominatim_url, configuration.geocoder_user_agent, http_client)
            searcher = place_searcher or PhotonSearcher(
                configuration.photon_url, configuration.geocoder_user_agent, http_client)
            repository = NoiseRepository(configuration.database_url, configuration.database_schema)
            service = NoiseService(repository, verifier, searcher)
            service.initialize()
            application.state.noise_service = service
            yield

    application = FastAPI(
        title="DecibelCity API",
        description="Cartografía sonora ilustrativa y recepción de reportes ciudadanos de Huancayo.",
        version="0.2.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(configuration.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    application.include_router(router)
    application.state.maps_config = PublicMapsConfig(
        tile_url=configuration.map_tile_url,
        attribution=configuration.map_attribution,
        center=MapCenter(),
        bounds=MapBounds(),
    )
    return application


app = create_app()
