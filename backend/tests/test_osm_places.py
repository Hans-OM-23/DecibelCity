"""Verificación con Nominatim, búsqueda con Photon y configuración pública sin claves."""

import httpx
import pytest
from collections.abc import Iterator
from contextlib import contextmanager
from fastapi.testclient import TestClient

from conftest import USER_AGENT, authenticate_client, db_connection, make_verifier
from config import Settings
from main import create_app
from services.osm_places import PhotonSearcher


def photon_feature(**changes) -> dict:
    properties = {
        "osm_type": "W", "osm_id": 813046785, "osm_key": "highway", "osm_value": "tertiary",
        "type": "street", "name": "Avenida Giraldez", "district": "San Carlos", "city": "Huancayo",
        "countrycode": "PE",
    }
    coordinates = changes.pop("coordinates", [-75.2062612, -12.0664834])
    properties.update(changes)
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": coordinates}, "properties": properties}


@contextmanager
def app_with(settings: Settings, verifier_response=None, photon_response=None) -> Iterator[TestClient]:
    with (
        httpx.Client(transport=httpx.MockTransport(verifier_response or (lambda r: httpx.Response(500)))) as verifier_client,
        httpx.Client(transport=httpx.MockTransport(photon_response or (lambda r: httpx.Response(500)))) as photon_client,
    ):
        searcher = PhotonSearcher("https://photon.test", USER_AGENT, photon_client)
        with TestClient(create_app(settings, make_verifier(verifier_client), searcher)) as client:
            authenticate_client(client)
            yield client


def report_count(settings: Settings) -> int:
    with db_connection(settings) as connection:
        return connection.execute("SELECT COUNT(*) FROM reportes_ciudadanos").fetchone()[0]


def test_public_config_has_no_keys(client: TestClient) -> None:
    response = client.get("/api/maps/config")
    assert response.status_code == 200
    config = response.json()
    assert set(config) == {"tile_url", "attribution", "center", "bounds"}
    assert config["tile_url"] == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    assert "OpenStreetMap" in config["attribution"]
    assert config["bounds"] == {"south": -12.16, "west": -75.30, "north": -11.95, "east": -75.12}
    assert "key" not in response.text.lower()


def test_missing_place_id_is_rejected(authenticated_client: TestClient, report_payload: dict) -> None:
    del report_payload["place_id"]
    assert authenticated_client.post("/api/reports", json=report_payload).status_code == 422


def test_forged_location_is_replaced_and_address_is_not_stored(
    authenticated_client: TestClient, settings: Settings, report_payload: dict, osm_place: dict
) -> None:
    report_payload["location"] = "Dirección inventada en otro país"
    response = authenticated_client.post("/api/reports", json=report_payload)
    assert response.status_code == 201
    receipt = response.json()
    assert receipt["location"] == osm_place["display_name"]
    with db_connection(settings) as connection:
        columns = {row[0] for row in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='reportes_ciudadanos'")}
        assert "latitude" not in columns and "longitude" not in columns
        assert "id_usuario" not in columns
        row = connection.execute("SELECT * FROM reportes_ciudadanos WHERE id_reporte = %s", (receipt["id"],)).fetchone()
        assert osm_place["display_name"] not in row
        assert report_payload["location"] not in row
        assert "W813046785" in row and "osm" in row


@pytest.mark.parametrize(
    "changes",
    [
        {"lat": "-12.046", "lon": "-77.043"},  # Lima
        {"lat": "-12.20"}, {"lat": "-11.90"}, {"lon": "-75.31"}, {"lon": "-75.11"},
        {"lat": None}, {"lat": "no es número"}, {"lat": "nan"}, {"lon": "inf"},
        {"address": {"country_code": "cl"}}, {"address": None},
        {"place_rank": 16, "category": "place", "type": "city"},
        {"place_rank": 20, "category": "place", "type": "suburb"},
        {"place_rank": 30, "category": "boundary", "type": "administrative"},
        {"place_rank": 26, "category": "place", "type": "neighbourhood"},
        {"place_rank": "30"}, {"place_rank": None},
        {"display_name": ""}, {"display_name": "x" * 1001},
    ],
)
def test_invalid_or_outside_place_is_not_saved(settings: Settings, report_payload: dict, osm_place: dict, changes: dict) -> None:
    with app_with(settings, lambda request: httpx.Response(200, json=[osm_place | changes])) as client:
        assert client.post("/api/reports", json=report_payload).status_code == 422
    assert report_count(settings) == 0


@pytest.mark.parametrize("changes", [
    {"category": "place", "type": "square", "place_rank": 25},  # Plaza de la Constitución
    {"category": "shop", "type": "yes", "place_rank": 30},
    {"category": "amenity", "type": "marketplace", "place_rank": 30},
    {"category": "building", "type": "yes", "place_rank": 30},
])
def test_specific_places_are_accepted(settings: Settings, report_payload: dict, osm_place: dict, changes: dict) -> None:
    with app_with(settings, lambda request: httpx.Response(200, json=[osm_place | changes])) as client:
        assert client.post("/api/reports", json=report_payload).status_code == 201


@pytest.mark.parametrize("body, expected", [
    ([], 422),  # No existe
    ([{"osm_type": "way", "osm_id": 1}], 502),  # Otro lugar
    ([{"osm_type": "node", "osm_id": 813046785}], 502),  # Otro tipo
    ([{}, {}], 502),
    ({"error": "x"}, 502),
    ("no es json", 502),
])
def test_unexpected_nominatim_responses(settings: Settings, report_payload: dict, body, expected: int) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body) if isinstance(body, str) else httpx.Response(200, json=body)

    with app_with(settings, respond) as client:
        assert client.post("/api/reports", json=report_payload).status_code == expected
    assert report_count(settings) == 0


@pytest.mark.parametrize("upstream_status, expected_status", [(400, 502), (404, 502), (403, 503), (429, 503), (500, 503), (302, 502)])
def test_nominatim_errors_are_safe(settings: Settings, report_payload: dict, upstream_status: int, expected_status: int) -> None:
    secret = "detalle-interno-del-proveedor"
    respond = lambda request: httpx.Response(upstream_status, json={"error": secret}, headers={"Location": "https://untrusted.test"})
    with app_with(settings, respond) as client:
        response = client.post("/api/reports", json=report_payload)
        assert response.status_code == expected_status
        assert secret not in response.text
    assert report_count(settings) == 0


@pytest.mark.parametrize("error_type, expected_status", [(httpx.ReadTimeout, 503), (httpx.ConnectError, 502)])
def test_nominatim_network_errors(settings: Settings, report_payload: dict, error_type, expected_status: int) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise error_type("fallo", request=request)

    with app_with(settings, fail) as client:
        assert client.post("/api/reports", json=report_payload).status_code == expected_status
    assert report_count(settings) == 0


def test_search_filters_to_specific_places_in_huancayo(settings: Settings) -> None:
    seen = {}

    def respond(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params, host=request.url.host, path=request.url.path, agent=request.headers["User-Agent"])
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [
            photon_feature(),
            photon_feature(),  # Duplicado
            photon_feature(osm_id=1, name="Mercado Mayorista de Carne", osm_key="shop", osm_value="butcher",
                           street="Avenida Ferrocarril", district=None, coordinates=[-75.2050162, -12.0730445]),
            photon_feature(osm_type="R", osm_id=2, osm_key="place", osm_value="city", name="Huancayo"),
            photon_feature(osm_type="R", osm_id=3, osm_key="boundary", osm_value="administrative", name="Chilca"),
            photon_feature(osm_id=4, countrycode="BO"),
            photon_feature(osm_id=5, coordinates=[-77.04, -12.04]),  # Lima
            photon_feature(osm_id=6, coordinates=["x", -12.06]),
            photon_feature(osm_type="X", osm_id=7),
            photon_feature(osm_id=8, name=None, street=None),
            "no es un objeto",
        ]})

    with app_with(settings, photon_response=respond) as client:
        response = client.get("/api/places/search", params={"q": "  giraldez   huancayo "})
    assert response.status_code == 200
    assert response.json() == [
        {"place_id": "W813046785", "name": "Avenida Giraldez", "address": "San Carlos, Huancayo", "lat": -12.0664834, "lng": -75.2062612},
        {"place_id": "W1", "name": "Mercado Mayorista de Carne", "address": "Avenida Ferrocarril, Huancayo", "lat": -12.0730445, "lng": -75.2050162},
    ]
    assert seen["host"] == "photon.test" and seen["path"] == "/api/"
    assert seen["q"] == "giraldez huancayo"
    assert seen["bbox"] == "-75.3,-12.16,-75.12,-11.95"
    assert seen["agent"] == USER_AGENT


@pytest.mark.parametrize("query", ["", "ab", "  a  ", "x" * 121])
def test_search_rejects_short_or_long_queries(client: TestClient, query: str) -> None:
    assert client.get("/api/places/search", params={"q": query}).status_code == 422


@pytest.mark.parametrize("upstream_status, expected_status", [(429, 503), (500, 503), (400, 502)])
def test_search_provider_errors_are_safe(settings: Settings, upstream_status: int, expected_status: int) -> None:
    with app_with(settings, photon_response=lambda request: httpx.Response(upstream_status, text="interno")) as client:
        response = client.get("/api/places/search", params={"q": "Giraldez"})
    assert response.status_code == expected_status
    assert "interno" not in response.text


def test_settings_read_openstreetmap_services_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("DECIBELCITY_NOMINATIM_URL", "http://127.0.0.1:8080/")
    monkeypatch.setenv("DECIBELCITY_PHOTON_URL", "http://127.0.0.1:2322")
    monkeypatch.setenv("DECIBELCITY_GEOCODER_USER_AGENT", "MiDespliegue/1.0")
    settings = Settings.from_env()
    assert settings.nominatim_url == "http://127.0.0.1:8080"
    assert settings.photon_url == "http://127.0.0.1:2322"
    assert settings.geocoder_user_agent == "MiDespliegue/1.0"
    monkeypatch.delenv("DECIBELCITY_NOMINATIM_URL")
    assert Settings.from_env().nominatim_url == "https://nominatim.openstreetmap.org"
