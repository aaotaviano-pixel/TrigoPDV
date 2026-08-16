"""Autenticação local, perfis e administração de usuários."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Optional

from db.database import Database

from .audit import AuditService, now
from .errors import AuthenticationError, AuthorizationError, ConflictError, NotFoundError, ValidationError
from .passwords import hash_password, validate_new_password, verify_password
from .rate_limit import (
    PASSWORD_FIELDS,
    PASSWORD_POLICY,
    RECOVERY_FIELDS,
    RECOVERY_POLICY,
    Clock,
    RateLimitFields,
    RateLimitPolicy,
    RateLimitState,
    clock_utc,
    format_utc,
    persist_state,
)
from .security import get_active_user, public_user, require_admin, user_id


VALID_ROLES = {"admin", "caixa"}


def _login(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("Informe o login do usuário.")
    result = value.strip()
    if not result or len(result) > 60:
        raise ValidationError("O login deve ter entre 1 e 60 caracteres.")
    return result


def _name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("Informe o nome do usuário.")
    result = " ".join(value.split())
    if not result or len(result) > 120:
        raise ValidationError("O nome deve ter entre 1 e 120 caracteres.")
    return result


def _role(value: Any) -> str:
    result = str(value or "").strip().lower()
    if result not in VALID_ROLES:
        raise ValidationError("Perfil inválido. Use 'admin' ou 'caixa'.")
    return result


def _recovery_code(value: Any) -> str:
    """Valida o código que comprova a posse do acesso administrativo."""

    if not isinstance(value, str):
        raise ValidationError("Informe o código de recuperação.")
    result = value.strip()
    if len(result) < 12 or len(result) > 180:
        raise ValidationError("O código de recuperação deve ter entre 12 e 180 caracteres.")
    return result


class AuthService:
    """Serviço de identidade. Nunca devolve ``senha_hash`` à interface."""

    def __init__(
        self,
        database: Database,
        audit: Optional[AuditService] = None,
        *,
        clock: Clock | None = None,
        login_policy: RateLimitPolicy = PASSWORD_POLICY,
        recovery_policy: RateLimitPolicy = RECOVERY_POLICY,
    ):
        self.database = database
        self.audit = audit or AuditService(database)
        self.clock = clock
        self.login_policy = login_policy
        self.recovery_policy = recovery_policy

    def _at(self) -> datetime:
        return clock_utc(self.clock)

    @staticmethod
    def _state(row: sqlite3.Row | dict, fields: RateLimitFields) -> RateLimitState:
        return RateLimitState.from_row(dict(row), fields)

    @staticmethod
    def _verify_password_proof_in_transaction(
        connection: sqlite3.Connection,
        row: sqlite3.Row | dict,
        senha: Any,
        *,
        requested_by: Optional[int],
        clock: Clock | None,
        policy: RateLimitPolicy,
        rejected_action: str,
    ) -> bool:
        """Valida uma prova de senha pelo único limiter persistente."""

        user = dict(row)
        at = clock_utc(clock)
        state = RateLimitState.from_row(user, PASSWORD_FIELDS)
        normalized_state = state.normalized(at, policy)
        if normalized_state != state:
            persist_state(
                connection,
                int(user["id"]),
                PASSWORD_FIELDS,
                normalized_state,
                updated_at=at,
            )
        rejected = normalized_state.is_blocked(at, policy)
        if not rejected and (
            not isinstance(senha, str)
            or not senha
            or not verify_password(senha, user["senha_hash"])
        ):
            persist_state(
                connection,
                int(user["id"]),
                PASSWORD_FIELDS,
                normalized_state.register_failure(at, policy),
                updated_at=at,
            )
            rejected = True
        if rejected:
            AuditService.record(
                connection,
                rejected_action,
                "AUTENTICACAO",
                entity_id=user["id"],
                actor_id=requested_by,
                details={"resultado": "rejeitado"},
            )
            return False
        persist_state(
            connection,
            int(user["id"]),
            PASSWORD_FIELDS,
            RateLimitState.cleared(),
            updated_at=at,
        )
        return True

    def authenticate(self, login: str, senha: str) -> Optional[dict]:
        """Retorna o usuário público ou ``None`` para credenciais inválidas."""

        try:
            normalized_login = _login(login)
        except ValidationError:
            normalized_login = None
        valid_password_input = isinstance(senha, str) and bool(senha)
        at = self._at()
        with self.database.transaction(write=True) as connection:
            row = (
                connection.execute(
                    "SELECT * FROM usuarios WHERE login = ?", (normalized_login,)
                ).fetchone()
                if normalized_login is not None
                else None
            )
            if row is None or not row["ativo"]:
                AuditService.record(
                    connection,
                    "LOGIN_FALHOU",
                    "AUTENTICACAO",
                    actor_id=None,
                    details={"resultado": "rejeitado"},
                )
                return None
            state = self._state(row, PASSWORD_FIELDS)
            normalized_state = state.normalized(at, self.login_policy)
            if normalized_state != state:
                persist_state(
                    connection,
                    int(row["id"]),
                    PASSWORD_FIELDS,
                    normalized_state,
                    updated_at=at,
                )
            if normalized_state.is_blocked(at, self.login_policy):
                AuditService.record(
                    connection,
                    "LOGIN_FALHOU",
                    "AUTENTICACAO",
                    actor_id=None,
                    details={"resultado": "rejeitado"},
                )
                return None
            if not valid_password_input or not verify_password(senha, row["senha_hash"]):
                failed_state = normalized_state.register_failure(at, self.login_policy)
                persist_state(
                    connection,
                    int(row["id"]),
                    PASSWORD_FIELDS,
                    failed_state,
                    updated_at=at,
                )
                AuditService.record(
                    connection,
                    "LOGIN_FALHOU",
                    "AUTENTICACAO",
                    actor_id=None,
                    details={"resultado": "rejeitado"},
                )
                return None
            persist_state(
                connection,
                int(row["id"]),
                PASSWORD_FIELDS,
                RateLimitState.cleared(),
                updated_at=at,
            )
            updated = connection.execute(
                "SELECT * FROM usuarios WHERE id = ?", (row["id"],)
            ).fetchone()
            user = public_user(dict(updated))
            AuditService.record(
                connection,
                "LOGIN_SUCESSO",
                "AUTENTICACAO",
                entity_id=user["id"],
                actor_id=user["id"],
            )
            return user

    def password_recovery_available(self, login: str) -> bool:
        """Informa se a recuperação por código está liberada para um administrador.

        A tela só revela essa ação depois de cinco erros na própria conta e
        apenas quando o administrador já cadastrou o código local.
        """

        try:
            normalized_login = _login(login)
        except ValidationError:
            return False
        at = self._at()
        with self.database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM usuarios WHERE login = ? AND perfil = 'admin' "
                "AND ativo = 1 AND codigo_recuperacao_hash IS NOT NULL",
                (normalized_login,),
            ).fetchone()
            if row is None:
                return False
            state = self._state(row, PASSWORD_FIELDS)
            normalized_state = state.normalized(at, self.login_policy)
            if normalized_state != state:
                persist_state(
                    connection,
                    int(row["id"]),
                    PASSWORD_FIELDS,
                    normalized_state,
                    updated_at=at,
                )
            return normalized_state.failures >= self.login_policy.threshold

    def recover_password_with_code(
        self, login: str, recovery_code: str, new_password: str, new_recovery_code: str
    ) -> dict:
        """Recupera uma conta administrativa mediante código previamente guardado.

        O código é substituído no mesmo ato para que o anterior não possa ser
        reutilizado. Senhas e códigos nunca entram no log de auditoria.
        """

        normalized_login = _login(login)
        normalized_code = _recovery_code(recovery_code)
        validate_new_password(new_password)
        normalized_new_code = _recovery_code(new_recovery_code)
        if normalized_code == normalized_new_code:
            raise ValidationError("Defina um novo código de recuperação diferente do anterior.")
        new_password_hash = hash_password(new_password)
        new_recovery_hash = hash_password(normalized_new_code)
        at = self._at()
        rejected = False
        recovered_user: Optional[dict] = None
        with self.database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM usuarios WHERE login = ? AND perfil = 'admin' AND ativo = 1", (normalized_login,)
            ).fetchone()
            eligible = False
            if row is not None and row["codigo_recuperacao_hash"]:
                login_state = self._state(row, PASSWORD_FIELDS)
                normalized_login_state = login_state.normalized(at, self.login_policy)
                if normalized_login_state != login_state:
                    persist_state(
                        connection,
                        int(row["id"]),
                        PASSWORD_FIELDS,
                        normalized_login_state,
                        updated_at=at,
                    )
                eligible = normalized_login_state.failures >= self.login_policy.threshold
            if not eligible:
                AuditService.record(
                    connection,
                    "RECUPERACAO_SENHA_REJEITADA",
                    "AUTENTICACAO",
                    actor_id=None,
                    details={"resultado": "rejeitado"},
                )
                rejected = True
            else:
                recovery_state = self._state(row, RECOVERY_FIELDS)
                normalized_state = recovery_state.normalized(at, self.recovery_policy)
                if normalized_state != recovery_state:
                    persist_state(
                        connection,
                        int(row["id"]),
                        RECOVERY_FIELDS,
                        normalized_state,
                        updated_at=at,
                    )
                if normalized_state.is_blocked(at, self.recovery_policy):
                    rejected = True
                elif not verify_password(normalized_code, row["codigo_recuperacao_hash"]):
                    persist_state(
                        connection,
                        int(row["id"]),
                        RECOVERY_FIELDS,
                        normalized_state.register_failure(at, self.recovery_policy),
                        updated_at=at,
                    )
                    rejected = True
                else:
                    connection.execute(
                        "UPDATE usuarios SET senha_hash = ?, codigo_recuperacao_hash = ?, "
                        "tentativas_login_falhas = 0, login_falhas_janela_inicio = NULL, "
                        "login_bloqueado_ate = NULL, recuperacao_falhas = 0, "
                        "recuperacao_janela_inicio = NULL, recuperacao_bloqueado_ate = NULL, "
                        "deve_trocar_senha = 0, atualizado_em = ? WHERE id = ?",
                        (
                            new_password_hash,
                            new_recovery_hash,
                            format_utc(at),
                            row["id"],
                        ),
                    )
                    updated = connection.execute(
                        "SELECT * FROM usuarios WHERE id = ?", (row["id"],)
                    ).fetchone()
                    AuditService.record(
                        connection,
                        "SENHA_RECUPERADA_CODIGO",
                        "USUARIO",
                        entity_id=row["id"],
                        actor_id=row["id"],
                        details={"codigo_recuperacao_rotacionado": True},
                    )
                    recovered_user = public_user(dict(updated))
                if rejected:
                    AuditService.record(
                        connection,
                        "RECUPERACAO_SENHA_REJEITADA",
                        "AUTENTICACAO",
                        actor_id=None,
                        details={"resultado": "rejeitado"},
                    )
        if rejected or recovered_user is None:
            raise ValidationError("Não foi possível validar a recuperação de acesso.")
        return recovered_user

    def configure_own_recovery_code(
        self,
        senha_atual: str,
        recovery_code: str,
        *,
        actor_id: int,
    ) -> dict:
        """Configura ou rotaciona o codigo da propria conta administrativa."""

        normalized_code = _recovery_code(recovery_code)
        recovery_hash = hash_password(normalized_code)
        at = self._at()
        updated_user: Optional[dict] = None
        with self.database.transaction(write=True) as connection:
            admin = require_admin(connection, actor_id)
            row = connection.execute(
                "SELECT * FROM usuarios WHERE id = ?", (admin["id"],)
            ).fetchone()
            password_valid = self._verify_password_proof_in_transaction(
                connection,
                row,
                senha_atual,
                requested_by=admin["id"],
                clock=self.clock,
                policy=self.login_policy,
                rejected_action="CONFIGURACAO_RECUPERACAO_REJEITADA",
            )
            if password_valid:
                connection.execute(
                    "UPDATE usuarios SET codigo_recuperacao_hash = ?, recuperacao_falhas = 0, "
                    "recuperacao_janela_inicio = NULL, recuperacao_bloqueado_ate = NULL, "
                    "atualizado_em = ? WHERE id = ?",
                    (recovery_hash, format_utc(at), admin["id"]),
                )
                updated = connection.execute(
                    "SELECT * FROM usuarios WHERE id = ?", (admin["id"],)
                ).fetchone()
                AuditService.record(
                    connection,
                    "CODIGO_RECUPERACAO_CONFIGURADO",
                    "USUARIO",
                    entity_id=admin["id"],
                    actor_id=admin["id"],
                    details={"codigo_recuperacao_rotacionado": bool(row["codigo_recuperacao_hash"])},
                )
                updated_user = public_user(dict(updated))
        if updated_user is None:
            raise AuthenticationError("A senha atual não confere.")
        return updated_user

    def get_user(self, requested_user_id: int, *, actor_id: int) -> dict:
        with self.database.transaction() as connection:
            requested = user_id(requested_user_id)
            actor = get_active_user(connection, actor_id)
            if actor["id"] != requested and actor["perfil"] != "admin":
                raise AuthorizationError("Você não tem permissão para consultar este usuário.")
            row = connection.execute("SELECT * FROM usuarios WHERE id = ?", (requested,)).fetchone()
            if row is None:
                raise NotFoundError("Usuário não encontrado.")
            return public_user(dict(row))

    def list_users(self, *, actor_id: int) -> list[dict]:
        with self.database.transaction() as connection:
            require_admin(connection, actor_id)
            rows = connection.execute("SELECT * FROM usuarios ORDER BY nome COLLATE NOCASE, id").fetchall()
            return [public_user(dict(row)) for row in rows]

    def create_user(self, nome: str, login: str, senha: str, perfil: str, *, actor_id: int) -> dict:
        normalized_name = _name(nome)
        normalized_login = _login(login)
        normalized_role = _role(perfil)
        validate_new_password(senha)
        with self.database.transaction(write=True) as connection:
            require_admin(connection, actor_id)
            try:
                cursor = connection.execute(
                    "INSERT INTO usuarios(nome, login, senha_hash, perfil, ativo, deve_trocar_senha, criado_em, atualizado_em) "
                    "VALUES (?, ?, ?, ?, 1, 1, ?, ?)",
                    (normalized_name, normalized_login, hash_password(senha), normalized_role, now(), now()),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("Já existe um usuário com este login.") from exc
            row = connection.execute("SELECT * FROM usuarios WHERE id = ?", (cursor.lastrowid,)).fetchone()
            AuditService.record(
                connection,
                "USUARIO_CRIADO",
                "USUARIO",
                entity_id=cursor.lastrowid,
                actor_id=actor_id,
                details={"login": normalized_login, "perfil": normalized_role},
            )
            return public_user(dict(row))

    def change_password(
        self,
        requested_user_id: int,
        senha_atual: str,
        nova_senha: str,
        *,
        actor_id: int,
    ) -> dict:
        """Troca atomicamente a senha da própria conta autenticada."""

        requested = user_id(requested_user_id)
        validate_new_password(nova_senha)
        at = self._at()
        updated_user: Optional[dict] = None
        with self.database.transaction(write=True) as connection:
            actor = get_active_user(connection, actor_id, allow_password_change=True)
            if actor["id"] != requested:
                raise AuthorizationError("Você só pode trocar a senha da própria conta.")
            target = connection.execute(
                "SELECT * FROM usuarios WHERE id = ?", (requested,)
            ).fetchone()
            if target is None:
                raise NotFoundError("Usuário não encontrado.")
            password_valid = self._verify_password_proof_in_transaction(
                connection,
                target,
                senha_atual,
                requested_by=actor["id"],
                clock=self.clock,
                policy=self.login_policy,
                rejected_action="SENHA_ATUAL_REJEITADA",
            )
            if password_valid:
                connection.execute(
                    "UPDATE usuarios SET senha_hash = ?, deve_trocar_senha = 0, atualizado_em = ? WHERE id = ?",
                    (hash_password(nova_senha), format_utc(at), requested),
                )
                updated = connection.execute(
                    "SELECT * FROM usuarios WHERE id = ?", (requested,)
                ).fetchone()
                AuditService.record(
                    connection,
                    "SENHA_ALTERADA",
                    "USUARIO",
                    entity_id=requested,
                    actor_id=actor["id"],
                )
                updated_user = public_user(dict(updated))
        if updated_user is None:
            raise AuthenticationError("A senha atual está incorreta.")
        return updated_user

    def reset_user_password(self, requested_user_id: int, senha_temporaria: str, *, actor_id: int) -> dict:
        """Define uma senha temporária por administrador, sem expor segredos."""

        requested = user_id(requested_user_id)
        validate_new_password(senha_temporaria)
        with self.database.transaction(write=True) as connection:
            admin = require_admin(connection, actor_id)
            if requested == admin["id"]:
                raise ValidationError("Use a troca de senha da própria conta para alterar sua senha.")
            row = connection.execute("SELECT * FROM usuarios WHERE id = ?", (requested,)).fetchone()
            if row is None:
                raise NotFoundError("Usuário não encontrado.")
            connection.execute(
                "UPDATE usuarios SET senha_hash = ?, deve_trocar_senha = 1, atualizado_em = ? WHERE id = ?",
                (hash_password(senha_temporaria), now(), requested),
            )
            updated = connection.execute("SELECT * FROM usuarios WHERE id = ?", (requested,)).fetchone()
            AuditService.record(
                connection,
                "SENHA_REDEFINIDA_ADMIN",
                "USUARIO",
                entity_id=requested,
                actor_id=admin["id"],
                details={"troca_de_senha_obrigatoria": True},
            )
            return public_user(dict(updated))

    def verify_user_password(self, requested_user_id: int, senha: str, *, actor_id: int) -> bool:
        """Confere a senha atual sem expor hash; usado antes de troca pelo próprio usuário."""

        requested = user_id(requested_user_id)
        with self.database.transaction(write=True) as connection:
            actor = get_active_user(connection, actor_id)
            if actor["id"] != requested:
                raise AuthorizationError("Você só pode validar a senha da própria conta.")
            row = connection.execute("SELECT * FROM usuarios WHERE id = ?", (requested,)).fetchone()
            if row is None:
                raise NotFoundError("Usuário não encontrado.")
            return self._verify_password_proof_in_transaction(
                connection,
                row,
                senha,
                requested_by=actor["id"],
                clock=self.clock,
                policy=self.login_policy,
                rejected_action="SENHA_ATUAL_REJEITADA",
            )

    def set_user_active(self, requested_user_id: int, active: bool, *, actor_id: int) -> None:
        requested = user_id(requested_user_id)
        if not isinstance(active, bool):
            raise ValidationError("O status do usuário é inválido.")
        with self.database.transaction(write=True) as connection:
            admin = require_admin(connection, actor_id)
            target_row = connection.execute("SELECT * FROM usuarios WHERE id = ?", (requested,)).fetchone()
            if target_row is None:
                raise NotFoundError("Usuário não encontrado.")
            target = dict(target_row)
            if not active and target["id"] == admin["id"]:
                raise ConflictError("O administrador logado não pode desativar a própria conta.")
            if not active and target["perfil"] == "admin":
                count = connection.execute(
                    "SELECT COUNT(*) AS total FROM usuarios WHERE perfil = 'admin' AND ativo = 1"
                ).fetchone()["total"]
                if count <= 1:
                    raise ConflictError("Deve existir ao menos um administrador ativo.")
            connection.execute("UPDATE usuarios SET ativo = ?, atualizado_em = ? WHERE id = ?", (1 if active else 0, now(), requested))
            AuditService.record(
                connection,
                "USUARIO_ATIVADO" if active else "USUARIO_DESATIVADO",
                "USUARIO",
                entity_id=requested,
                actor_id=admin["id"],
            )

    @staticmethod
    def verify_admin_credentials_in_transaction(
        connection: sqlite3.Connection,
        login: str,
        senha: str,
        *,
        requested_by: Optional[int] = None,
        clock: Clock | None = None,
        policy: RateLimitPolicy = PASSWORD_POLICY,
    ) -> Optional[dict]:
        """Canal transacional único para validar uma credencial administrativa."""

        try:
            normalized_login = _login(login)
        except ValidationError:
            normalized_login = None
        if requested_by is not None:
            get_active_user(connection, requested_by)
        row = (
            connection.execute(
                "SELECT * FROM usuarios WHERE login = ? AND perfil = 'admin' AND ativo = 1",
                (normalized_login,),
            ).fetchone()
            if normalized_login is not None
            else None
        )
        if row is None:
            AuditService.record(
                connection,
                "CREDENCIAL_ADMIN_REJEITADA",
                "AUTORIZACAO",
                actor_id=requested_by,
                details={"resultado": "rejeitado"},
            )
            return None
        try:
            require_admin(connection, row["id"])
        except AuthorizationError:
            AuditService.record(
                connection,
                "CREDENCIAL_ADMIN_REJEITADA",
                "AUTORIZACAO",
                actor_id=requested_by,
                details={"resultado": "rejeitado"},
            )
            return None
        if not AuthService._verify_password_proof_in_transaction(
            connection,
            row,
            senha,
            requested_by=requested_by,
            clock=clock,
            policy=policy,
            rejected_action="CREDENCIAL_ADMIN_REJEITADA",
        ):
            return None
        updated = connection.execute(
            "SELECT * FROM usuarios WHERE id = ?", (row["id"],)
        ).fetchone()
        admin = public_user(dict(updated))
        AuditService.record(
            connection,
            "CREDENCIAL_ADMIN_VALIDADA",
            "AUTORIZACAO",
            entity_id=admin["id"],
            actor_id=admin["id"],
            details={"solicitado_por": requested_by},
        )
        return admin

    def verify_admin_credentials(self, login: str, senha: str, *, requested_by: Optional[int] = None) -> Optional[dict]:
        """Valida credencial de administrador para uma ação excepcional (ex.: F5)."""

        with self.database.transaction(write=True) as connection:
            admin = self.verify_admin_credentials_in_transaction(
                connection,
                login,
                senha,
                requested_by=requested_by,
                clock=self.clock,
                policy=self.login_policy,
            )
            return admin
