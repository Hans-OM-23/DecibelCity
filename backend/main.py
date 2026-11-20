"""Aplicación FastAPI. Ejecutar con: uv run uvicorn main:app --reload."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from apis.auth import router as auth_router
from apis.auth import validation_error_without_input
from apis.noise import router
from config import Settings
from repositories.noise_repository import NoiseRepository
from repositories.user_repository import UserRepository
from schemas.maps import MapBounds, MapCenter, PublicMapsConfig
from services.auth_service import AuthService
from services.noise_service import NoiseService
from services.osm_places import NominatimVerifier, PhotonSearcher, PlaceSearcher, PlaceVerifier
from services.session_service import SessionStore


def create_app(
    settings: Settings | None = None,
    place_verifier: PlaceVerifier | None = None,
    place_searcher: PlaceSearcher | None = None,
    password_hasher: PasswordHasher | None = None,
) -> FastAPI:
    configuration = settings or Settings.from_env()
    if "*" in configuration.cors_origins:
        raise ValueError("Configura orígenes CORS explícitos para las sesiones con cookies.")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        with httpx.Client() as http_client:
            verifier = place_verifier or NominatimVerifier(
                configuration.nominatim_url, configuration.geocoder_user_agent, http_client)
            searcher = place_searcher or PhotonSearcher(
                configuration.photon_url, configuration.geocoder_user_agent, http_client)
            repository = NoiseRepository(configuration.database_url, configuration.database_schema)
            service = NoiseService(repository, verifier, searcher)
            # Crea o actualiza el esquema (incluida la tabla usuarios) y el rol base.
            service.initialize()
            application.state.noise_service = service
            application.state.auth_service = AuthService(
                UserRepository(configuration.database_url, configuration.database_schema), password_hasher)
            application.state.session_store = SessionStore()
            try:
                yield
            finally:
                # No conservar sesiones al terminar el ciclo de vida de la aplicación.
                application.state.session_store = SessionStore()

    application = FastAPI(
        title="DecibelCity API",
        description="Cartografía sonora ilustrativa y recepción de reportes ciudadanos de Huancayo.",
        version="0.2.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(configuration.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    application.include_router(router)
    application.include_router(auth_router)
    application.add_exception_handler(RequestValidationError, validation_error_without_input)
    application.state.cors_origins = configuration.cors_origins

    @application.middleware("http")
    async def prevent_profile_caching(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/auth/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    application.state.maps_config = PublicMapsConfig(
        tile_url=configuration.map_tile_url,
        attribution=configuration.map_attribution,
        center=MapCenter(),
        bounds=MapBounds(),
    )
    return application


app = create_app()
