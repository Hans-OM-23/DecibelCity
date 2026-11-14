"""Importar una exportación v2 en PostgreSQL vacío, sin sobrescribir registros."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings
from repositories.data_import import import_snapshot
from repositories.noise_repository import NoiseRepository

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env()
    counts = import_snapshot(NoiseRepository(settings.database_url, settings.database_schema),
                             json.loads(args.snapshot.read_text(encoding="utf-8")))
    print("Importación completa y verificada:", counts)
