"""PostgreSQL portátil para desarrollo; no registra servicios de Windows."""

import argparse
import json
import os
import re
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
# Datos locales de base de datos (excluidos de Git) en la carpeta Database/ del proyecto.
DATA = ROOT.parent / "Database" / "local"
BIN = DATA / "postgresql-runtime" / "pgsql" / "bin"
CLUSTER = DATA / "postgresql-local"
CREDENTIALS = DATA / "postgresql-local.json"
PORT = 55432


def run(program: str, *args: str, env=None, check=True):
    return subprocess.run(
        [str(BIN / f"{program}.exe"), *args], check=check, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def url(user: str, password: str, database: str) -> str:
    return f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@127.0.0.1:{PORT}/{database}"


def configure_environment(credentials: dict) -> None:
    path = ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    keys = {"DECIBELCITY_DB_PATH", "DECIBELCITY_DATABASE_URL", "DECIBELCITY_TEST_DATABASE_URL"}
    lines = [line for line in lines if line.split("=", 1)[0].strip() not in keys]
    for key, database in [("DECIBELCITY_DATABASE_URL", "decibelcity"),
                          ("DECIBELCITY_TEST_DATABASE_URL", "decibelcity_test")]:
        lines.append(f"{key}={url('decibelcity', credentials['app_password'], database)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def start() -> None:
    status = run("pg_ctl", "-D", str(CLUSTER), "status", check=False)
    if status.returncode == 3:
        run("pg_ctl", "-D", str(CLUSTER), "-l", str(DATA / "postgresql-local.log"),
            "-w", "-t", "20", "-o", f"-h 127.0.0.1 -p {PORT}", "start")
    elif status.returncode != 0:
        raise RuntimeError("No se pudo comprobar el estado de PostgreSQL local.")


def initialize() -> None:
    if not (BIN / "initdb.exe").exists():
        raise SystemExit("Faltan los binarios oficiales: consulta Database/base-datos.md.")
    DATA.mkdir(parents=True, exist_ok=True)
    if not CREDENTIALS.exists():
        if CLUSTER.exists():
            raise SystemExit("Existe un clúster sin configuración local; no se sobrescribirá.")
        CREDENTIALS.write_text(json.dumps({"admin_password": secrets.token_urlsafe(32),
                                           "app_password": secrets.token_urlsafe(32)}), encoding="utf-8")
    credentials = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
    if not (CLUSTER / "PG_VERSION").exists():
        password_file = DATA / "postgresql-init-password.txt"
        try:
            password_file.write_text(credentials["admin_password"], encoding="utf-8")
            run("initdb", "-D", str(CLUSTER), "-U", "postgres", "--encoding=UTF8", "--locale=C",
                "--auth-host=scram-sha-256", "--auth-local=scram-sha-256", f"--pwfile={password_file}")
        finally:
            password_file.unlink(missing_ok=True)
    start()
    with psycopg.connect(url("postgres", credentials["admin_password"], "postgres"), autocommit=True) as connection:
        if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname='decibelcity'").fetchone():
            connection.execute(sql.SQL("CREATE ROLE decibelcity LOGIN PASSWORD {}").format(sql.Literal(credentials["app_password"])))
        for database in ("decibelcity", "decibelcity_test"):
            if not connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).fetchone():
                connection.execute(sql.SQL("CREATE DATABASE {} OWNER decibelcity").format(sql.Identifier(database)))
    configure_environment(credentials)
    print("PostgreSQL local listo en 127.0.0.1:55432. Conexiones guardadas en backend/.env.")


def backup() -> None:
    credentials = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
    folder = DATA / "backups"
    folder.mkdir(exist_ok=True)
    target = folder / f"decibelcity-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.dump"
    env = os.environ | {"PGPASSWORD": credentials["app_password"]}
    run("pg_dump", "-h", "127.0.0.1", "-p", str(PORT), "-U", "decibelcity", "-d", "decibelcity",
        "--format=custom", "--no-owner", "--file", str(target), env=env)
    print(f"Respaldo creado: {target}")


def restore(source: Path, database: str) -> None:
    if not source.is_file() or not re.fullmatch(r"decibelcity_restore(?:_[a-z0-9]+)?", database):
        raise SystemExit("Selecciona un respaldo existente y un destino decibelcity_restore o decibelcity_restore_<sufijo>.")
    credentials = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
    with psycopg.connect(url("postgres", credentials["admin_password"], "postgres"), autocommit=True) as connection:
        if connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).fetchone():
            raise SystemExit("La base de destino ya existe; no se sobrescribirá.")
        connection.execute(sql.SQL("CREATE DATABASE {} OWNER decibelcity").format(sql.Identifier(database)))
    env = os.environ | {"PGPASSWORD": credentials["app_password"]}
    run("pg_restore", "-h", "127.0.0.1", "-p", str(PORT), "-U", "decibelcity", "-d", database,
        "--single-transaction", "--no-owner", "--exit-on-error", str(source.resolve()), env=env)
    print(f"Respaldo restaurado en {database}. La base original se conserva.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "start", "stop", "status", "backup", "restore"))
    parser.add_argument("--file", type=Path)
    parser.add_argument("--database")
    args = parser.parse_args()
    action = args.action
    if action == "init":
        initialize()
    elif action == "start":
        start()
    elif action == "stop":
        run("pg_ctl", "-D", str(CLUSTER), "-m", "fast", "-w", "-t", "20", "stop")
    elif action == "status":
        raise SystemExit(run("pg_ctl", "-D", str(CLUSTER), "status", check=False).returncode)
    elif action == "restore":
        if not args.file or not args.database:
            parser.error("restore requiere --file y --database")
        restore(args.file, args.database)
    else:
        backup()
