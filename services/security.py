"""Validação de atores e autorização compartilhada entre serviços."""

from __future__ import annotations

import sqlite3
from typing import Any

from .errors import AuthorizationError, NotFoundError, PasswordChangeRequiredError, ValidationError


def user_id(value: Any, field: str = "usuário") -> int:
    if isinstance(value, bool):
        raise ValidationError(f"Informe um {field} válido.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Informe um {field} válido.") from exc
    if parsed <= 0:
        raise ValidationError(f"Informe um {field} válido.")
    return parsed


def get_active_user(
    connection: sqlite3.Connection,
    actor_id: Any,
    *,
    allow_password_change: bool = False,
) -> dict:
    parsed = user_id(actor_id)
    row = connection.execute(
        "SELECT id, nome, login, perfil, ativo, deve_trocar_senha FROM usuarios WHERE id = ?", (parsed,)
    ).fetchone()
    if row is None:
        raise NotFoundError("Usuário não encontrado.")
    actor = dict(row)
    if not actor["ativo"]:
        raise AuthorizationError("Este usuário está inativo.")
    if actor["deve_trocar_senha"] and not allow_password_change:
        raise PasswordChangeRequiredError("Troque sua senha antes de continuar.")
    return actor


def require_admin(connection: sqlite3.Connection, actor_id: Any) -> dict:
    actor = get_active_user(connection, actor_id)
    if actor["perfil"] != "admin":
        raise AuthorizationError("Esta operação exige um usuário administrador.")
    return actor


def can_access_cash(connection: sqlite3.Connection, cash_row: dict, actor_id: Any) -> dict:
    actor = get_active_user(connection, actor_id)
    if actor["perfil"] != "admin" and int(cash_row["usuario_id"]) != actor["id"]:
        raise AuthorizationError("Você só pode operar o seu próprio caixa.")
    return actor


def public_user(row: dict) -> dict:
    return {
        "id": row["id"],
        "nome": row["nome"],
        "login": row["login"],
        "perfil": row["perfil"],
        "ativo": bool(row["ativo"]),
        "deve_trocar_senha": bool(row.get("deve_trocar_senha", 0)),
        "recovery_configured": bool(row.get("codigo_recuperacao_hash")),
        "criado_em": row.get("criado_em"),
        "atualizado_em": row.get("atualizado_em"),
    }
