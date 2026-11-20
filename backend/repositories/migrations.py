"""Inicialización PostgreSQL transaccional, serializada por base y esquema."""

from collections.abc import Sequence
from pathlib import Path

from psycopg import Connection

from repositories.catalogs import insert_hotspot, seed_references, seed_sources
from schemas.noise import Hotspot, NoiseSourceOption

SCHEMA_VERSION = 5
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
# Actualizaciones in situ: versión de origen → script que la lleva a la siguiente.
UPGRADES = {
    3: Path(__file__).with_name("upgrade_v4.sql"),
    4: Path(__file__).with_name("upgrade_v5.sql"),
}
# Las diez tablas de la exportación v2, en orden de dependencias. Es también el conjunto
# de tablas de las versiones 3 y 4.
TABLE_ORDER = ("departamentos", "provincias", "distritos", "zonificaciones",
               "categorias_fuente_ruido", "roles_usuario", "tipos_fuente_ruido",
               "estados_reporte", "reportes_ciudadanos", "puntos_criticos")
SNAPSHOT_TABLES = frozenset(TABLE_ORDER)
# Tablas de cuentas (v5). No forman parte de la exportación v2: nunca se importan hashes.
ACCOUNT_TABLES = ("usuarios",)
TABLES = SNAPSHOT_TABLES | set(ACCOUNT_TABLES)
_VERSION_TABLE = {"decibelcity_schema"}
_KNOWN_TABLE_SETS = (TABLES | _VERSION_TABLE, SNAPSHOT_TABLES | _VERSION_TABLE)
HOTSPOT_SELECT = """
    SELECT p.id_punto AS id, p.nombre AS name, p.area,
           COALESCE(d.nombre_distrito, p.distrito_heredado) AS district,
           p.sector, p.nivel_db AS decibels, f.codigo AS source,
           p.etiqueta_fuente AS source_label, p.descripcion AS description,
           p.franja_horaria AS time_label, p.x, p.y,
           p.limite_referencia AS reference_limit, p.ilustrativo AS illustrative,
           p.latitud AS latitude, p.longitud AS longitude
    FROM puntos_criticos p JOIN tipos_fuente_ruido f USING (id_tipo_fuente)
    LEFT JOIN distritos d USING (id_distrito)
    ORDER BY p.id_punto
"""


class DatabaseMigrationError(RuntimeError):
    pass


def _current_tables(connection: Connection) -> set[str]:
    return {row["table_name"] for row in connection.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = current_schema() AND table_type='BASE TABLE'
    """)}


def prepare_schema(connection: Connection) -> None:
    # La cerradura dura hasta COMMIT/ROLLBACK y coordina las importaciones.
    connection.execute("SELECT pg_advisory_xact_lock(hashtext(current_database()), hashtext(current_schema()))")
    tables = _current_tables(connection)
    if not tables:
        connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"), prepare=False)
    elif tables not in _KNOWN_TABLE_SETS:
        raise DatabaseMigrationError("El esquema contiene tablas no reconocidas; no se modificará.")
    while True:
        versions = connection.execute("SELECT version FROM decibelcity_schema").fetchall()
        if versions == [{"version": SCHEMA_VERSION}]:
            # Una base marcada como actual pero sin las tablas de la versión no es coherente.
            if _current_tables(connection) != TABLES | _VERSION_TABLE:
                raise DatabaseMigrationError("La estructura no coincide con la versión registrada; no se modificará.")
            return
        if len(versions) != 1 or versions[0]["version"] not in UPGRADES:
            raise DatabaseMigrationError("La versión de la base de datos no es compatible con este backend.")
        # Misma transacción que el resto del arranque: un fallo revierte la actualización completa.
        connection.execute(UPGRADES[versions[0]["version"]].read_text(encoding="utf-8"), prepare=False)


def initialize_database(connection: Connection, hotspots: Sequence[Hotspot], sources: Sequence[NoiseSourceOption]) -> None:
    prepare_schema(connection)
    seed_references(connection)
    seed_sources(connection, sources)
    for point in hotspots:
        insert_hotspot(connection, point, seed=True)
