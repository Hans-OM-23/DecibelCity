"""Sesiones opacas y autorización de reportes sobre PostgreSQL exclusivo de pruebas."""

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.cookies import SimpleCookie

import pytest
from fastapi.testclient import TestClient

from conftest import authenticate_client, db_connection, make_registration_payload
from main import create_app
from services.session_service import SESSION_COOKIE, SESSION_TTL_SECONDS, SessionStore


def set_session_cookie(client, token):
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, token, path="/api")


def credentials(profile):
    return {"nombre_usuario": profile["nombre_usuario"], "contrasena": "Contraseña de prueba 2026!"}


def forbid_verification(client, monkeypatch):
    def unexpected(place_id):
        pytest.fail("Una solicitud no autorizada no debe consultar Nominatim.")

    monkeypatch.setattr(client.app.state.noise_service.place_verifier, "verify", unexpected)


def assert_no_reports(settings):
    with db_connection(settings) as connection:
        assert connection.execute("SELECT COUNT(*) FROM reportes_ciudadanos").fetchone() == (0,)


def test_report_without_session_is_rejected_before_verification(
    client, settings, report_payload, monkeypatch,
):
    calls = []
    verifier = client.app.state.noise_service.place_verifier
    original = verifier.verify

    def verify(place_id):
        calls.append(place_id)
        return original(place_id)

    monkeypatch.setattr(verifier, "verify", verify)
    response = client.post("/api/reports", json=report_payload)
    assert response.status_code == 401
    assert calls == []
    assert_no_reports(settings)


@pytest.mark.parametrize("token", ["", "inventada", "a" * 43, "x" * 1_000])
def test_invalid_cookie_or_forged_profile_does_not_authorize_reports(
    client, settings, report_payload, monkeypatch, token,
):
    forbid_verification(client, monkeypatch)
    set_session_cookie(client, token)
    # Un perfil local o una identidad declarada por el cliente no acreditan una sesión.
    headers = {"X-User-Id": "1", "Authorization": "Bearer perfil-falsificado"}
    response = client.post("/api/reports", json=report_payload, headers=headers)
    assert response.status_code == 401
    assert isinstance(response.json()["detail"], str)
    session = client.get("/api/auth/session")
    assert session.status_code == 401
    assert session.headers["cache-control"] == "no-store"
    assert_no_reports(settings)


def test_store_keeps_only_digests_and_expires_at_eight_hours():
    now = [100.0]
    store = SessionStore(clock=lambda: now[0])
    token = store.create(17)
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    assert len(token) == 43
    assert store._sessions == {digest: (17, now[0] + SESSION_TTL_SECONDS)}
    assert token not in repr(store._sessions)
    now[0] += SESSION_TTL_SECONDS - 1
    assert store.resolve(token) == 17
    now[0] += 1
    assert store.resolve(token) is None
    assert store._sessions == {}


def test_store_rotates_revokes_and_evicts_oldest_without_refreshing_it():
    store = SessionStore(max_sessions=2)
    oldest = store.create(1)
    second = store.create(2)
    assert store.resolve(oldest) == 1
    newest = store.create(3)
    assert store.resolve(oldest) is None
    assert store.resolve(second) == 2
    rotated = store.create(4, previous_token=newest)
    assert store.resolve(newest) is None
    assert store.resolve(second) == 2
    assert store.resolve(rotated) == 4
    store.revoke(rotated)
    store.revoke(rotated)
    assert store.resolve(rotated) is None


def test_store_cleans_expired_entries_before_enforcing_memory_limit():
    now = [0.0]
    store = SessionStore(clock=lambda: now[0], ttl_seconds=10, max_sessions=2)
    expired = store.create(1)
    now[0] = 5
    live = store.create(2)
    now[0] = 10
    newest = store.create(3)
    assert store.resolve(expired) is None
    assert store.resolve(live) == 2
    assert store.resolve(newest) == 3
    assert len(store._sessions) == 2


def test_store_creation_resolution_and_revocation_are_safe_between_threads():
    store = SessionStore()
    with ThreadPoolExecutor(max_workers=8) as workers:
        tokens = list(workers.map(store.create, range(200)))
        assert len(set(tokens)) == 200
        assert list(workers.map(store.resolve, tokens)) == list(range(200))
        list(workers.map(store.revoke, tokens))
    assert store._sessions == {}
    limited = SessionStore(max_sessions=10)
    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(limited.create, range(100)))
    assert len(limited._sessions) == 10


@pytest.mark.parametrize("scheme, secure", [("http", False), ("https", True)])
def test_login_sets_cookie_attributes_without_exposing_tokens_or_hashes(
    settings, place_verifier, scheme, secure, caplog,
):
    caplog.set_level(logging.DEBUG)
    with TestClient(create_app(settings, place_verifier), base_url=f"{scheme}://testserver") as client:
        profile = authenticate_client(client)
        response = client.post("/api/auth/login", json=credentials(profile))
        assert response.status_code == 200
        cookie = SimpleCookie(response.headers["set-cookie"])[SESSION_COOKIE]
        assert bool(cookie["secure"]) is secure
        assert cookie["httponly"]
        assert cookie["samesite"].lower() == "lax"
        assert cookie["path"] == "/api"
        assert cookie["max-age"] == str(SESSION_TTL_SECONDS)
        assert not cookie["domain"]
        assert response.json() == profile
        assert cookie.value not in response.text and cookie.value not in caplog.text
        assert "password_hash" not in response.text and "$argon2" not in response.text
        assert response.headers["cache-control"] == "no-store"
        session = client.get("/api/auth/session")
        assert session.status_code == 200
        assert session.json() == profile
        assert session.headers["cache-control"] == "no-store"


def test_login_rotates_cookie_and_rejects_replay_of_previous_session(client):
    profile = authenticate_client(client)
    old_token = client.cookies.get(SESSION_COOKIE)
    response = client.post("/api/auth/login", json=credentials(profile))
    assert response.status_code == 200
    new_token = client.cookies.get(SESSION_COOKIE)
    assert new_token != old_token
    assert client.app.state.session_store.resolve(new_token) == profile["id_usuario"]
    set_session_cookie(client, old_token)
    assert client.get("/api/auth/session").status_code == 401


def test_registration_rotates_existing_cookie_and_changes_current_account(client):
    first = authenticate_client(client)
    old_token = client.cookies.get(SESSION_COOKIE)
    second = authenticate_client(client)
    assert second["id_usuario"] != first["id_usuario"]
    assert client.get("/api/auth/session").json() == second
    assert client.app.state.session_store.resolve(old_token) is None


def test_failed_login_does_not_issue_or_rotate_cookie(client):
    profile = authenticate_client(client)
    token = client.cookies.get(SESSION_COOKIE)
    response = client.post("/api/auth/login", json=credentials(profile) | {"contrasena": "incorrecta"})
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert client.cookies.get(SESSION_COOKIE) == token
    assert client.get("/api/auth/session").json() == profile


def test_session_reads_current_profile_from_database(authenticated_client, settings):
    with db_connection(settings) as connection:
        connection.execute("UPDATE usuarios SET nombres='Nombre actualizado'")
    assert authenticated_client.get("/api/auth/session").json()["nombres"] == "Nombre actualizado"


def test_expiry_between_session_check_and_report_prevents_external_calls_and_insertion(
    client, settings, report_payload, monkeypatch,
):
    now = [0.0]
    client.app.state.session_store = SessionStore(clock=lambda: now[0])
    authenticate_client(client)
    assert client.get("/api/auth/session").status_code == 200
    now[0] = SESSION_TTL_SECONDS
    forbid_verification(client, monkeypatch)
    assert client.post("/api/reports", json=report_payload).status_code == 401
    assert client.get("/api/auth/session").status_code == 401
    assert_no_reports(settings)


@pytest.mark.parametrize("table", ["usuarios", "roles_usuario"])
def test_disabled_user_or_role_cannot_use_existing_session_or_login(
    client, settings, report_payload, monkeypatch, table,
):
    profile = authenticate_client(client)
    with db_connection(settings) as connection:
        connection.execute(f"UPDATE {table} SET activo=FALSE")
    forbid_verification(client, monkeypatch)
    for response in (client.get("/api/auth/session"), client.post("/api/reports", json=report_payload)):
        assert response.status_code == 403
        assert isinstance(response.json()["detail"], str)
    assert client.post("/api/auth/login", json=credentials(profile)).status_code == 403
    assert client.post("/api/auth/login", json=credentials(profile) | {"contrasena": "incorrecta"}).status_code == 401
    assert_no_reports(settings)


def test_deleted_user_cannot_use_existing_session(authenticated_client, settings, report_payload, monkeypatch):
    with db_connection(settings) as connection:
        connection.execute("DELETE FROM usuarios")
    forbid_verification(authenticated_client, monkeypatch)
    assert authenticated_client.get("/api/auth/session").status_code == 401
    assert authenticated_client.post("/api/reports", json=report_payload).status_code == 401
    assert_no_reports(settings)


def test_logout_revokes_cookie_and_prevents_replay(authenticated_client, settings, report_payload, monkeypatch):
    token = authenticated_client.cookies.get(SESSION_COOKIE)
    response = authenticated_client.post("/api/auth/logout")
    assert response.status_code == 204 and response.content == b""
    assert SESSION_COOKIE not in authenticated_client.cookies
    cookie = SimpleCookie(response.headers["set-cookie"])[SESSION_COOKIE]
    assert cookie["max-age"] == "0" and cookie["path"] == "/api" and cookie["httponly"]
    assert response.headers["cache-control"] == "no-store"
    set_session_cookie(authenticated_client, token)
    forbid_verification(authenticated_client, monkeypatch)
    assert authenticated_client.get("/api/auth/session").status_code == 401
    assert authenticated_client.post("/api/reports", json=report_payload).status_code == 401
    assert_no_reports(settings)


def test_logout_without_session_is_idempotent(client):
    assert client.post("/api/auth/logout").status_code == 204
    set_session_cookie(client, "inventada")
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/session").status_code == 401


@pytest.mark.parametrize("origin", ["https://unknown.test", "null", "http://localhost:4200.evil.test"])
@pytest.mark.parametrize("path", ["/api/auth/register", "/api/auth/login", "/api/auth/logout", "/api/reports"])
def test_foreign_origin_is_denied_before_side_effects(
    client, settings, report_payload, monkeypatch, origin, path,
):
    profile = authenticate_client(client)
    registration = make_registration_payload(client)
    payload = registration if path.endswith("register") else credentials(profile)
    if path == "/api/reports":
        payload = report_payload
    forbid_verification(client, monkeypatch)
    response = client.post(path, json=payload, headers={"Origin": origin})
    assert response.status_code == 403
    assert response.json() == {"detail": "El origen de la solicitud no está permitido."}
    assert client.get("/api/auth/session").json() == profile  # Logout ajeno no revoca la cookie.
    with db_connection(settings) as connection:
        assert connection.execute("SELECT COUNT(*) FROM usuarios").fetchone() == (1,)
    assert_no_reports(settings)


@pytest.mark.parametrize("origin", [None, "http://testserver", "http://localhost:4200"])
def test_same_origin_configured_frontend_and_api_clients_can_authenticate_and_report(client, report_payload, origin):
    headers = {"Origin": origin} if origin is not None else {}
    payload = make_registration_payload(client)
    response = client.post("/api/auth/register", json=payload, headers=headers)
    assert response.status_code == 201
    profile = response.json()
    assert client.get("/api/auth/session").json() == profile
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/auth/session").status_code == 401
    assert client.post("/api/auth/login", json=credentials(profile), headers=headers).status_code == 200
    assert client.post("/api/reports", json=report_payload, headers=headers).status_code == 201


def test_session_is_local_to_app_and_restart_invalidates_it(settings, place_verifier):
    app = create_app(settings, place_verifier)
    with TestClient(app) as first:
        authenticate_client(first)
        token = first.cookies.get(SESSION_COOKIE)
        with TestClient(create_app(settings, place_verifier)) as second:
            set_session_cookie(second, token)
            assert second.get("/api/auth/session").status_code == 401
        assert first.get("/api/auth/session").status_code == 200
    with TestClient(app) as restarted:
        set_session_cookie(restarted, token)
        assert restarted.get("/api/auth/session").status_code == 401


def test_credentialed_cors_requires_explicit_origins(settings):
    with pytest.raises(ValueError, match="orígenes CORS explícitos"):
        create_app(replace(settings, cors_origins=("*",)))
