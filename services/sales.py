"""Confirmação transacional de vendas e relatórios financeiros."""

from __future__ import annotations

import re
import sqlite3
import hashlib
import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from db.database import Database

from .audit import AuditService, now
from .auth import AuthService
from .checkout import LineKind, SaleQuote, quote_lines
from .errors import (
    AuthorizationError,
    AuthorizationRequirement,
    AuthorizationRequiredError,
    ConflictError,
    InsufficientStockError,
    NotFoundError,
    PaymentError,
    ValidationError,
)
from .money import as_float, money
from .security import can_access_cash, get_active_user, require_admin, user_id


PAYMENT_METHODS = {
    "DINHEIRO": "Dinheiro",
    "PIX": "PIX",
    "CARTAO": "Cartão",
    "CARTÃO": "Cartão",
    "CARD": "Cartão",
}
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.-]{8,128}$")
_CANCELLATION_AUDIT_FIELDS = (
    "gtin",
    "codigo",
    "nome",
    "nome_produto",
    "unidade",
    "quantidade",
    "preco_unitario",
    "preco",
    "subtotal",
)


def _safe_cancellation_item(item: Any) -> dict:
    """Mantém na auditoria somente dados públicos necessários do produto."""

    if not isinstance(item, dict):
        return {}
    safe: dict[str, Any] = {}
    for field in _CANCELLATION_AUDIT_FIELDS:
        value = item.get(field)
        if value is None:
            continue
        if isinstance(value, str):
            safe[field] = value[:180]
        elif isinstance(value, bool):
            safe[field] = value
        elif isinstance(value, (int, float)):
            safe[field] = value
        elif isinstance(value, Decimal):
            safe[field] = str(value)[:180]
    return safe


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


def _sale_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValidationError("Informe uma venda válida.")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Informe uma venda válida.") from exc
    if result <= 0:
        raise ValidationError("Informe uma venda válida.")
    return result


def _payment_method(value: Any) -> str:
    normalized = " ".join(str(value or "").split()).upper()
    normalized = normalized.replace(" DE ", " ").replace("CRÉDITO", "").replace("CREDITO", "").replace("DÉBITO", "").replace("DEBITO", "")
    result = PAYMENT_METHODS.get(normalized)
    if result is None:
        raise PaymentError("Forma de pagamento inválida. Use Dinheiro, PIX ou Cartão.")
    return result


def _sale_public(row: dict) -> dict:
    return {
        "id": row["id"],
        "caixa_id": row["caixa_id"],
        "operador_id": row["operador_id"],
        "total": round(float(row["total"]), 2),
        "forma_pagamento": row["forma_pagamento"],
        "valor_recebido": None if row["valor_recebido"] is None else round(float(row["valor_recebido"]), 2),
        "troco": round(float(row["troco"]), 2),
        "data_venda": row["data_venda"],
        "status": row["status"],
        "total_manual": round(float(row.get("total_manual", 0) or 0), 2),
        "autorizador_excecao_id": row.get("autorizador_excecao_id"),
        "motivo_excecao": row.get("motivo_excecao"),
    }


def _sale_items(connection: sqlite3.Connection, sale_id: int) -> list[dict]:
    rows = connection.execute("SELECT * FROM itens_venda WHERE venda_id = ? ORDER BY id", (sale_id,)).fetchall()
    return [
        {
            "id": row["id"],
            "gtin": row["gtin"],
            "nome_produto": row["nome_produto"],
            "unidade": row["unidade"],
            "quantidade": float(row["quantidade"]),
            "preco_unitario": round(float(row["preco_unitario"]), 2),
            "subtotal": round(float(row["subtotal"]), 2),
            "tipo_lancamento": row["tipo_lancamento"],
            "codigo_informado": row["codigo_informado"],
            "preco_original": (
                None if row["preco_original"] is None else round(float(row["preco_original"]), 2)
            ),
        }
        for row in rows
    ]


def _date_string(value: Any, field: str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise ValidationError(f"Informe a {field} no formato AAAA-MM-DD.")
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise ValidationError(f"Informe a {field} no formato AAAA-MM-DD.") from exc


class SaleService:
    def __init__(
        self,
        database: Database,
        *,
        audit: Optional[AuditService] = None,
        auth: Optional[AuthService] = None,
    ):
        self.database = database
        self.audit = audit or AuditService(database)
        self.auth = auth or AuthService(database, self.audit)

    @staticmethod
    def _safe_idempotency_key(value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        if not isinstance(value, str) or not IDEMPOTENCY_KEY.fullmatch(value):
            raise ValidationError("A chave de confirmação da venda é inválida.")
        return value

    @staticmethod
    def _with_items(connection: sqlite3.Connection, sale_row: dict) -> dict:
        result = _sale_public(sale_row)
        result["itens"] = _sale_items(connection, sale_row["id"])
        print_job = connection.execute(
            "SELECT id, status FROM impressao_outbox "
            "WHERE venda_id = ? AND tipo = 'ORIGINAL'",
            (sale_row["id"],),
        ).fetchone()
        if print_job is not None:
            result["print_job_id"] = int(print_job["id"])
            result["print_status"] = str(print_job["status"])
        return result

    @staticmethod
    def _receipt_payload(
        connection: sqlite3.Connection,
        sale_id: int,
        context: Mapping[str, Any],
        *,
        second_copy: bool,
    ) -> dict[str, Any]:
        sale_row = connection.execute(
            "SELECT * FROM vendas WHERE id = ?", (sale_id,)
        ).fetchone()
        if sale_row is None:
            raise NotFoundError("Venda não encontrada.")
        sale = SaleService._with_items(connection, dict(sale_row))
        operator = connection.execute(
            "SELECT nome, login FROM usuarios WHERE id = ?", (sale["operador_id"],)
        ).fetchone()
        return {
            "business_name": str(context.get("business_name") or "TRIGO DE MINAS"),
            "business_document": str(context.get("business_document") or ""),
            "address": str(context.get("address") or ""),
            "sale_id": sale["id"],
            "date": sale["data_venda"],
            "items": [
                {
                    "nome": item["nome_produto"],
                    "quantidade": item["quantidade"],
                    "preco_unitario": item["preco_unitario"],
                    "subtotal": item["subtotal"],
                }
                for item in sale["itens"]
            ],
            "total": sale["total"],
            "payment_method": sale["forma_pagamento"],
            "change": sale["troco"],
            "operator": (
                str(operator["nome"] or operator["login"]) if operator is not None else ""
            ),
            "copy_label": "SEGUNDA VIA" if second_copy else "",
        }

    @staticmethod
    def _queue_receipt_in_transaction(
        connection: sqlite3.Connection,
        *,
        sale_id: int,
        actor_id: int,
        kind: str,
        idempotency_key: str,
        context: Mapping[str, Any],
    ) -> dict:
        payload = SaleService._receipt_payload(
            connection, sale_id, context, second_copy=kind == "SEGUNDA_VIA"
        )
        serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "venda_id": sale_id,
                    "solicitado_por": actor_id,
                    "tipo": kind,
                    "payload": payload,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        existing = connection.execute(
            "SELECT * FROM impressao_outbox WHERE chave_idempotencia = ?",
            (idempotency_key,),
        ).fetchone()
        if existing is not None:
            stored = dict(existing)
            if stored["fingerprint"] != fingerprint:
                raise ConflictError(
                    "Esta solicitação de impressão já foi usada com dados diferentes."
                )
            stored["idempotent_replay"] = True
            return stored
        cursor = connection.execute(
            "INSERT INTO impressao_outbox(venda_id, tipo, solicitado_por, chave_idempotencia, "
            "fingerprint, status, payload) VALUES (?, ?, ?, ?, ?, 'PENDENTE', ?)",
            (sale_id, kind, actor_id, idempotency_key, fingerprint, serialized),
        )
        return dict(
            connection.execute(
                "SELECT * FROM impressao_outbox WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        )

    @staticmethod
    def _authorization_requirement(quote: SaleQuote) -> AuthorizationRequirement:
        return AuthorizationRequirement(
            reasons=quote.authorization_reasons,
            manual_total=f"{quote.manual_total:.2f}",
            price_exception_count=quote.price_exception_count,
        )

    @staticmethod
    def _exception_reason(value: Any) -> str:
        reason = " ".join(str(value or "").split())
        if not 8 <= len(reason) <= 250:
            raise ValidationError("A justificativa deve ter entre 8 e 250 caracteres.")
        return reason

    @staticmethod
    def _fingerprint(
        *,
        cash_id: int,
        operator_id: int,
        quote: SaleQuote,
        payment: str,
        received: Decimal,
        authorizer_id: int | None,
        reason: str | None,
    ) -> str:
        payload = {
            "caixa_id": cash_id,
            "operador_id": operator_id,
            "itens": quote.to_payload()["itens"],
            "total": f"{quote.total:.2f}",
            "total_manual": f"{quote.manual_total:.2f}",
            "forma_pagamento": payment,
            "valor_recebido": f"{received:.2f}",
            "autorizador_excecao_id": authorizer_id,
            "motivo_excecao": reason,
        }
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def quote(self, itens: list[dict], actor_id: int) -> SaleQuote:
        """Cota sem gravar, revalidando que o solicitante continua ativo."""

        actor = user_id(actor_id, "operador")
        with self.database.transaction() as connection:
            get_active_user(connection, actor)
            return quote_lines(connection, itens)

    def finalize(
        self,
        caixa_id: int,
        itens: list[dict],
        forma_pagamento: str,
        valor_recebido: Any = 0,
        *,
        operador_id: int,
        chave_idempotencia: Optional[str] = None,
        exception_authorizer_id: Optional[int] = None,
        exception_reason: str = "",
        receipt_context: Optional[Mapping[str, Any]] = None,
    ) -> dict:
        """Grava venda, itens, baixa de estoque e auditoria em uma única transação."""

        cash_id = _cash_id(caixa_id)
        payment = _payment_method(forma_pagamento)
        idempotency_key = self._safe_idempotency_key(chave_idempotencia)
        operator = user_id(operador_id, "operador")
        with self.database.transaction(write=True) as connection:
            cash_row = connection.execute("SELECT * FROM caixas WHERE id = ?", (cash_id,)).fetchone()
            if cash_row is None:
                raise NotFoundError("Caixa não encontrado.")
            cash = dict(cash_row)
            if cash["status"] != "ABERTO":
                raise ConflictError("Não é possível registrar venda em caixa fechado.")
            actor = get_active_user(connection, operator)
            if actor["perfil"] != "admin" and actor["id"] != cash["usuario_id"]:
                raise AuthorizationError("Você só pode registrar vendas no seu próprio caixa.")
            quote = quote_lines(connection, itens)
            authorizer: dict | None = None
            reason: str | None = None
            if quote.requires_authorization:
                if exception_authorizer_id is None:
                    if actor["perfil"] == "admin":
                        exception_authorizer_id = int(actor["id"])
                    else:
                        raise AuthorizationRequiredError(
                            self._authorization_requirement(quote)
                        )
                authorizer = require_admin(
                    connection,
                    user_id(exception_authorizer_id, "administrador autorizador"),
                )
                reason = self._exception_reason(exception_reason)
            elif exception_authorizer_id is not None or str(exception_reason or "").strip():
                raise ValidationError(
                    "Esta venda não possui exceção que precise de autorização."
                )

            total = quote.total
            if payment == "Dinheiro":
                received = money(valor_recebido, "valor recebido")
                if received < total:
                    raise PaymentError("O valor recebido deve ser igual ou maior que o total da venda.")
                change = money(received - total, "troco")
            else:
                received = total
                change = Decimal("0.00")

            fingerprint = self._fingerprint(
                cash_id=cash_id,
                operator_id=int(actor["id"]),
                quote=quote,
                payment=payment,
                received=received,
                authorizer_id=None if authorizer is None else int(authorizer["id"]),
                reason=reason,
            )
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM vendas WHERE chave_idempotencia = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    existing_sale = dict(existing)
                    if existing_sale.get("fingerprint") != fingerprint:
                        raise ConflictError(
                            "Esta confirmação já foi usada com dados diferentes."
                        )
                    replay = self._with_items(connection, existing_sale)
                    replay["idempotent_replay"] = True
                    return replay

            cursor = connection.execute(
                "INSERT INTO vendas(caixa_id, operador_id, total, forma_pagamento, valor_recebido, "
                "troco, data_venda, status, chave_idempotencia, total_manual, "
                "autorizador_excecao_id, motivo_excecao, fingerprint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'CONFIRMADA', ?, ?, ?, ?, ?)",
                (
                    cash_id,
                    actor["id"],
                    as_float(total),
                    payment,
                    as_float(received),
                    as_float(change),
                    now(),
                    idempotency_key,
                    as_float(quote.manual_total),
                    None if authorizer is None else authorizer["id"],
                    reason,
                    fingerprint,
                ),
            )
            sale_id = int(cursor.lastrowid)
            for line in quote.lines:
                product: dict | None = None
                if line.kind is LineKind.CATALOG:
                    row = connection.execute(
                        "SELECT * FROM produtos WHERE gtin = ? AND ativo = 1",
                        (line.gtin,),
                    ).fetchone()
                    if row is None:
                        raise NotFoundError(
                            f"Produto {line.gtin} não está disponível para venda."
                        )
                    product = dict(row)
                if product is not None and product["estoque_controlado"]:
                    changed = connection.execute(
                        "UPDATE produtos SET estoque = estoque - ?, atualizado_em = ? "
                        "WHERE gtin = ? AND ativo = 1 AND estoque_controlado = 1 AND estoque >= ?",
                        (
                            as_float(line.quantity),
                            now(),
                            product["gtin"],
                            as_float(line.quantity),
                        ),
                    ).rowcount
                    if changed != 1:
                        # A condição é a última defesa contra corrida entre dois caixas.
                        raise InsufficientStockError(f"Estoque insuficiente para '{product['nome']}'.")
                connection.execute(
                    "INSERT INTO itens_venda(venda_id, gtin, nome_produto, unidade, quantidade, "
                    "preco_unitario, subtotal, tipo_lancamento, codigo_informado, preco_original) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        sale_id,
                        line.gtin,
                        line.name,
                        line.unit,
                        as_float(line.quantity),
                        as_float(line.unit_price),
                        as_float(line.subtotal),
                        line.kind.value,
                        line.entered_code,
                        None if line.original_price is None else as_float(line.original_price),
                    ),
                )
                if line.kind is LineKind.MANUAL:
                    AuditService.record(
                        connection,
                        "ITEM_MANUAL_VENDIDO",
                        "VENDA",
                        entity_id=sale_id,
                        actor_id=actor["id"],
                        details={
                            "tipo_lancamento": line.kind.value,
                            "codigo_informado": line.entered_code,
                            "nome_produto": line.name,
                            "unidade": line.unit,
                            "quantidade": str(line.quantity),
                            "preco_unitario": f"{line.unit_price:.2f}",
                            "subtotal": f"{line.subtotal:.2f}",
                        },
                    )
                if line.has_price_exception:
                    AuditService.record(
                        connection,
                        "PRECO_EXCEPCIONAL_APLICADO",
                        "VENDA",
                        entity_id=sale_id,
                        actor_id=actor["id"],
                        details={
                            "gtin": line.gtin,
                            "nome_produto": line.name,
                            "preco_original": f"{line.original_price:.2f}",
                            "preco_novo": f"{line.unit_price:.2f}",
                            "autorizado_por": authorizer["id"] if authorizer else None,
                            "motivo": reason,
                        },
                    )
            if authorizer is not None:
                AuditService.record(
                    connection,
                    "EXCECAO_VENDA_AUTORIZADA",
                    "VENDA",
                    entity_id=sale_id,
                    actor_id=actor["id"],
                    details={
                        "motivos": list(quote.authorization_reasons),
                        "total_manual": f"{quote.manual_total:.2f}",
                        "autorizado_por": authorizer["id"],
                        "justificativa": reason,
                    },
                )
            AuditService.record(
                connection,
                "VENDA_CONFIRMADA",
                "VENDA",
                entity_id=sale_id,
                actor_id=actor["id"],
                details={
                    "caixa_id": cash_id,
                    "total": as_float(total),
                    "forma_pagamento": payment,
                    "valor_recebido": as_float(received),
                    "troco": as_float(change),
                    "quantidade_itens": len(quote.lines),
                    "total_manual": as_float(quote.manual_total),
                    "autorizador_excecao_id": None if authorizer is None else authorizer["id"],
                },
            )
            if receipt_context is not None:
                if not isinstance(receipt_context, Mapping):
                    raise ValidationError("A configuração do comprovante é inválida.")
                self._queue_receipt_in_transaction(
                    connection,
                    sale_id=sale_id,
                    actor_id=int(actor["id"]),
                    kind="ORIGINAL",
                    idempotency_key=f"ORIGINAL-{sale_id}",
                    context=receipt_context,
                )
            sale_row = dict(connection.execute("SELECT * FROM vendas WHERE id = ?", (sale_id,)).fetchone())
            return self._with_items(connection, sale_row)

    def queue_receipt_copy(
        self,
        venda_id: int,
        *,
        actor_id: int,
        idempotency_key: str,
        receipt_context: Mapping[str, Any],
    ) -> dict:
        sale_id = _sale_id(venda_id)
        actor = user_id(actor_id, "operador")
        key = self._safe_idempotency_key(idempotency_key)
        if key is None:
            raise ValidationError("Informe a chave da segunda via.")
        if not isinstance(receipt_context, Mapping):
            raise ValidationError("A configuração do comprovante é inválida.")
        with self.database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT v.id, c.usuario_id AS caixa_usuario_id FROM vendas v "
                "JOIN caixas c ON c.id = v.caixa_id WHERE v.id = ?",
                (sale_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError("Venda não encontrada.")
            get_active_user(connection, actor)
            can_access_cash(
                connection, {"usuario_id": row["caixa_usuario_id"]}, actor
            )
            job = self._queue_receipt_in_transaction(
                connection,
                sale_id=sale_id,
                actor_id=actor,
                kind="SEGUNDA_VIA",
                idempotency_key=key,
                context=receipt_context,
            )
            if not job.get("idempotent_replay"):
                AuditService.record(
                    connection,
                    "SEGUNDA_VIA_SOLICITADA",
                    "VENDA",
                    entity_id=sale_id,
                    actor_id=actor,
                    details={"impressao_outbox_id": job["id"]},
                )
            return job

    def get_sale(self, venda_id: int, *, actor_id: int) -> dict:
        sale_id = _sale_id(venda_id)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT v.*, c.usuario_id AS caixa_usuario_id FROM vendas v JOIN caixas c ON c.id = v.caixa_id WHERE v.id = ?",
                (sale_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError("Venda não encontrada.")
            sale_with_owner = dict(row)
            cash = {"usuario_id": sale_with_owner["caixa_usuario_id"]}
            can_access_cash(connection, cash, actor_id)
            sale_with_owner.pop("caixa_usuario_id", None)
            return self._with_items(connection, sale_with_owner)

    def cancel_sale(
        self,
        venda_id: int,
        *,
        operator_id: int,
        authorizer_id: int,
        reason: str,
        idempotency_key: str,
    ) -> dict:
        """Cancela localmente e recompõe estoque uma vez, sem alegar estorno financeiro."""

        sale_id = _sale_id(venda_id)
        operator = user_id(operator_id, "operador")
        authorizer = user_id(authorizer_id, "administrador autorizador")
        key = self._safe_idempotency_key(idempotency_key)
        if key is None:
            raise ValidationError("Informe a chave de confirmação do cancelamento.")
        safe_reason = self._exception_reason(reason)
        fingerprint_payload = json.dumps(
            {
                "venda_id": sale_id,
                "operador_id": operator,
                "autorizador_id": authorizer,
                "motivo": safe_reason,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = hashlib.sha256(fingerprint_payload).hexdigest()

        with self.database.transaction(write=True) as connection:
            sale_row = connection.execute(
                "SELECT v.*, c.usuario_id AS caixa_usuario_id, c.status AS caixa_status "
                "FROM vendas v JOIN caixas c ON c.id = v.caixa_id WHERE v.id = ?",
                (sale_id,),
            ).fetchone()
            if sale_row is None:
                raise NotFoundError("Venda não encontrada.")
            sale = dict(sale_row)
            get_active_user(connection, operator)
            can_access_cash(
                connection, {"usuario_id": sale["caixa_usuario_id"]}, operator
            )
            require_admin(connection, authorizer)

            existing = connection.execute(
                "SELECT * FROM cancelamentos_venda WHERE chave_idempotencia = ?",
                (key,),
            ).fetchone()
            if existing is not None:
                cancellation = dict(existing)
                if cancellation["fingerprint"] != fingerprint:
                    raise ConflictError(
                        "Esta confirmação de cancelamento já foi usada com dados diferentes."
                    )
                cancellation["sale_status"] = sale["status"]
                cancellation["idempotent_replay"] = True
                cancellation["financial_warning"] = (
                    "O cancelamento é local e não realiza estorno automático de PIX ou cartão."
                )
                return cancellation

            previous = connection.execute(
                "SELECT * FROM cancelamentos_venda WHERE venda_id = ?", (sale_id,)
            ).fetchone()
            if previous is not None:
                raise ConflictError("Esta venda já foi cancelada.")
            if sale["status"] != "CONFIRMADA":
                raise ConflictError("Somente uma venda confirmada pode ser cancelada.")
            if sale["caixa_status"] != "ABERTO":
                raise ConflictError(
                    "A venda só pode ser cancelada enquanto o caixa de origem estiver aberto."
                )

            restored = connection.execute(
                "SELECT gtin, SUM(quantidade) AS quantidade FROM itens_venda "
                "WHERE venda_id = ? AND tipo_lancamento = 'CATALOGO' AND gtin IS NOT NULL "
                "GROUP BY gtin",
                (sale_id,),
            ).fetchall()
            changed = connection.execute(
                "UPDATE vendas SET status = 'CANCELADA' WHERE id = ? AND status = 'CONFIRMADA'",
                (sale_id,),
            ).rowcount
            if changed != 1:
                raise ConflictError("A venda já foi alterada e não pôde ser cancelada.")
            for item in restored:
                connection.execute(
                    "UPDATE produtos SET estoque = estoque + ?, atualizado_em = ? "
                    "WHERE gtin = ? AND estoque_controlado = 1",
                    (item["quantidade"], now(), item["gtin"]),
                )
            cursor = connection.execute(
                "INSERT INTO cancelamentos_venda(venda_id, operador_id, autorizador_id, motivo, "
                "chave_idempotencia, fingerprint, data_cancelamento) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sale_id, operator, authorizer, safe_reason, key, fingerprint, now()),
            )
            AuditService.record(
                connection,
                "VENDA_CANCELADA",
                "VENDA",
                entity_id=sale_id,
                actor_id=operator,
                details={
                    "autorizado_por": authorizer,
                    "motivo": safe_reason,
                    "forma_pagamento": sale["forma_pagamento"],
                    "estoque_reposto": [
                        {"gtin": row["gtin"], "quantidade": row["quantidade"]}
                        for row in restored
                    ],
                    "estorno_financeiro_automatico": False,
                },
            )
            cancellation = dict(
                connection.execute(
                    "SELECT * FROM cancelamentos_venda WHERE id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
            )
            cancellation["sale_status"] = "CANCELADA"
            cancellation["financial_warning"] = (
                "O cancelamento é local e não realiza estorno automático de PIX ou cartão."
            )
            return cancellation

    def authorize_item_cancellation(
        self,
        item: Any,
        *,
        operador_id: int,
        admin_login: Optional[str] = None,
        admin_senha: Optional[str] = None,
        admin_user_id: Optional[int] = None,
    ) -> dict:
        """Autoriza e audita remoção de item ainda mantido no carrinho pela UI."""

        operator = user_id(operador_id, "operador")
        safe_item = _safe_cancellation_item(item)
        rejected_credentials = False
        result: Optional[dict] = None
        with self.database.transaction(write=True) as connection:
            operator_user = get_active_user(connection, operator)
            if admin_user_id is not None:
                try:
                    supplied_admin_id = user_id(admin_user_id, "administrador")
                except ValidationError:
                    supplied_admin_id = None
                if (
                    supplied_admin_id != operator
                    or operator_user["perfil"] != "admin"
                ):
                    AuditService.record(
                        connection,
                        "CANCELAMENTO_ITEM_REJEITADO",
                        "CARRINHO",
                        actor_id=operator,
                        details=safe_item,
                    )
                    rejected_credentials = True
                else:
                    admin = require_admin(connection, operator)
            else:
                login = str(admin_login or "").strip()
                admin = self.auth.verify_admin_credentials_in_transaction(
                    connection,
                    login,
                    admin_senha,
                    requested_by=operator,
                    clock=self.auth.clock,
                    policy=self.auth.login_policy,
                )
                if admin is None:
                    AuditService.record(
                        connection,
                        "CANCELAMENTO_ITEM_REJEITADO",
                        "CARRINHO",
                        actor_id=operator,
                        details=safe_item,
                    )
                    rejected_credentials = True
            if not rejected_credentials:
                AuditService.record(
                    connection,
                    "ITEM_CARRINHO_CANCELADO",
                    "CARRINHO",
                    entity_id=safe_item.get("gtin") or safe_item.get("codigo"),
                    actor_id=operator,
                    details={"item": safe_item, "autorizado_por": admin["id"]},
                )
                result = {"authorized": True, "admin_id": admin["id"]}
        if rejected_credentials:
            raise AuthorizationError("É necessária uma senha de administrador para cancelar o item.")
        assert result is not None
        return result

    # Alias mais explícito para controladores que usam essa nomenclatura.
    cancel_pending_item = authorize_item_cancellation

    def sales_report(self, data_inicial: Any, data_final: Any, *, actor_id: int) -> list[dict]:
        start = _date_string(data_inicial, "data inicial")
        end = _date_string(data_final, "data final")
        if start > end:
            raise ValidationError("A data inicial não pode ser maior que a data final.")
        with self.database.transaction() as connection:
            require_admin(connection, actor_id)
            rows = connection.execute(
                "SELECT v.*, u.nome AS operador_nome FROM vendas v LEFT JOIN usuarios u ON u.id = v.operador_id "
                "WHERE substr(v.data_venda, 1, 10) BETWEEN ? AND ? ORDER BY v.id DESC",
                (start, end),
            ).fetchall()
            result = []
            for row in rows:
                item = _sale_public(dict(row))
                item["operador_nome"] = row["operador_nome"] or ""
                item["itens"] = _sale_items(connection, row["id"])
                result.append(item)
            return result

    def payment_totals_by_period(self, data_inicial: Any, data_final: Any, *, actor_id: int) -> list[dict]:
        start = _date_string(data_inicial, "data inicial")
        end = _date_string(data_final, "data final")
        if start > end:
            raise ValidationError("A data inicial não pode ser maior que a data final.")
        with self.database.transaction() as connection:
            require_admin(connection, actor_id)
            rows = connection.execute(
                "SELECT forma_pagamento, COUNT(*) AS quantidade, COALESCE(SUM(total), 0) AS total "
                "FROM vendas WHERE status = 'CONFIRMADA' AND substr(data_venda, 1, 10) BETWEEN ? AND ? "
                "GROUP BY forma_pagamento ORDER BY forma_pagamento",
                (start, end),
            ).fetchall()
            return [
                {"forma_pagamento": row["forma_pagamento"], "quantidade": row["quantidade"], "total": round(float(row["total"]), 2)}
                for row in rows
            ]

    def top_products_by_period(self, data_inicial: Any, data_final: Any, *, actor_id: int, limit: int = 15) -> list[dict]:
        start = _date_string(data_inicial, "data inicial")
        end = _date_string(data_final, "data final")
        if start > end:
            raise ValidationError("A data inicial não pode ser maior que a data final.")
        try:
            safe_limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Limite de relatório inválido.") from exc
        with self.database.transaction() as connection:
            require_admin(connection, actor_id)
            rows = connection.execute(
                "SELECT i.gtin, i.nome_produto, i.unidade, SUM(i.quantidade) AS quantidade, SUM(i.subtotal) AS total "
                "FROM itens_venda i JOIN vendas v ON v.id = i.venda_id "
                "WHERE v.status = 'CONFIRMADA' AND substr(v.data_venda, 1, 10) BETWEEN ? AND ? "
                "GROUP BY i.gtin, i.nome_produto, i.unidade ORDER BY quantidade DESC, total DESC LIMIT ?",
                (start, end, safe_limit),
            ).fetchall()
            return [
                {
                    "gtin": row["gtin"],
                    "nome_produto": row["nome_produto"],
                    "unidade": row["unidade"],
                    "quantidade": float(row["quantidade"]),
                    "total": round(float(row["total"]), 2),
                }
                for row in rows
            ]

    def dashboard_summary(self, *, actor_id: int) -> dict:
        today = date.today().isoformat()
        with self.database.transaction() as connection:
            require_admin(connection, actor_id)
            totals = connection.execute(
                "SELECT COUNT(*) AS quantidade, COALESCE(SUM(total), 0) AS total "
                "FROM vendas WHERE status = 'CONFIRMADA' AND substr(data_venda, 1, 10) = ?", (today,)
            ).fetchone()
            payments = connection.execute(
                "SELECT forma_pagamento, COALESCE(SUM(total), 0) AS total FROM vendas "
                "WHERE status = 'CONFIRMADA' AND substr(data_venda, 1, 10) = ? GROUP BY forma_pagamento", (today,)
            ).fetchall()
            open_cash_count = connection.execute("SELECT COUNT(*) AS total FROM caixas WHERE status = 'ABERTO'").fetchone()["total"]
            low_stock = connection.execute(
                "SELECT COUNT(*) AS total FROM produtos WHERE ativo = 1 AND estoque_controlado = 1 AND estoque <= 0"
            ).fetchone()["total"]
            by_payment = {row["forma_pagamento"]: round(float(row["total"]), 2) for row in payments}
            result = {
                "data": today,
                "quantidade_vendas": totals["quantidade"],
                "total_vendido": round(float(totals["total"]), 2),
                "caixas_abertos": open_cash_count,
                "estoque_baixo": low_stock,
                "por_forma_pagamento": by_payment,
            }
            # Aliases usados pela camada visual, preservando nomes administrativos.
            result["vendas_hoje"] = result["quantidade_vendas"]
            result["sales_today"] = result["quantidade_vendas"]
            result["faturamento_hoje"] = result["total_vendido"]
            result["revenue_today"] = result["total_vendido"]
            return result
