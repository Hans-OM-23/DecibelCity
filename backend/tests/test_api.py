"""Integración HTTP, validación de entradas y persistencia en PostgreSQL temporal."""

from conftest import db_connection

import pytest
from fastapi.testclient import TestClient

from config import Settings
from main import create_app


def test_catalog_and_health(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    response = client.get("/api/hotspots")
    assert response.status_code == 200
    hotspots = response.json()
    assert [hotspot["decibels"] for hotspot in hotspots] == [74, 78, 69]
    assert all(hotspot["illustrative"] is True for hotspot in hotspots)
    # Posiciones aproximadas de OpenStreetMap dentro del área de Huancayo.
    assert all(-12.16 <= hotspot["latitude"] <= -11.95 for hotspot in hotspots)
    assert all(-75.30 <= hotspot["longitude"] <= -75.12 for hotspot in hotspots)
    assert all("google_query" not in hotspot for hotspot in hotspots)
    sources = client.get("/api/noise-sources").json()
    assert {source["value"] for source in sources} == {
        "traffic", "commerce", "construction", "entertainment", "other"
    }


def test_report_is_persisted_after_app_restart(settings: Settings, report_payload: dict, osm_place: dict, place_verifier) -> None:
    with TestClient(create_app(settings, place_verifier)) as client:
        response = client.post("/api/reports", json=report_payload)
        assert response.status_code == 201
        receipt = response.json()
        assert receipt["status"] == "received"
        assert receipt["created_at"].endswith("Z")
        assert receipt["location"] == osm_place["display_name"]
        assert receipt["latitude"] == float(osm_place["lat"])
        assert receipt["longitude"] == float(osm_place["lon"])
        assert receipt["place_id"] == "W813046785"
    with TestClient(create_app(settings, place_verifier)) as client:
        assert client.get("/api/health").status_code == 200
        # There is intentionally no public endpoint listing private report text.
        assert client.get("/api/reports").status_code == 405
        assert client.get(f"/api/reports/{receipt['id']}").status_code == 404
    with db_connection(settings) as connection:
        stored = connection.execute(
            """SELECT r.ubicacion_heredada, f.codigo, r.descripcion, r.nivel_estimado_db,
                      e.codigo_estado, r.id_lugar, r.proveedor_lugar, r.es_historico
               FROM reportes_ciudadanos r JOIN tipos_fuente_ruido f USING (id_tipo_fuente)
               JOIN estados_reporte e USING (id_estado) WHERE r.id_reporte = %s""",
            (receipt["id"],),
        ).fetchone()
    assert stored == (
        None, report_payload["source"],
        report_payload["description"], 74.0, "received", "W813046785", "osm", 0,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"location": "abcd"},
        {"location": " " * 10},
        {"location": "x" * 1001},
        {"description": "short"},
        {"description": " " * 10},
        {"description": "x" * 501},
        {"source": "unknown"},
        {"decibels": 29.9},
        {"decibels": 120.1},
        {"decibels": True},
        {"decibels": "74"},
        {"email": "person@example.test"},
        {"place_id": ""},
        {"place_id": "W"},
        {"place_id": "W" + "1" * 20},
        {"place_id": "X123"},
        {"place_id": "w123"},
        {"place_id": "ChIJ_TestHuancayo123"},
        {"place_id": "../lookup?osm_ids=W1"},
        {"place_id": "https://example.test"},
        {"place_id": 813046785},
        {"google_place_id": "ChIJ_TestHuancayo123"},
        {"latitude": -12.068, "longitude": -75.209},
    ],
)
def test_invalid_report_is_not_saved(
    client: TestClient, settings: Settings, report_payload: dict, changes: dict
) -> None:
    response = client.post("/api/reports", json=report_payload | changes)
    assert response.status_code == 422
    with db_connection(settings) as connection:
        assert connection.execute("SELECT COUNT(*) FROM reportes_ciudadanos").fetchone()[0] == 0


@pytest.mark.parametrize("decibels", [None, 30, 120, 68.5])
def test_optional_noise_level_and_boundaries(client: TestClient, report_payload: dict, decibels) -> None:
    report_payload["decibels"] = decibels
    response = client.post("/api/reports", json=report_payload)
    assert response.status_code == 201
    assert response.json()["decibels"] == decibels


def test_omitted_decibels_and_whitespace(client: TestClient, report_payload: dict, osm_place: dict) -> None:
    del report_payload["decibels"]
    report_payload["location"] = "  Calle Real  "
    response = client.post("/api/reports", json=report_payload)
    assert response.status_code == 201
    assert response.json()["decibels"] is None
    assert response.json()["location"] == osm_place["display_name"]


def test_seed_is_idempotent(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        initial = client.get("/api/hotspots").json()
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/hotspots").json() == initial
    with db_connection(settings) as connection:
        assert connection.execute("SELECT COUNT(*) FROM puntos_criticos").fetchone()[0] == 3


def test_cors_allows_configured_frontend_only(client: TestClient) -> None:
    headers = {
        "Origin": "http://localhost:4200",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }
    response = client.options("/api/reports", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:4200"
    response = client.options("/api/reports", headers=headers | {"Origin": "https://unknown.test"})
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
