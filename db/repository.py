"""Repositório genérico e deliberadamente pequeno para evitar SQL nas telas."""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .database import Database


class Repository:
    """Operações de consulta reutilizáveis sobre uma instância :class:`Database`."""

    def __init__(self, database: Database):
        self.database = database

    def one(self, sql: str, parameters: Iterable[Any] = ()) -> Optional[dict]:
        return self.database.fetch_one(sql, parameters)

    def all(self, sql: str, parameters: Iterable[Any] = ()) -> list[dict]:
        return self.database.fetch_all(sql, parameters)
