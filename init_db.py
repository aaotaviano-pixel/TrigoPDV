"""Inicialização idempotente do banco local do PDV Trigo de Minas."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config.settings import ConfigurationError, ensure_default_config, load_settings
from db.database import Database, DatabaseError
from runtime.single_instance import SingleInstanceError, SingleInstanceGuard
from updates.startup import startup_preflight


def initialize(config_path: str | Path | None = None) -> Path:
    """Cria a configuração e o schema sem fabricar credenciais."""

    if config_path is not None:
        ensure_default_config(Path(config_path))
        settings = load_settings(Path(config_path))
    else:
        settings = load_settings()
    with SingleInstanceGuard(settings.database_path):
        if hasattr(settings, "project_root") and hasattr(settings, "update_state_path"):
            startup_preflight(settings)
        else:
            database = Database(settings.database_path)
            database.initialize()
    return settings.database_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicializa o banco do PDV Trigo de Minas.")
    parser.add_argument("--config", help="Caminho alternativo para o config.ini")
    args = parser.parse_args()
    try:
        database_path = initialize(args.config)
    except (ConfigurationError, DatabaseError, SingleInstanceError, OSError) as exc:
        print(f"Erro ao inicializar o PDV: {exc}", file=sys.stderr)
        return 1
    print(f"Banco de dados pronto: {database_path}")
    print("Estrutura verificada; conclua o provisionamento na primeira execução.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
