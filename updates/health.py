from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class HealthResult:
    healthy: bool
    code: str = "OK"


def run_health_check(database_path: str | Path, *, expected_schema: int) -> HealthResult:
    """Valida sem migrar, escrever vendas ou criar credenciais."""

    path = Path(database_path)
    if not path.is_file():
        return HealthResult(False, "DATABASE_MISSING")
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return HealthResult(False, "INTEGRITY")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                return HealthResult(False, "FOREIGN_KEYS")
            version_row = connection.execute(
                "SELECT valor FROM schema_meta WHERE chave='schema_version'"
            ).fetchone()
            installation = connection.execute(
                "SELECT installation_id, state FROM installation_state WHERE singleton=1"
            ).fetchone()
            if version_row is None or int(version_row[0]) != int(expected_schema):
                return HealthResult(False, "SCHEMA")
            if installation is None or len(str(installation[0])) != 36 or installation[1] not in {"UNINITIALIZED", "READY"}:
                return HealthResult(False, "INSTALLATION_STATE")
        finally:
            connection.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return HealthResult(False, "DATABASE_READ")
    return HealthResult(True)

