"""Conexión PostgreSQL compartida por los repositorios; una conexión y transacción por operación."""

import re
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


class PostgresDatabase:
    def __init__(self, database_url: str, database_schema: str = "public") -> None:
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("La conexión debe utilizar PostgreSQL.")
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", database_schema):
            raise ValueError("El nombre del esquema PostgreSQL no es válido.")
        self.database_url = database_url
        self.database_schema = database_schema

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection]:
        """Confirma al salir sin errores y revierte ante cualquier excepción."""
        with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=5) as connection:
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.database_schema)))
            connection.execute("SET TIME ZONE 'UTC'")
            yield connection
