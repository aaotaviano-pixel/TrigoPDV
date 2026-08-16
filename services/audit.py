"""Registro imutável de ações sensíveis do PDV."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Optional

from db.database import Database

from .errors import ValidationError
from .security import require_admin


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class AuditService:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def record(
        connection: sqlite3.Connection,
        action: str,
        entity: str,
        *,
        entity_id: Any = None,
        actor_id: Optional[int] = None,
        details: Any = None,
    ) -> int:
        """Grava dentro da transação atual da operação de negócio."""

        action = (action or "").strip().upper()
        entity = (entity or "").strip().upper()
        if not action or not entity:
            raise ValidationError("Ação e entidade de auditoria são obrigatórias.")
        actor_login = None
        if actor_id is not None:
            row = connection.execute("SELECT login FROM usuarios WHERE id = ?", (actor_id,)).fetchone()
            actor_login = row["login"] if row else None
        try:
            serialized = json.dumps(details, ensure_ascii=False, sort_keys=True, default=str) if details is not None else None
        except (TypeError, ValueError) as exc:
            raise ValidationError("Detalhes de auditoria inválidos.") from exc
        cursor = connection.execute(
            "INSERT INTO logs_auditoria(usuario_id, usuario_login, acao, entidade, entidade_id, detalhes, criado_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (actor_id, actor_login, action, entity, str(entity_id) if entity_id is not None else None, serialized, now()),
        )
        return int(cursor.lastrowid)

    def log(self, action: str, entity: str, *, entity_id: Any = None, actor_id: Optional[int] = None, details: Any = None) -> int:
        with self.database.transaction(write=True) as connection:
            return self.record(connection, action, entity, entity_id=entity_id, actor_id=actor_id, details=details)

    def list_events(
        self,
        actor_id: int,
        *,
        limit: int = 200,
        entity: Optional[str] = None,
        entity_id: Optional[str] = None,
    ) -> list[dict]:
        try:
            safe_limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Limite de auditoria inválido.") from exc
        safe_limit = max(1, min(safe_limit, 500))
        clauses: list[str] = []
        parameters: list[Any] = []
        if entity:
            clauses.append("entidade = ?")
            parameters.append(str(entity).strip().upper())
        if entity_id is not None:
            clauses.append("entidade_id = ?")
            parameters.append(str(entity_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(safe_limit)
        with self.database.transaction() as connection:
            require_admin(connection, actor_id)
            rows = connection.execute(
                "SELECT * FROM logs_auditoria" + where + " ORDER BY id DESC LIMIT ?", parameters
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                if item["detalhes"]:
                    try:
                        item["detalhes"] = json.loads(item["detalhes"])
                    except json.JSONDecodeError:
                        pass
                result.append(item)
            return result
