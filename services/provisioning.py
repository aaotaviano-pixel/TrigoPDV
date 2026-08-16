"""Provisionamento transacional da primeira identidade administrativa."""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass

from db.database import Database

from .audit import AuditService, now
from .auth import _login, _name, _recovery_code
from .errors import ConflictError
from .passwords import hash_password, validate_new_password
from .security import public_user


@dataclass(frozen=True)
class ProvisioningStatus:
    installation_id: str
    state: str
    requires_provisioning: bool


class ProvisioningService:
    """Controla o único caminho permitido para criar o primeiro usuário."""

    def __init__(self, database: Database):
        self.database = database
        self.database.initialize()

    def status(self) -> ProvisioningStatus:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT installation_id, state FROM installation_state WHERE singleton = 1"
            ).fetchone()
        if row is None:
            raise ConflictError("O estado de instalação não está disponível.")
        state = str(row["state"])
        return ProvisioningStatus(
            installation_id=str(row["installation_id"]),
            state=state,
            requires_provisioning=state == "UNINITIALIZED",
        )

    @staticmethod
    def generate_recovery_code() -> str:
        """Gera um código para exibição única, sem qualquer persistência."""

        return secrets.token_urlsafe(24)

    def provision_initial_admin(
        self,
        name: str,
        login: str,
        password: str,
        recovery_code: str,
    ) -> dict:
        normalized_name = _name(name)
        normalized_login = _login(login)
        validate_new_password(password)
        normalized_recovery_code = _recovery_code(recovery_code)
        password_hash = hash_password(password)
        recovery_hash = hash_password(normalized_recovery_code)

        with self.database.transaction(write=True) as connection:
            installation = connection.execute(
                "SELECT installation_id, state FROM installation_state WHERE singleton = 1"
            ).fetchone()
            user_count = int(connection.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0])
            if installation is None or installation["state"] != "UNINITIALIZED" or user_count != 0:
                raise ConflictError("Esta instalação não pode mais ser provisionada.")
            timestamp = now()
            try:
                cursor = connection.execute(
                    "INSERT INTO usuarios("
                    "nome, login, senha_hash, perfil, ativo, deve_trocar_senha, "
                    "codigo_recuperacao_hash, criado_em, atualizado_em"
                    ") VALUES (?, ?, ?, 'admin', 1, 0, ?, ?, ?)",
                    (
                        normalized_name,
                        normalized_login,
                        password_hash,
                        recovery_hash,
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("Esta instalação não pode mais ser provisionada.") from exc
            updated = connection.execute(
                "UPDATE installation_state SET state = 'READY', provisioned_at = ? "
                "WHERE singleton = 1 AND state = 'UNINITIALIZED'",
                (timestamp,),
            )
            if updated.rowcount != 1:
                raise ConflictError("Esta instalação não pode mais ser provisionada.")
            AuditService.record(
                connection,
                "INSTALACAO_PROVISIONADA",
                "INSTALACAO",
                entity_id=installation["installation_id"],
                actor_id=cursor.lastrowid,
                details={
                    "installation_id": installation["installation_id"],
                    "login": normalized_login,
                },
            )
            row = connection.execute(
                "SELECT * FROM usuarios WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return public_user(dict(row))
