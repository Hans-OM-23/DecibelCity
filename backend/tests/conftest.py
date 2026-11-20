"""PostgreSQL real con esquemas aislados; HTTP de Nominatim y Photon simulado."""

from pathlib import Path
from contextlib import contextmanager
from uuid import uuid4
import os

import httpx
import pytest
import psycopg
from psycopg import sql
from fastapi.testclient import TestClient

from config import Settings
from main import create_app
from services.osm_places import NominatimVerifier

NOMINATIM_URL = "https://nominatim.test"
PHOTON_URL = "https://photon.test"
USER_AGENT = "DecibelCity-tests/1.0"


@pytest.fixture(scope="session")
def database_url():
    value = os.getenv("DECIBELCITY_TEST_DATABASE_URL", "")
    if not value:
        env_file = Path(__file__).resolve().parents[1] / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("DECIBELCITY_TEST_DATABASE_URL="):
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not value:
        pytest.fail("Configura DECIBELCITY_TEST_DATABASE_URL con una base PostgreSQL exclusiva terminada en _test.")
    with psycopg.connect(value, connect_timeout=5) as connection:
        if not connection.execute("SELECT current_database()").fetchone()[0].endswith("_test"):
            pytest.fail("La base de pruebas debe terminar en _test; no se utilizará la base de la aplicación.")
    return value


@contextmanager
def db_connection(settings):
    with psycopg.connect(settings.database_url, connect_timeout=5) as connection:
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(settings.database_schema)))
        connection.execute("SET TIME ZONE 'UTC'")
        yield connection


@pytest.fixture
def settings(database_url) -> Settings:
    schema = "test_" + uuid4().hex
    with psycopg.connect(database_url) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    yield Settings(
        database_url=database_url,
        database_schema=schema,
        cors_origins=("http://localhost:4200",),
        nominatim_url=NOMINATIM_URL,
        photon_url=PHOTON_URL,
        geocoder_user_agent=USER_AGENT,
    )
    with psycopg.connect(database_url) as connection:
        # Nombre generado aquí, nunca tomado del entorno ni de entradas externas.
        connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def osm_place() -> dict:
    """Respuesta de /lookup de Nominatim (jsonv2) para una vía de Huancayo."""
    return {
        "osm_type": "way",
        "osm_id": 813046785,
        "lat": "-12.0664834",
        "lon": "-75.2062612",
        "category": "highway",
        "type": "tertiary",
        "place_rank": 26,
        "display_name": "Avenida Giraldez, San Carlos, Huancayo, Junín, 12001, Perú",
        "address": {"road": "Avenida Giraldez", "city": "Huancayo", "country": "Perú", "country_code": "pe"},
    }


def make_verifier(http_client: httpx.Client) -> NominatimVerifier:
    return NominatimVerifier(NOMINATIM_URL, USER_AGENT, http_client, min_interval=0)


@pytest.fixture
def place_verifier(osm_place: dict):
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.host == "nominatim.test"
        assert request.url.path == "/lookup"
        assert request.url.params["osm_ids"] == "W813046785"
        assert request.url.params["format"] == "jsonv2"
        assert request.headers["User-Agent"] == USER_AGENT
        return httpx.Response(200, json=[osm_place])

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        yield make_verifier(http_client)


@pytest.fixture
def client(settings: Settings, place_verifier):
    with TestClient(create_app(settings, place_verifier)) as test_client:
        yield test_client


def make_registration_payload(client: TestClient) -> dict:
    """Selecciona IDs reales del esquema temporal, sin asumir IDs del catálogo."""
    catalog = client.get("/api/territories").json()
    district = next(row for row in catalog["distritos"] if row["ubigeo"] == "120101")
    province = next(row for row in catalog["provincias"] if row["id_provincia"] == district["id_provincia"])
    return {
        "nombres": "Ana María", "apellidos": "Quispe Rojas", "telefono": "+51 964 123 456",
        "direccion": "Jr. Ica 123, Huancayo", "id_distrito": district["id_distrito"],
        "id_provincia": province["id_provincia"], "id_departamento": province["id_departamento"],
        "nombre_usuario": "reporte." + uuid4().hex,
        "contrasena": "Contraseña de prueba 2026!",
    }


def authenticate_client(client: TestClient) -> dict:
    """Registra una cuenta de prueba y deja su cookie real en este cliente HTTP."""
    response = client.post("/api/auth/register", json=make_registration_payload(client))
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def authenticated_client(client: TestClient):
    authenticate_client(client)
    return client


@pytest.fixture
def report_payload() -> dict:
    return {
        "location": "Calle Real y Av. Giráldez",
        "place_id": "W813046785",
        "source": "traffic",
        "description": "Bocinas constantes junto al paradero durante la tarde.",
        "decibels": 74,
    }
