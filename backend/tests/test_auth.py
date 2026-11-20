"""Catálogo territorial, registro e inicio de sesión con Argon2id sobre PostgreSQL temporal."""

import logging
from collections import Counter
from datetime import datetime

import pytest
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi.testclient import TestClient

from conftest import db_connection
from main import create_app
from repositories.catalogs import JUNIN_DISTRICTS, JUNIN_PROVINCES

PASSWORD = "Ruido seguro 2026!"
PROFILE_KEYS = {
    "id_usuario", "nombre_usuario", "nombres", "apellidos", "telefono", "direccion", "id_distrito",
    "nombre_distrito", "id_provincia", "nombre_provincia", "id_departamento", "nombre_departamento",
    "rol", "fecha_registro",
}


def district_id(settings, ubigeo: str = "120101") -> int:
    with db_connection(settings) as connection:
        return connection.execute("SELECT id_distrito FROM distritos WHERE ubigeo=%s", (ubigeo,)).fetchone()[0]


@pytest.fixture
def registration(client, settings) -> dict:
    return {
        "nombres": "  Ana María ", "apellidos": "Quispe Rojas", "telefono": "+51 964 123 456",
        "direccion": "Jr. Ica 123, Huancayo", "id_distrito": district_id(settings),
        "id_departamento": 1, "id_provincia": 1,
        "nombre_usuario": "ana.quispe", "contrasena": PASSWORD,
    }


def stored_hash(settings, username: str = "ana.quispe") -> str:
    with db_connection(settings) as connection:
        return connection.execute("SELECT password_hash FROM usuarios WHERE nombre_usuario=%s", (username,)).fetchone()[0]


def login(client, username="ana.quispe", password=PASSWORD):
    return client.post("/api/auth/login", json={"nombre_usuario": username, "contrasena": password})


def test_territories_list_all_of_junin(client):
    response = client.get("/api/territories")
    assert response.status_code == 200
    catalog = response.json()
    assert catalog["departamentos"] == [{"id_departamento": 1, "ubigeo": "12", "nombre_departamento": "Junín"}]
    assert sorted((p["ubigeo"], p["nombre_provincia"]) for p in catalog["provincias"]) == list(JUNIN_PROVINCES)
    assert all(p["id_departamento"] == 1 for p in catalog["provincias"])
    assert sorted((d["ubigeo"], d["nombre_distrito"]) for d in catalog["distritos"]) == sorted(JUNIN_DISTRICTS)
    assert len(catalog["provincias"]) == 9 and len(catalog["distritos"]) == 124
    assert set(catalog["distritos"][0]) == {"id_distrito", "id_provincia", "ubigeo", "nombre_distrito"}
    # Cada distrito apunta a la provincia de su UBIGEO.
    province_ubigeo = {p["id_provincia"]: p["ubigeo"] for p in catalog["provincias"]}
    assert all(province_ubigeo[d["id_provincia"]] == d["ubigeo"][:4] for d in catalog["distritos"])
    per_province = Counter(d["ubigeo"][:4] for d in catalog["distritos"])
    assert per_province == {"1201": 28, "1202": 15, "1203": 6, "1204": 34, "1205": 4,
                            "1206": 9, "1207": 9, "1208": 10, "1209": 9}
    # Lib1887, pp. 74–77, cotejado con el cuadro 1.5 de Lib0968, pp. 56–58:
    # tildes y Carhuacayán completo proceden del segundo, no de las constantes del seed.
    names = {d["ubigeo"]: d["nombre_distrito"] for d in catalog["distritos"]}
    expected_spellings = {
        "120119": "Huancán", "120126": "Pucará", "120408": "Huamalí", "120424": "Pancán",
        "120427": "Ricrán", "120429": "San Pedro de Chunán", "120432": "Tunán Marca",
        "120807": "Santa Bárbara de Carhuacayán", "120906": "San Juan de Iscos",
    }
    assert {code: names[code] for code in expected_spellings} == expected_spellings


def test_territories_list_only_fully_active_hierarchy(client, settings):
    with db_connection(settings) as connection:
        connection.execute("UPDATE distritos SET activo=FALSE WHERE ubigeo='120107'")
    distritos = client.get("/api/territories").json()["distritos"]
    assert len(distritos) == 123 and "120107" not in {d["ubigeo"] for d in distritos}
    with db_connection(settings) as connection:
        connection.execute("UPDATE provincias SET activo=FALSE WHERE ubigeo='1201'")
    catalog = client.get("/api/territories").json()
    assert "1201" not in {p["ubigeo"] for p in catalog["provincias"]} and len(catalog["provincias"]) == 8
    assert len(catalog["distritos"]) == 124 - 28
    assert not any(d["ubigeo"].startswith("1201") for d in catalog["distritos"])
    with db_connection(settings) as connection:
        connection.execute("UPDATE provincias SET activo=FALSE")
    assert client.get("/api/territories").json() == {
        "departamentos": [{"id_departamento": 1, "ubigeo": "12", "nombre_departamento": "Junín"}],
        "provincias": [], "distritos": []}
    with db_connection(settings) as connection:
        # Con descendientes activos, el departamento inactivo basta para ocultar toda la jerarquía.
        connection.execute("UPDATE provincias SET activo=TRUE")
        connection.execute("UPDATE departamentos SET activo=FALSE")
    assert client.get("/api/territories").json() == {"departamentos": [], "provincias": [], "distritos": []}


def territorial_selection(client, ubigeo: str) -> dict:
    """IDs de los tres selectores para un distrito, tomados de GET /api/territories como haría el formulario."""
    response = client.get("/api/territories")
    assert response.status_code == 200
    catalog = response.json()
    district = next(d for d in catalog["distritos"] if d["ubigeo"] == ubigeo)
    province = next(p for p in catalog["provincias"] if p["id_provincia"] == district["id_provincia"])
    return {"id_departamento": province["id_departamento"], "id_provincia": province["id_provincia"],
            "id_distrito": district["id_distrito"]}


@pytest.mark.parametrize("ubigeo,distrito,provincia", [
    ("120114", "El Tambo", "Huancayo"), ("120215", "Santa Rosa de Ocopa", "Concepción"),
    ("120303", "Pichanaqui", "Chanchamayo"), ("120432", "Tunán Marca", "Jauja"),
    ("120502", "Carhuamayo", "Junín"), ("120609", "Vizcatán del Ene", "Satipo"),
    ("120708", "San Pedro de Cajas", "Tarma"), ("120801", "La Oroya", "Yauli"),
    ("120906", "San Juan de Iscos", "Chupaca"),
])
def test_register_with_districts_of_every_junin_province(client, settings, registration, ubigeo, distrito, provincia):
    selection = territorial_selection(client, ubigeo)
    response = client.post("/api/auth/register", json=registration | selection)
    assert response.status_code == 201
    profile = response.json()
    assert (profile["id_distrito"], profile["nombre_distrito"], profile["id_provincia"], profile["nombre_provincia"],
            profile["nombre_departamento"]) == (selection["id_distrito"], distrito, selection["id_provincia"], provincia, "Junín")
    with db_connection(settings) as connection:
        assert connection.execute("""SELECT u.id_distrito,d.ubigeo,p.ubigeo,a.ubigeo FROM usuarios u
            JOIN distritos d USING(id_distrito) JOIN provincias p USING(id_provincia)
            JOIN departamentos a USING(id_departamento) WHERE u.id_usuario=%s""",
            (profile["id_usuario"],)).fetchone() == (selection["id_distrito"], ubigeo, ubigeo[:4], "12")
    assert login(client).json() == profile


@pytest.mark.parametrize("district_code,other_province_district", [
    ("120701", "120101"), ("120906", "120609"), ("120303", "120801"),
])
def test_register_rejects_district_from_another_junin_province(
        client, settings, registration, district_code, other_province_district):
    selection = territorial_selection(client, district_code)
    other = territorial_selection(client, other_province_district)
    response = client.post("/api/auth/register", json=registration | selection | {"id_provincia": other["id_provincia"]})
    assert response.status_code == 422
    with db_connection(settings) as connection:
        assert connection.execute("SELECT COUNT(*) FROM usuarios").fetchone() == (0,)


@pytest.mark.parametrize("ubigeo", ["120609", "120801"])
def test_register_rejects_inactive_province_outside_huancayo(client, settings, registration, ubigeo):
    selection = territorial_selection(client, ubigeo)
    with db_connection(settings) as connection:
        connection.execute("UPDATE provincias SET activo=FALSE WHERE id_provincia=%s", (selection["id_provincia"],))
    catalog = client.get("/api/territories").json()
    assert selection["id_provincia"] not in {p["id_provincia"] for p in catalog["provincias"]}
    assert not any(d["ubigeo"].startswith(ubigeo[:4]) for d in catalog["distritos"])
    assert any(d["ubigeo"] == "120101" for d in catalog["distritos"])
    response = client.post("/api/auth/register", json=registration | selection)
    assert response.status_code == 422
    with db_connection(settings) as connection:
        assert connection.execute("SELECT COUNT(*) FROM usuarios").fetchone() == (0,)


def test_register_stores_argon2id_hash_and_returns_profile(client, settings, registration):
    response = client.post("/api/auth/register", json=registration)
    assert response.status_code == 201
    profile = response.json()
    assert set(profile) == PROFILE_KEYS
    assert profile | {"id_usuario": 0, "fecha_registro": ""} == {
        "id_usuario": 0, "nombre_usuario": "ana.quispe", "nombres": "Ana María", "apellidos": "Quispe Rojas",
        "telefono": "+51 964 123 456", "direccion": "Jr. Ica 123, Huancayo",
        "id_distrito": registration["id_distrito"], "nombre_distrito": "Huancayo",
        "id_provincia": 1, "nombre_provincia": "Huancayo", "id_departamento": 1, "nombre_departamento": "Junín",
        "rol": "ciudadano", "fecha_registro": "",
    }
    assert datetime.fromisoformat(profile["fecha_registro"]).utcoffset() is not None
    password_hash = stored_hash(settings)
    assert password_hash.startswith("$argon2id$")
    assert PASSWORD not in password_hash
    assert PasswordHasher().verify(password_hash, PASSWORD)
    with pytest.raises(VerifyMismatchError):
        PasswordHasher().verify(password_hash, PASSWORD + "x")
    # Mismo texto, sal distinta: el hash no es determinista.
    assert client.post("/api/auth/register", json=registration | {"nombre_usuario": "otra.cuenta"}).status_code == 201
    assert stored_hash(settings, "otra.cuenta") != password_hash


@pytest.mark.parametrize("username", ["ana.quispe", "Ana.Quispe"])
def test_register_rejects_duplicate_username(client, registration, username):
    assert client.post("/api/auth/register", json=registration).status_code == 201
    response = client.post("/api/auth/register", json=registration | {"nombre_usuario": username})
    assert response.status_code == 409
    assert response.json() == {"detail": "El nombre de usuario ya está registrado."}


@pytest.mark.parametrize("table", ["distritos", "provincias", "departamentos"])
def test_register_rejects_district_with_inactive_hierarchy(client, settings, registration, table):
    with db_connection(settings) as connection:
        connection.execute(f"UPDATE {table} SET activo=FALSE")
    response = client.post("/api/auth/register", json=registration)
    assert response.status_code == 422
    assert response.json() == {"detail": "El distrito seleccionado no existe o no está disponible para el registro."}
    with db_connection(settings) as connection:
        assert connection.execute("SELECT COUNT(*) FROM usuarios").fetchone() == (0,)


def test_register_rejects_unknown_district(client, registration):
    response = client.post("/api/auth/register", json=registration | {"id_distrito": 999})
    assert response.status_code == 422
    assert "distrito" in response.json()["detail"]


@pytest.mark.parametrize("field", ["id_departamento", "id_provincia"])
def test_register_rejects_mismatched_territorial_selection(client, settings, registration, field):
    # El otro ID existe y está activo: se rechaza por pertenencia, no por ausencia.
    with db_connection(settings) as connection:
        connection.execute("INSERT INTO departamentos(id_departamento,ubigeo,nombre_departamento) VALUES(999,'15','Lima')")
        connection.execute("INSERT INTO provincias(id_provincia,id_departamento,ubigeo,nombre_provincia) VALUES(999,999,'1501','Lima')")
    response = client.post("/api/auth/register", json=registration | {field: 999})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    with db_connection(settings) as connection:
        assert connection.execute("SELECT COUNT(*) FROM usuarios").fetchone() == (0,)


@pytest.mark.parametrize("field", ["id_departamento", "id_provincia"])
def test_register_requires_all_territorial_selectors(client, registration, field):
    payload = registration.copy()
    payload.pop(field)
    assert client.post("/api/auth/register", json=payload).status_code == 422


def test_auth_validation_does_not_echo_secrets_used_as_extra_keys(client, registration):
    response = client.post("/api/auth/register", json=registration | {PASSWORD: "extra"})
    assert response.status_code == 422
    assert PASSWORD not in response.text


def test_auth_openapi_documents_registration_and_error_contract(client):
    document = client.get('/openapi.json').json()
    schemas = document['components']['schemas']
    assert set(schemas['UserRegistration']['required']) == {
        'nombres', 'apellidos', 'telefono', 'direccion', 'id_departamento', 'id_provincia',
        'id_distrito', 'nombre_usuario', 'contrasena',
    }
    for field in ('id_departamento', 'id_provincia', 'id_distrito'):
        assert schemas['UserRegistration']['properties'][field]['type'] == 'integer'
    assert set(document['paths']['/api/auth/register']['post']['responses']) == {'201', '403', '409', '422', '503'}
    assert set(document['paths']['/api/auth/login']['post']['responses']) == {'200', '401', '403', '422'}
    assert {'401', '403'} <= document['paths']['/api/reports']['post']['responses'].keys()
    assert not {'password_hash', 'contrasena'} & schemas['UserProfile']['properties'].keys()


@pytest.mark.parametrize("field", ["id_departamento", "id_provincia"])
@pytest.mark.parametrize("value", ["1", True, 0])
def test_register_requires_strict_positive_territorial_ids(client, registration, field, value):
    assert client.post("/api/auth/register", json=registration | {field: value}).status_code == 422


def test_register_unavailable_without_active_default_role(client, settings, registration):
    with db_connection(settings) as connection:
        connection.execute("UPDATE roles_usuario SET activo=FALSE WHERE nombre_rol='ciudadano'")
    response = client.post("/api/auth/register", json=registration)
    assert response.status_code == 503
    assert response.json() == {"detail": "El registro no está disponible en este momento."}


@pytest.mark.parametrize("changes", [
    {"nombres": "A"}, {"apellidos": " " * 5}, {"telefono": "12345"}, {"telefono": "964-abc-123"},
    {"direccion": "Jr 1"}, {"direccion": "d" * 201}, {"nombre_usuario": "ab"}, {"nombre_usuario": "ana quispe"},
    {"id_distrito": "1"}, {"id_distrito": True}, {"id_distrito": 0},
    {"contrasena": "corta7!"}, {"contrasena": " " * 12}, {"contrasena": "x" * 129},
    {"rol": "administrador"}, {"password_hash": "$argon2id$inyectado"},
])
def test_register_validation_never_echoes_input(client, settings, registration, changes):
    response = client.post("/api/auth/register", json=registration | changes)
    assert response.status_code == 422
    assert PASSWORD not in response.text
    assert all("input" not in error for error in response.json()["detail"])
    for value in changes.values():
        if isinstance(value, str) and len(value.strip()) > 3:
            assert value not in response.text
    with db_connection(settings) as connection:
        assert connection.execute("SELECT COUNT(*) FROM usuarios").fetchone() == (0,)


def test_password_keeps_surrounding_spaces(client, registration):
    assert client.post("/api/auth/register", json=registration | {"contrasena": f"  {PASSWORD}  "}).status_code == 201
    assert login(client, password=PASSWORD).status_code == 401
    assert login(client, password=f"  {PASSWORD}  ").status_code == 200


def test_login_returns_same_profile_and_never_the_hash(client, registration):
    registered = client.post("/api/auth/register", json=registration).json()
    for username in ("ana.quispe", " ANA.QUISPE "):
        response = login(client, username)
        assert response.status_code == 200
        assert response.json() == registered
        assert "password_hash" not in response.text and "$argon2" not in response.text


@pytest.mark.parametrize("username,password", [
    ("ana.quispe", PASSWORD + "x"), ("ana.quispe", PASSWORD.lower()), ("no.existe", PASSWORD),
])
def test_login_rejects_bad_credentials_with_same_message(client, registration, username, password):
    client.post("/api/auth/register", json=registration)
    response = login(client, username, password)
    assert response.status_code == 401
    assert response.json() == {"detail": "Nombre de usuario o contraseña incorrectos."}


def test_login_rejects_inactive_account_only_after_valid_password(client, settings, registration):
    client.post("/api/auth/register", json=registration)
    with db_connection(settings) as connection:
        connection.execute("UPDATE usuarios SET activo=FALSE")
    response = login(client)
    assert response.status_code == 403
    assert response.json() == {"detail": "La cuenta está desactivada. Comunícate con el equipo de DecibelCity."}
    # Con una contraseña incorrecta no se revela que la cuenta existe y está desactivada.
    assert login(client, password="otra contraseña").status_code == 401


def test_login_with_corrupt_hash_fails_closed(client, settings, registration):
    client.post("/api/auth/register", json=registration)
    with db_connection(settings) as connection:
        connection.execute("UPDATE usuarios SET password_hash='no-es-un-hash'")
    assert login(client).status_code == 401


def test_login_rehashes_outdated_parameters(settings, place_verifier, registration):
    weak = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1)
    with TestClient(create_app(settings, place_verifier, password_hasher=weak)) as client:
        assert client.post("/api/auth/register", json=registration).status_code == 201
    old_hash = stored_hash(settings)
    assert PasswordHasher().check_needs_rehash(old_hash)
    with TestClient(create_app(settings, place_verifier)) as client:
        assert login(client).status_code == 200
    new_hash = stored_hash(settings)
    assert new_hash != old_hash and not PasswordHasher().check_needs_rehash(new_hash)
    assert PasswordHasher().verify(new_hash, PASSWORD)


def test_login_validation_and_logs_do_not_expose_password(client, registration, caplog):
    caplog.set_level(logging.DEBUG)
    client.post("/api/auth/register", json=registration)
    login(client)
    login(client, password="contraseña equivocada")
    response = client.post("/api/auth/login", json={"nombre_usuario": "ana.quispe", "contrasena": PASSWORD, "extra": 1})
    assert response.status_code == 422 and PASSWORD not in response.text
    assert PASSWORD not in caplog.text and "contraseña equivocada" not in caplog.text
    assert '$argon2' not in caplog.text
