"""Abertura, movimentação e fechamento cego de caixa."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from typing import Any, Optional

from db.database import Database

from .audit import AuditService, now
from .backup import CashBackupWorker
from .errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from .money import as_float, money
from .security import can_access_cash, get_active_user, require_admin, user_id


MOVEMENT_TYPES = {"SANGRIA": "SANGRIA", "SUPRIMENTO": "SUPRIMENTO", "REFORCO": "SUPRIMENTO"}
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.-]{8,128}$")


def _cash_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValidationError("Informe um caixa válido.")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Informe um caixa válido.") from exc
    if result <= 0:
        raise ValidationError("Informe um caixa válido.")
    return result


def _note(value: Any, field: str = "observação", *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"A {field} é inválida.")
    result = " ".join(value.split())
    if required and not result:
        raise ValidationError(f"Informe uma {field} para justificar a diferença.")
    if len(result) > 500:
        raise ValidationError(f"A {field} pode ter no máximo 500 caracteres.")
    return result


def _resume_reason(value: Any) -> str:
    result = _note(value, "justificativa", required=True)
    if not 8 <= len(result) <= 250:
        raise ValidationError("A justificativa deve ter entre 8 e 250 caracteres.")
    return result


def _idempotency_key(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not IDEMPOTENCY_KEY.fullmatch(value):
        raise ValidationError("A chave da movimentação é inválida.")
    return value


def _movement_fingerprint(
    cash_id: int,
    actor_id: int,
    movement_type: str,
    amount: Any,
    note: str,
) -> str:
    payload = json.dumps(
        {
            "caixa_id": cash_id,
            "usuario_id": actor_id,
            "tipo": movement_type,
            "valor": f"{amount:.2f}",
            "observacao": note,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _public_cash(
    row: dict,
    *,
    include_opening_amount: bool = False,
    include_closure_values: bool = False,
) -> dict:
    result = {
        "id": row["id"],
        "usuario_id": row["usuario_id"],
        "data_abertura": row["data_abertura"],
        "data_fechamento": row["data_fechamento"],
        "status": row["status"],
    }
    if include_opening_amount:
        result["fundo_inicial"] = round(float(row["fundo_inicial"]), 2)
    if include_closure_values:
        result.update(
            {
                "valor_informado": None if row["valor_informado"] is None else round(float(row["valor_informado"]), 2),
                "valor_esperado": None if row["valor_esperado"] is None else round(float(row["valor_esperado"]), 2),
                "quebra": None if row["quebra"] is None else round(float(row["quebra"]), 2),
                "justificativa": row["justificativa"] or "",
            }
        )
    return result


class CashService:
    def __init__(self, database: Database, *, audit: Optional[AuditService] = None, backup_service: Any = None):
        self.database = database
        self.audit = audit or AuditService(database)
        self.backup_service = backup_service
        self.backup_worker = (
            CashBackupWorker(database, backup_service, audit=self.audit)
            if backup_service is not None
            else None
        )

    def set_backup_service(self, backup_service: Any) -> None:
        if self.backup_worker is not None:
            self.backup_worker.shutdown()
        self.backup_service = backup_service
        self.backup_worker = CashBackupWorker(
            self.database, backup_service, audit=self.audit
        )

    def shutdown(self, timeout: float = 2.0) -> None:
        if self.backup_worker is not None:
            self.backup_worker.shutdown(timeout)

    @staticmethod
    def _expected_amount(connection: Any, cash_id: int) -> dict:
        cash = connection.execute("SELECT * FROM caixas WHERE id = ?", (cash_id,)).fetchone()
        if cash is None:
            raise NotFoundError("Caixa não encontrado.")
        movements = connection.execute(
            "SELECT "
            "COALESCE(SUM(CASE WHEN tipo = 'SUPRIMENTO' THEN valor ELSE 0 END), 0) AS suprimentos, "
            "COALESCE(SUM(CASE WHEN tipo = 'SANGRIA' THEN valor ELSE 0 END), 0) AS sangrias "
            "FROM movimentacoes_caixa WHERE caixa_id = ?",
            (cash_id,),
        ).fetchone()
        cash_sales = connection.execute(
            "SELECT COALESCE(SUM(total), 0) AS total FROM vendas "
            "WHERE caixa_id = ? AND status = 'CONFIRMADA' AND forma_pagamento = 'Dinheiro'",
            (cash_id,),
        ).fetchone()["total"]
        expected = money(
            Decimal(str(cash["fundo_inicial"]))
            + Decimal(str(movements["suprimentos"]))
            - Decimal(str(movements["sangrias"]))
            + Decimal(str(cash_sales)),
            "valor esperado",
        )
        return {
            "cash": dict(cash),
            "fundo_inicial": money(cash["fundo_inicial"], "fundo inicial"),
            "suprimentos": money(movements["suprimentos"], "suprimentos"),
            "sangrias": money(movements["sangrias"], "sangrias"),
            "vendas_dinheiro": money(cash_sales, "vendas em dinheiro"),
            "valor_esperado": expected,
        }

    def get_open_cash(self, usuario_id: int, *, actor_id: int) -> Optional[dict]:
        owner = user_id(usuario_id)
        with self.database.transaction() as connection:
            actor = get_active_user(connection, actor_id)
            if actor["perfil"] != "admin" and actor["id"] != owner:
                raise AuthorizationError("Você só pode consultar o seu próprio caixa.")
            row = connection.execute(
                "SELECT * FROM caixas WHERE usuario_id = ? AND status = 'ABERTO' ORDER BY id DESC LIMIT 1", (owner,)
            ).fetchone()
            return _public_cash(dict(row), include_opening_amount=actor["perfil"] == "admin") if row else None

    def get_global_open_cash(self, *, actor_id: int) -> Optional[dict]:
        """Informa se o único caixa físico já está ocupado, sem retomá-lo."""

        with self.database.transaction() as connection:
            actor = get_active_user(connection, actor_id)
            row = connection.execute(
                "SELECT * FROM caixas WHERE status = 'ABERTO' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            cash = dict(row)
            return _public_cash(
                cash,
                include_opening_amount=(
                    actor["perfil"] == "admin" or actor["id"] == cash["usuario_id"]
                ),
            )

    def resume_open_cash(self, caixa_id: int, *, actor_id: int, reason: str) -> dict:
        """Permite retomada administrativa explícita do caixa já aberto."""

        cash_id = _cash_id(caixa_id)
        justification = _resume_reason(reason)
        with self.database.transaction(write=True) as connection:
            actor = require_admin(connection, actor_id)
            row = connection.execute(
                "SELECT * FROM caixas WHERE id = ?", (cash_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("Caixa não encontrado.")
            cash = dict(row)
            if cash["status"] != "ABERTO":
                raise ConflictError("Este caixa não está mais aberto.")
            AuditService.record(
                connection,
                "CAIXA_RETOMADO_ADMIN",
                "CAIXA",
                entity_id=cash_id,
                actor_id=actor["id"],
                details={
                    "usuario_responsavel_id": cash["usuario_id"],
                    "justificativa": justification,
                },
            )
            return _public_cash(cash, include_opening_amount=True)

    def get_cash(self, caixa_id: int, *, actor_id: int, include_closure_values: bool = False) -> dict:
        cash_id = _cash_id(caixa_id)
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM caixas WHERE id = ?", (cash_id,)).fetchone()
            if row is None:
                raise NotFoundError("Caixa não encontrado.")
            cash = dict(row)
            actor = can_access_cash(connection, cash, actor_id)
            if include_closure_values and actor["perfil"] != "admin":
                # O caixa nunca vê o esperado, inclusive após informar a contagem.
                include_closure_values = False
            return _public_cash(
                cash,
                include_opening_amount=actor["perfil"] == "admin",
                include_closure_values=include_closure_values,
            )

    def open_cash(self, usuario_id: int, fundo_inicial: Any, *, actor_id: int) -> dict:
        owner = user_id(usuario_id)
        opening = money(fundo_inicial, "fundo inicial")
        with self.database.transaction(write=True) as connection:
            actor = get_active_user(connection, actor_id)
            target = get_active_user(connection, owner)
            if actor["perfil"] != "admin" and actor["id"] != target["id"]:
                raise AuthorizationError("Você só pode abrir o seu próprio caixa.")
            existing = connection.execute(
                "SELECT id, usuario_id FROM caixas WHERE status = 'ABERTO' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if existing is not None:
                if int(existing["usuario_id"]) == owner:
                    raise ConflictError("Este usuário já possui um caixa aberto.")
                raise ConflictError(
                    "Já existe um caixa aberto por outro operador. Peça a um administrador para retomá-lo ou fechá-lo."
                )
            cursor = connection.execute(
                "INSERT INTO caixas(usuario_id, data_abertura, fundo_inicial, status) VALUES (?, ?, ?, 'ABERTO')",
                (owner, now(), as_float(opening)),
            )
            row = connection.execute("SELECT * FROM caixas WHERE id = ?", (cursor.lastrowid,)).fetchone()
            AuditService.record(
                connection,
                "CAIXA_ABERTO",
                "CAIXA",
                entity_id=cursor.lastrowid,
                actor_id=actor["id"],
                details={"usuario_id": owner, "fundo_inicial": as_float(opening)},
            )
            return _public_cash(dict(row), include_opening_amount=actor["perfil"] == "admin")

    def add_movement(
        self,
        caixa_id: int,
        tipo: str,
        valor: Any,
        observacao: str = "",
        *,
        actor_id: int,
        chave_idempotencia: str | None = None,
    ) -> dict:
        cash_id = _cash_id(caixa_id)
        normalized_type = MOVEMENT_TYPES.get(str(tipo or "").strip().upper())
        if normalized_type is None:
            raise ValidationError("Tipo de movimentação inválido. Use SANGRIA ou SUPRIMENTO.")
        amount = money(valor, "valor da movimentação", allow_zero=False)
        note = _note(observacao)
        idempotency_key = _idempotency_key(chave_idempotencia)
        with self.database.transaction(write=True) as connection:
            row = connection.execute("SELECT * FROM caixas WHERE id = ?", (cash_id,)).fetchone()
            if row is None:
                raise NotFoundError("Caixa não encontrado.")
            cash = dict(row)
            if cash["status"] != "ABERTO":
                raise ConflictError("Não é possível movimentar um caixa fechado.")
            actor = can_access_cash(connection, cash, actor_id)
            fingerprint = _movement_fingerprint(
                cash_id, int(actor["id"]), normalized_type, amount, note
            )
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM movimentacoes_caixa WHERE chave_idempotencia = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    stored = dict(existing)
                    if stored.get("fingerprint") != fingerprint:
                        raise ConflictError(
                            "Esta confirmação de movimentação já foi usada com dados diferentes."
                        )
                    stored["idempotent_replay"] = True
                    return stored
            cursor = connection.execute(
                "INSERT INTO movimentacoes_caixa(caixa_id, usuario_id, tipo, valor, observacao, "
                "data_movimentacao, chave_idempotencia, fingerprint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cash_id,
                    actor["id"],
                    normalized_type,
                    as_float(amount),
                    note,
                    now(),
                    idempotency_key,
                    fingerprint if idempotency_key else None,
                ),
            )
            row = connection.execute("SELECT * FROM movimentacoes_caixa WHERE id = ?", (cursor.lastrowid,)).fetchone()
            AuditService.record(
                connection,
                f"CAIXA_{normalized_type}",
                "MOVIMENTACAO_CAIXA",
                entity_id=cursor.lastrowid,
                actor_id=actor["id"],
                details={"caixa_id": cash_id, "valor": as_float(amount), "observacao": note},
            )
            return dict(row)

    def list_movements(self, caixa_id: int, *, actor_id: int) -> list[dict]:
        cash_id = _cash_id(caixa_id)
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM caixas WHERE id = ?", (cash_id,)).fetchone()
            if row is None:
                raise NotFoundError("Caixa não encontrado.")
            can_access_cash(connection, dict(row), actor_id)
            rows = connection.execute(
                "SELECT * FROM movimentacoes_caixa WHERE caixa_id = ? ORDER BY id DESC", (cash_id,)
            ).fetchall()
            return [dict(item) for item in rows]

    def close_cash(
        self,
        caixa_id: int,
        valor_informado: Any,
        justificativa: str = "",
        *,
        actor_id: int,
        reveal_expected: bool = False,
    ) -> dict:
        """Fecha sem vazar o esperado ao operador e dispara backup pós-commit."""

        cash_id = _cash_id(caixa_id)
        counted = money(valor_informado, "valor contado")
        result: dict
        with self.database.transaction(write=True) as connection:
            initial = connection.execute("SELECT * FROM caixas WHERE id = ?", (cash_id,)).fetchone()
            if initial is None:
                raise NotFoundError("Caixa não encontrado.")
            cash = dict(initial)
            if cash["status"] != "ABERTO":
                raise ConflictError("Este caixa já foi fechado.")
            actor = can_access_cash(connection, cash, actor_id)
            summary = self._expected_amount(connection, cash_id)
            expected = summary["valor_esperado"]
            difference = counted - expected
            note = _note(justificativa, "justificativa", required=difference != 0)
            closed_at = now()
            connection.execute(
                "UPDATE caixas SET data_fechamento = ?, valor_informado = ?, valor_esperado = ?, quebra = ?, "
                "justificativa = ?, status = 'FECHADO' WHERE id = ? AND status = 'ABERTO'",
                (closed_at, as_float(counted), as_float(expected), as_float(difference), note, cash_id),
            )
            closed = dict(connection.execute("SELECT * FROM caixas WHERE id = ?", (cash_id,)).fetchone())
            AuditService.record(
                connection,
                "CAIXA_FECHADO",
                "CAIXA",
                entity_id=cash_id,
                actor_id=actor["id"],
                details={
                    "valor_informado": as_float(counted),
                    "valor_esperado": as_float(expected),
                    "quebra": as_float(difference),
                    "justificativa": note,
                },
            )
            # O padrão evita vazamento no fechamento cego. A fachada só envia
            # ``reveal_expected`` quando a configuração administrativa permite.
            result = _public_cash(
                closed,
                include_opening_amount=actor["perfil"] == "admin",
                include_closure_values=bool(reveal_expected),
            )

            if self.backup_worker is not None:
                connection.execute(
                    "INSERT INTO backups_caixa(caixa_id, solicitado_por, status, tentativas, "
                    "ultimo_erro, arquivo, solicitado_em, atualizado_em) "
                    "VALUES (?, ?, 'PENDENTE', 0, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(caixa_id) DO UPDATE SET solicitado_por = excluded.solicitado_por, "
                    "status = 'PENDENTE', ultimo_erro = NULL, arquivo = NULL, "
                    "atualizado_em = CURRENT_TIMESTAMP",
                    (cash_id, actor["id"]),
                )
                result["backup_status"] = "PENDENTE"

        if self.backup_worker is not None:
            self.backup_worker.enqueue(cash_id)
        return result

    def retry_cash_backup(self, caixa_id: int, *, actor_id: int) -> dict:
        cash_id = _cash_id(caixa_id)
        if self.backup_worker is None:
            raise ConflictError("O backup automático não está configurado.")
        with self.database.transaction(write=True) as connection:
            cash_row = connection.execute(
                "SELECT * FROM caixas WHERE id = ?", (cash_id,)
            ).fetchone()
            if cash_row is None:
                raise NotFoundError("Caixa não encontrado.")
            actor = can_access_cash(connection, dict(cash_row), actor_id)
            job = connection.execute(
                "SELECT * FROM backups_caixa WHERE caixa_id = ?", (cash_id,)
            ).fetchone()
            if job is None:
                raise NotFoundError("Não há backup pendente para este caixa.")
            connection.execute(
                "UPDATE backups_caixa SET status = 'PENDENTE', ultimo_erro = NULL, "
                "atualizado_em = CURRENT_TIMESTAMP WHERE caixa_id = ?",
                (cash_id,),
            )
            AuditService.record(
                connection,
                "BACKUP_CAIXA_REENVIADO",
                "CAIXA",
                entity_id=cash_id,
                actor_id=actor["id"],
            )
            result = dict(
                connection.execute(
                    "SELECT * FROM backups_caixa WHERE caixa_id = ?", (cash_id,)
                ).fetchone()
            )
        self.backup_worker.enqueue(cash_id)
        return result

    def get_cash_summary(self, caixa_id: int, *, actor_id: int, reveal_expected: bool = False) -> dict:
        cash_id = _cash_id(caixa_id)
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM caixas WHERE id = ?", (cash_id,)).fetchone()
            if row is None:
                raise NotFoundError("Caixa não encontrado.")
            cash = dict(row)
            actor = can_access_cash(connection, cash, actor_id)
            summary = self._expected_amount(connection, cash_id)
            public = _public_cash(cash, include_opening_amount=actor["perfil"] == "admin", include_closure_values=False)
            if actor["perfil"] == "admin":
                public.update(
                    {
                        "suprimentos": as_float(summary["suprimentos"]),
                        "sangrias": as_float(summary["sangrias"]),
                        "vendas_dinheiro": as_float(summary["vendas_dinheiro"]),
                        "valor_em_caixa": as_float(summary["valor_esperado"]),
                    }
                )
                if reveal_expected:
                    public["valor_esperado"] = as_float(summary["valor_esperado"])
            elif reveal_expected:
                # A configuração administrativa pode autorizar a visualização
                # operacional também para o perfil caixa. O fechamento cego
                # continua ocultando o valor quando essa opção está desligada.
                public["valor_em_caixa"] = as_float(summary["valor_esperado"])
            return public

    def list_cash_history(self, *, actor_id: int, limit: int = 100) -> list[dict]:
        try:
            safe_limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Limite de histórico inválido.") from exc
        with self.database.transaction() as connection:
            require_admin(connection, actor_id)
            rows = connection.execute("SELECT * FROM caixas ORDER BY id DESC LIMIT ?", (safe_limit,)).fetchall()
            return [_public_cash(dict(row), include_opening_amount=True, include_closure_values=True) for row in rows]
