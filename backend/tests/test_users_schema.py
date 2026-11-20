"""Tabla usuarios (v5): migración desde v4, restricciones CHECK/UNIQUE y claves foráneas."""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4
import re

import psycopg
import pytest
from psycopg import sql

from conftest import db_connection
from repositories import migrations
from repositories.noise_repository import NoiseRepository
from services.noise_service import DEMO_HOTSPOTS, NOISE_SOURCES

V4_SCHEMA = Path(__file__).parent / "fixtures" / "schema_v4.sql"
USER_COLUMNS = "id_rol,id_distrito,nombres,apellidos,telefono,direccion,nombre_usuario,password_hash"
VALID_USER = {
    "nombres": "Ana", "apellidos": "Quispe", "telefono": "+51 (64) 123-456",
    "direccion": "Jr. Ica 123", "nombre_usuario": "ana.quispe", "password_hash": "$argon2id$v=19$prueba",
}


def initialize(settings):
    NoiseRepository(settings.database_url, settings.database_schema).initialize(DEMO_HOTSPOTS, NOISE_SOURCES)


def insert_user(connection, **changes):
    values = {**VALID_USER, **changes}
    connection.execute(f"""INSERT INTO usuarios({USER_COLUMNS}) VALUES (
        (SELECT id_rol FROM roles_usuario WHERE nombre_rol='ciudadano'),
        (SELECT id_distrito FROM distritos WHERE ubigeo='120101'),
        %(nombres)s,%(apellidos)s,%(telefono)s,%(direccion)s,%(nombre_usuario)s,%(password_hash)s)""", values)


def structure(connection) -> dict:
    """Columnas, restricciones e índices de usuarios, sin nombres de esquema."""
    columns = connection.execute("""SELECT column_name,data_type,is_nullable,column_default,is_identity
        FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='usuarios'
        ORDER BY ordinal_position""").fetchall()
    constraints = connection.execute("""SELECT conname,pg_get_constraintdef(c.oid) FROM pg_constraint c
        JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname=current_schema() AND t.relname='usuarios' ORDER BY conname""").fetchall()
    indexes = connection.execute("""SELECT indexname,replace(indexdef,current_schema()||'.','') FROM pg_indexes
        WHERE schemaname=current_schema() AND tablename='usuarios' ORDER BY indexname""").fetchall()
    return {"columns": columns, "constraints": constraints, "indexes": indexes}


@pytest.fixture
def v4_database(settings):
    """Base v4 real (esquema publicado antes de las cuentas) con un reporte OSM y un rol propio."""
    with db_connection(settings) as connection:
        connection.execute(V4_SCHEMA.read_text(encoding="utf-8"), prepare=False)
        connection.execute("INSERT INTO categorias_fuente_ruido(codigo,nombre,descripcion) VALUES('TRANSPORTE','Transporte','Tránsito')")
        connection.execute("INSERT INTO tipos_fuente_ruido(codigo,nombre_fuente,id_categoria) VALUES('traffic','Tráfico',1)")
        connection.execute("INSERT INTO estados_reporte(codigo_estado,descripcion) VALUES('received','Recibido')")
        connection.execute("INSERT INTO roles_usuario(nombre_rol,descripcion,activo) VALUES('moderador','Rol previo conservado',FALSE)")
        connection.execute("""INSERT INTO reportes_ciudadanos(id_reporte,id_tipo_fuente,id_estado,descripcion,fecha_hora,id_lugar,proveedor_lugar)
            VALUES('previo-v4',1,1,'Reporte guardado en la versión 4',CURRENT_TIMESTAMP,'W813046785','osm')""")
    return settings


@pytest.fixture
def fresh_settings(settings, database_url):
    """Segundo esquema temporal, creado directamente en v5, para comparar estructuras."""
    schema = "test_" + uuid4().hex
    with psycopg.connect(database_url) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    yield replace(settings, database_schema=schema)
    with psycopg.connect(database_url) as connection:
        connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_v4_database_upgrades_to_v5_without_losing_data(v4_database, fresh_settings):
    for _ in range(2):  # La segunda vez ya es v5 y no vuelve a migrar.
        initialize(v4_database)
    initialize(fresh_settings)
    with db_connection(v4_database) as migrated, db_connection(fresh_settings) as fresh:
        assert migrated.execute("SELECT version FROM decibelcity_schema").fetchone() == (5,)
        assert migrated.execute("SELECT id_lugar,proveedor_lugar,descripcion FROM reportes_ciudadanos WHERE id_reporte='previo-v4'").fetchone() == (
            "W813046785", "osm", "Reporte guardado en la versión 4")
        assert migrated.execute("SELECT nombre_rol,activo FROM roles_usuario ORDER BY id_rol").fetchall() == [
            ("moderador", False), ("ciudadano", True)]
        assert migrated.execute("SELECT COUNT(*) FROM usuarios").fetchone() == (0,)
        # La migración y el esquema nuevo producen exactamente la misma tabla.
        assert structure(migrated) == structure(fresh)


def test_failed_v5_upgrade_rolls_back_to_v4(v4_database, monkeypatch):
    def fail(*args):
        raise RuntimeError("Fallo simulado después de la migración")
    monkeypatch.setattr(migrations, "seed_references", fail)
    with pytest.raises(RuntimeError, match="simulado"):
        initialize(v4_database)
    with db_connection(v4_database) as connection:
        assert connection.execute("SELECT version FROM decibelcity_schema").fetchone() == (4,)
        assert connection.execute("SELECT to_regclass('usuarios')").fetchone() == (None,)
        assert connection.execute("SELECT COUNT(*) FROM roles_usuario").fetchone() == (1,)


def test_version_5_without_usuarios_table_is_rejected(settings):
    initialize(settings)
    with db_connection(settings) as connection:
        connection.execute("DROP TABLE usuarios")
    with pytest.raises(migrations.DatabaseMigrationError, match="no coincide"):
        initialize(settings)


def test_users_have_no_duplicated_territorial_columns(settings):
    initialize(settings)
    with db_connection(settings) as connection:
        columns = {row[0] for row in connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='usuarios'")}
    assert columns == {"id_usuario", "id_rol", "id_distrito", "nombres", "apellidos", "telefono", "direccion",
                       "nombre_usuario", "password_hash", "activo", "fecha_registro"}


def test_fresh_and_migrated_v5_identifiers_use_snake_case(v4_database, fresh_settings):
    for database in (v4_database, fresh_settings):
        initialize(database)
        with db_connection(database) as connection:
            names = connection.execute("""
                SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()
                UNION SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema()
                UNION SELECT indexname FROM pg_indexes WHERE schemaname=current_schema()
            """).fetchall()
        assert names
        assert all(re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", name) for (name,) in names)


def test_valid_user_with_boundary_values_is_accepted(settings):
    initialize(settings)
    with db_connection(settings) as connection:
        insert_user(connection, nombres="Al", apellidos="x" * 80, telefono="123456", direccion="Jr. 1",
                    nombre_usuario="a_b")
        insert_user(connection, nombre_usuario="u" * 40, telefono="9" * 20, direccion="d" * 200)
        assert connection.execute("SELECT COUNT(*) FROM usuarios WHERE activo AND fecha_registro IS NOT NULL").fetchone() == (2,)


@pytest.mark.parametrize("changes", [
    {"nombres": "A"}, {"nombres": "   A   "}, {"nombres": "x" * 81},
    {"apellidos": "B"}, {"apellidos": "y" * 81},
    {"telefono": "12345"}, {"telefono": "1" * 21}, {"telefono": "964-abc-123"}, {"telefono": "964#123456"},
    {"direccion": "Jr 1"}, {"direccion": "     "}, {"direccion": "d" * 201},
    {"nombre_usuario": "ab"}, {"nombre_usuario": "u" * 41}, {"nombre_usuario": "ana quispe"},
    {"nombre_usuario": "ana@quispe"}, {"nombre_usuario": "ñandú"},
    {"password_hash": ""}, {"password_hash": "   "},
    {"nombres": None}, {"password_hash": None},
])
def test_user_check_constraints(settings, changes):
    initialize(settings)
    with db_connection(settings) as connection:
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            insert_user(connection, **changes)
        assert connection.execute("SELECT COUNT(*) FROM usuarios").fetchone() == (0,)


@pytest.mark.parametrize("duplicate", ["ana.quispe", "ANA.Quispe"])
def test_username_is_unique_ignoring_case(settings, duplicate):
    initialize(settings)
    with db_connection(settings) as connection:
        insert_user(connection)
        with pytest.raises(psycopg.errors.UniqueViolation), connection.transaction():
            insert_user(connection, nombre_usuario=duplicate)


@pytest.mark.parametrize("statement", [
    f"INSERT INTO usuarios({USER_COLUMNS}) VALUES (999,1,'Ana','Quispe','123456','Jr. Ica 1','otra','h')",
    f"INSERT INTO usuarios({USER_COLUMNS}) VALUES (1,999,'Ana','Quispe','123456','Jr. Ica 1','otra','h')",
    "DELETE FROM distritos WHERE ubigeo='120101'",
    "DELETE FROM roles_usuario WHERE nombre_rol='ciudadano'",
    "UPDATE usuarios SET activo=NULL",
])
def test_user_foreign_keys_restrict(settings, statement):
    initialize(settings)
    with db_connection(settings) as connection:
        insert_user(connection)
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute(statement)
        assert connection.execute("SELECT COUNT(*) FROM usuarios").fetchone() == (1,)
