"""Importación atómica de una exportación v2; no abre ni modifica bases antiguas.

La exportación v2 contiene solo las diez tablas de catálogos, reportes y puntos. Las cuentas
de usuario (v5) no se importan: la base de destino debe tener también `usuarios` vacía.
"""

import math
from datetime import datetime

from psycopg import sql

from repositories.migrations import ACCOUNT_TABLES, SNAPSHOT_TABLES, TABLE_ORDER, DatabaseMigrationError, prepare_schema
from repositories.noise_repository import NoiseRepository


def normalize(value, column: str, data_type: str):
    if data_type == "boolean":
        if type(value) not in (int, bool) or value not in (0, 1):
            raise ValueError(f"Valor booleano inválido: {column}.")
        return bool(value)
    if value is None:
        return None
    if data_type == "timestamp with time zone":
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.utcoffset() is None:
            raise ValueError("La fecha debe incluir zona horaria.")
        return result
    if data_type == "integer" and type(value) is not int:
        raise ValueError(f"Identificador inválido: {column}.")
    if data_type == "double precision" and (type(value) not in (int, float) or not math.isfinite(value)):
        raise ValueError(f"Número inválido: {column}.")
    if data_type == "text" and not isinstance(value, str):
        raise ValueError(f"Texto inválido: {column}.")
    return value


def import_snapshot(repository: NoiseRepository, snapshot: dict) -> dict[str, int]:
    if (set(snapshot) != {"format", "tables"} or snapshot["format"] != "decibelcity-v2"
            or not isinstance(snapshot["tables"], dict) or set(snapshot["tables"]) != SNAPSHOT_TABLES):
        raise ValueError("Formato de exportación no reconocido; se requieren las diez tablas v2.")
    with repository._connection() as connection:
        prepare_schema(connection)
        for table in (*TABLE_ORDER, *ACCOUNT_TABLES):
            if connection.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(table))).fetchone():
                raise DatabaseMigrationError("La importación requiere tablas vacías; no se sobrescribirán datos.")
        counts = {}
        for table in TABLE_ORDER:
            columns = connection.execute("""SELECT column_name, data_type, is_identity
                FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s
                ORDER BY ordinal_position""", (table,)).fetchall()
            names = [column["column_name"] for column in columns]
            rows = snapshot["tables"][table]
            if not isinstance(rows, list):
                raise ValueError(f"Filas inválidas: {table}.")
            expected = []
            for row in rows:
                if not isinstance(row, dict) or set(row) != set(names):
                    raise ValueError(f"Columnas no reconocidas: {table}.")
                expected.append({column["column_name"]: normalize(row[column["column_name"]], column["column_name"], column["data_type"])
                                 for column in columns})
            statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, names)),
                sql.SQL(",").join(sql.Placeholder() for _ in names))
            with connection.cursor() as cursor:
                cursor.executemany(statement, [tuple(row[name] for name in names) for row in expected])
            actual = connection.execute(sql.SQL("SELECT * FROM {} ORDER BY {}").format(sql.Identifier(table), sql.Identifier(names[0]))).fetchall()
            if actual != sorted(expected, key=lambda row: row[names[0]]):
                raise DatabaseMigrationError(f"La comparación del contenido importado falló: {table}.")
            # RESTART es transaccional; setval() no revierte su estado al cancelar.
            for column in columns:
                if column["is_identity"] == "YES":
                    next_id = max((row[column["column_name"]] for row in expected), default=0) + 1
                    connection.execute(sql.SQL("ALTER TABLE {} ALTER COLUMN {} RESTART WITH {}").format(
                        sql.Identifier(table), sql.Identifier(column["column_name"]), sql.Literal(max(1, next_id))))
            counts[table] = len(actual)
    return counts
