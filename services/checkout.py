"""Cotação decimal autoritativa do checkout.

Este módulo não grava venda nem altera estoque. Ele transforma o payload da
interface em linhas imutáveis, confere produtos locais e calcula cada subtotal
com ``ROUND_HALF_UP`` antes de formar os totais da venda.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from .errors import NotFoundError, ValidationError
from .money import decimal_value


MANUAL_TOTAL_WITHOUT_AUTHORIZATION = Decimal("50.00")
PRICE_EXCEPTION_REASON = "PRECO_EXCEPCIONAL"
MANUAL_LIMIT_REASON = "TOTAL_MANUAL_ACIMA_LIMITE"
MAX_NUMERIC_TEXT_LENGTH = 512
MAX_NUMERIC_DIGITS = 256
MAX_NUMERIC_EXPONENT = 256


class LineKind(str, Enum):
    """Origem da linha da venda, igual ao valor persistido no schema 9."""

    CATALOG = "CATALOGO"
    MANUAL = "MANUAL"


def _decimal_from_scaled_integer(value: int, scale: int) -> Decimal:
    magnitude = abs(value)
    digits = tuple(int(character) for character in str(magnitude)) if magnitude else (0,)
    return Decimal((1 if value < 0 else 0, digits, -scale))


def _scaled_integer(value: Decimal, scale: int, *, round_half_up: bool) -> int:
    """Converte ``Decimal`` em inteiro escalado sem usar o contexto global."""

    decimal_tuple = value.as_tuple()
    coefficient = 0
    for digit in decimal_tuple.digits:
        coefficient = coefficient * 10 + digit
    shift = int(decimal_tuple.exponent) + scale
    if shift >= 0:
        magnitude = coefficient * (10**shift)
    else:
        divisor = 10 ** (-shift)
        magnitude, remainder = divmod(coefficient, divisor)
        if remainder:
            if not round_half_up:
                raise ValidationError("O valor decimal interno perdeu precisão.")
            if remainder * 2 >= divisor:
                magnitude += 1
    return -magnitude if decimal_tuple.sign else magnitude


def _format_scaled_integer(value: int, scale: int) -> str:
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if scale == 0:
        return f"{sign}{magnitude}"
    factor = 10**scale
    whole, fraction = divmod(magnitude, factor)
    return f"{sign}{whole}.{fraction:0{scale}d}"


def _money_text(value: Decimal) -> str:
    return _format_scaled_integer(
        _scaled_integer(value, 2, round_half_up=False),
        2,
    )


def _quantity_text(value: Decimal, unit: str) -> str:
    millis = _scaled_integer(value, 3, round_half_up=False)
    if unit == "UN":
        return _format_scaled_integer(millis // 1000, 0)
    return _format_scaled_integer(millis, 3)


@dataclass(frozen=True, slots=True)
class CheckoutLine:
    """Linha validada, sem referência mutável ao payload recebido da UI."""

    kind: LineKind
    gtin: str | None
    entered_code: str | None
    name: str
    unit: str
    quantity: Decimal
    unit_price: Decimal
    original_price: Decimal | None
    subtotal: Decimal

    @property
    def has_price_exception(self) -> bool:
        return (
            self.kind is LineKind.CATALOG
            and self.original_price is not None
            and self.unit_price != self.original_price
        )

    def to_payload(self) -> dict[str, Any]:
        """Produz o contrato persistível sem ``float`` na fronteira da UI."""

        return {
            "tipo_lancamento": self.kind.value,
            "gtin": self.gtin,
            "codigo_informado": self.entered_code,
            "nome_produto": self.name,
            "unidade": self.unit,
            "quantidade": _quantity_text(self.quantity, self.unit),
            "preco_unitario": _money_text(self.unit_price),
            "preco_original": (
                None if self.original_price is None else _money_text(self.original_price)
            ),
            "subtotal": _money_text(self.subtotal),
        }


@dataclass(frozen=True, slots=True)
class SaleQuote:
    """Resultado completo e imutável de uma cotação local."""

    lines: tuple[CheckoutLine, ...]
    total: Decimal
    manual_total: Decimal
    authorization_reasons: tuple[str, ...] = ()

    @property
    def manual_line_count(self) -> int:
        return sum(line.kind is LineKind.MANUAL for line in self.lines)

    @property
    def price_exception_count(self) -> int:
        return sum(line.has_price_exception for line in self.lines)

    @property
    def requires_authorization(self) -> bool:
        return bool(self.authorization_reasons)

    def to_payload(self) -> dict[str, Any]:
        return {
            "itens": [line.to_payload() for line in self.lines],
            "total": _money_text(self.total),
            "total_manual": _money_text(self.manual_total),
            "requer_autorizacao": self.requires_authorization,
            "motivos_autorizacao": list(self.authorization_reasons),
        }


def _line_kind(item: Mapping[str, Any]) -> LineKind:
    raw_kind = item.get("tipo_lancamento", item.get("kind"))
    if isinstance(raw_kind, LineKind):
        return raw_kind
    if raw_kind is None or not str(raw_kind).strip():
        code = item.get("gtin", item.get("codigo"))
        return LineKind.CATALOG if code is not None and str(code).strip() else LineKind.MANUAL
    normalized = str(raw_kind).strip().upper()
    aliases = {
        "CATALOG": LineKind.CATALOG,
        "CATALOGO": LineKind.CATALOG,
        "CATÁLOGO": LineKind.CATALOG,
        "MANUAL": LineKind.MANUAL,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValidationError("O tipo de lançamento do item é inválido.") from exc


def _text(value: Any, field: str, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise ValidationError(f"Informe {field}.")
        return None
    normalized = " ".join(str(value).split())
    if not normalized:
        if required:
            raise ValidationError(f"Informe {field}.")
        return None
    return normalized


def _unit(value: Any) -> str:
    normalized = str(value or "UN").strip().upper()
    if normalized not in {"UN", "KG"}:
        raise ValidationError("A unidade deve ser UN ou KG.")
    return normalized


def _bounded_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, str) and len(value.strip()) > MAX_NUMERIC_TEXT_LENGTH:
        raise ValidationError(f"O {field} informado é muito extenso para processamento seguro.")
    parsed = decimal_value(value, field)
    if not parsed.is_finite():
        raise ValidationError(f"Informe um {field} válido.")
    decimal_tuple = parsed.as_tuple()
    if (
        len(decimal_tuple.digits) > MAX_NUMERIC_DIGITS
        or abs(int(decimal_tuple.exponent)) > MAX_NUMERIC_EXPONENT
    ):
        raise ValidationError(f"O {field} informado é muito extenso para processamento seguro.")
    return parsed


def _positive_money(value: Any, field: str) -> Decimal:
    parsed = _bounded_decimal(value, field)
    if parsed <= 0:
        raise ValidationError(f"O {field} deve ser maior que zero.")
    cents = _scaled_integer(parsed, 2, round_half_up=True)
    if cents <= 0:
        raise ValidationError(f"O {field} deve ser maior que zero.")
    return _decimal_from_scaled_integer(cents, 2)


def _quantity(value: Any, unit: str) -> Decimal:
    parsed = _bounded_decimal(value, "quantidade")
    if parsed <= 0:
        raise ValidationError("A quantidade deve ser maior que zero.")
    millis = _scaled_integer(parsed, 3, round_half_up=True)
    if millis <= 0:
        raise ValidationError("A quantidade deve ser maior que zero.")
    rounded = _decimal_from_scaled_integer(millis, 3)
    if parsed != rounded:
        raise ValidationError("A quantidade aceita no máximo três casas decimais.")
    if unit == "UN" and millis % 1000:
        raise ValidationError("A quantidade em UN deve ser um número inteiro.")
    return rounded


def _line_subtotal(unit_price: Decimal, amount: Decimal) -> Decimal:
    price_cents = _scaled_integer(unit_price, 2, round_half_up=False)
    quantity_millis = _scaled_integer(amount, 3, round_half_up=False)
    numerator = price_cents * quantity_millis
    subtotal_cents, remainder = divmod(numerator, 1000)
    if remainder * 2 >= 1000:
        subtotal_cents += 1
    if subtotal_cents <= 0:
        raise ValidationError("O subtotal do item deve ser maior que zero.")
    return _decimal_from_scaled_integer(subtotal_cents, 2)


def _row_dict(cursor: sqlite3.Cursor, row: Any) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if isinstance(row, Mapping):
        return dict(row)
    names = tuple(column[0] for column in (cursor.description or ()))
    return dict(zip(names, row, strict=True))


def _catalog_line(
    connection: sqlite3.Connection,
    item: Mapping[str, Any],
) -> CheckoutLine:
    code = _text(item.get("gtin", item.get("codigo")), "o código do produto", required=True)
    assert code is not None
    cursor = connection.execute(
        "SELECT gtin, nome, preco, unidade, ativo FROM produtos WHERE gtin = ?",
        (code,),
    )
    row = cursor.fetchone()
    if row is None:
        raise NotFoundError(f"Produto {code} não está disponível para venda.")
    product = _row_dict(cursor, row)
    if not bool(product["ativo"]):
        raise NotFoundError(f"Produto {code} não está disponível para venda.")

    unit = _unit(product["unidade"])
    amount = _quantity(item.get("quantidade", item.get("quantity")), unit)
    original_price = _positive_money(product["preco"], "preço do catálogo")
    raw_price = item.get("preco_unitario", item.get("preco"))
    applied_price = (
        original_price
        if raw_price is None
        else _positive_money(raw_price, "preço unitário")
    )
    subtotal = _line_subtotal(applied_price, amount)
    entered_code = _text(item.get("codigo_informado"), "o código informado", required=False)
    return CheckoutLine(
        kind=LineKind.CATALOG,
        gtin=str(product["gtin"]),
        entered_code=entered_code or code,
        name=str(product["nome"]),
        unit=unit,
        quantity=amount,
        unit_price=applied_price,
        original_price=original_price,
        subtotal=subtotal,
    )


def _manual_line(item: Mapping[str, Any]) -> CheckoutLine:
    name = _text(
        item.get("descricao", item.get("nome_produto", item.get("nome"))),
        "a descrição do item avulso",
        required=True,
    )
    assert name is not None
    unit = _unit(item.get("unidade"))
    amount = _quantity(item.get("quantidade", item.get("quantity")), unit)
    raw_price = item.get("preco_unitario", item.get("preco"))
    applied_price = _positive_money(raw_price, "preço unitário")
    subtotal = _line_subtotal(applied_price, amount)
    entered_code = _text(
        item.get("codigo_informado", item.get("codigo", item.get("gtin"))),
        "o código informado",
        required=False,
    )
    return CheckoutLine(
        kind=LineKind.MANUAL,
        gtin=None,
        entered_code=entered_code,
        name=name,
        unit=unit,
        quantity=amount,
        unit_price=applied_price,
        original_price=None,
        subtotal=subtotal,
    )


def quote_lines(connection: sqlite3.Connection, items: Any) -> SaleQuote:
    """Valida e cota linhas usando o catálogo visível na conexão recebida."""

    if (
        not isinstance(items, Sequence)
        or isinstance(items, (str, bytes, bytearray))
        or not items
    ):
        raise ValidationError("Inclua pelo menos um item na venda.")

    lines: list[CheckoutLine] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValidationError("Cada item da venda deve conter seus dados completos.")
        kind = _line_kind(item)
        lines.append(
            _catalog_line(connection, item)
            if kind is LineKind.CATALOG
            else _manual_line(item)
        )

    immutable_lines = tuple(lines)
    total_cents = sum(
        _scaled_integer(line.subtotal, 2, round_half_up=False)
        for line in immutable_lines
    )
    manual_cents = sum(
        _scaled_integer(line.subtotal, 2, round_half_up=False)
        for line in immutable_lines
        if line.kind is LineKind.MANUAL
    )
    total = _decimal_from_scaled_integer(total_cents, 2)
    manual_total = _decimal_from_scaled_integer(manual_cents, 2)
    reasons: list[str] = []
    if any(line.has_price_exception for line in immutable_lines):
        reasons.append(PRICE_EXCEPTION_REASON)
    if manual_total > MANUAL_TOTAL_WITHOUT_AUTHORIZATION:
        reasons.append(MANUAL_LIMIT_REASON)
    return SaleQuote(
        lines=immutable_lines,
        total=total,
        manual_total=manual_total,
        authorization_reasons=tuple(reasons),
    )
