"""Persistencia PostgreSQL; cada operación tiene su conexión y transacción."""

import re
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from repositories.migrations import HOTSPOT_SELECT, initialize_database
from schemas.noise import Hotspot, NoiseReport, NoiseSourceOption


class NoiseRepository:
    def __init__(self, database_url: str, database_schema: str = "public") -> None:
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("La conexión debe utilizar PostgreSQL.")
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", database_schema):
            raise ValueError("El nombre del esquema PostgreSQL no es válido.")
        self.database_url = database_url
        self.database_schema = database_schema

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=5) as connection:
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.database_schema)))
            connection.execute("SET TIME ZONE 'UTC'")
            yield connection

    def initialize(self, hotspots: Sequence[Hotspot], sources: Sequence[NoiseSourceOption]) -> None:
        with self._connection() as connection:
            initialize_database(connection, hotspots, sources)

    def list_hotspots(self) -> list[Hotspot]:
        with self._connection() as connection:
            rows = connection.execute(HOTSPOT_SELECT).fetchall()
        return [Hotspot.model_validate(dict(row)) for row in rows]

    def list_sources(self) -> tuple[NoiseSourceOption, ...]:
        with self._connection() as connection:
            rows = connection.execute("SELECT codigo, nombre_fuente FROM tipos_fuente_ruido ORDER BY id_tipo_fuente").fetchall()
        return tuple(NoiseSourceOption(value=row["codigo"], label=row["nombre_fuente"]) for row in rows)

    def save_report(self, report: NoiseReport) -> NoiseReport:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO reportes_ciudadanos
                    (id_reporte, id_tipo_fuente, descripcion, nivel_estimado_db, fecha_hora, id_estado, id_lugar, proveedor_lugar)
                VALUES (%s, (SELECT id_tipo_fuente FROM tipos_fuente_ruido WHERE codigo = %s), %s, %s, %s,
                        (SELECT id_estado FROM estados_reporte WHERE codigo_estado = %s), %s, 'osm')
                """,
                (
                    report.id,
                    report.source.value,
                    report.description,
                    report.decibels,
                    report.created_at,
                    report.status,
                    report.place_id,
                ),
            )
        return report
