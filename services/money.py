"""Conversão de valores sem os erros de ponto flutuante comuns em PDV."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from .errors import ValidationError


CENTS = Decimal("0.01")
QUANTITY_PRECISION = Decimal("0.001")


def _decimal(value: Any, field: str) -> Decimal:
    """Converte tanto ``1.234,56`` quanto ``1234.56`` sem ambiguidade comum."""

    if isinstance(value, bool) or value is None:
        raise ValidationError(f"Informe um {field} válido.")
    raw = str(value).strip().replace("R$", "").replace(" ", "")
    if not raw:
        raise ValidationError(f"Informe um {field} válido.")
    if "," in raw and "." in raw:
        # O separador mais à direita representa a casa decimal; o outro agrupa milhares.
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return Decimal(raw)
    except (InvalidOperation, AttributeError) as exc:
        raise ValidationError(f"Informe um {field} válido.") from exc


def decimal_value(value: Any, field: str = "valor") -> Decimal:
    """Converte uma entrada decimal sem arredondá-la.

    Serviços que precisam validar a precisão informada (por exemplo, unidades
    inteiras ou peso com até três casas) usam esta função antes de quantizar.
    """

    return _decimal(value, field)


def _quantize(value: Decimal, precision: Decimal, field: str) -> Decimal:
    try:
        return value.quantize(precision, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValidationError(f"Informe um {field} válido.") from exc


def money(value: Any, field: str = "valor", *, allow_zero: bool = True) -> Decimal:
    parsed = _decimal(value, field)
    if not parsed.is_finite() or parsed < 0 or (not allow_zero and parsed == 0):
        comparison = "maior que zero" if not allow_zero else "não negativo"
        raise ValidationError(f"O {field} deve ser {comparison}.")
    rounded = _quantize(parsed, CENTS, field)
    if not allow_zero and rounded == 0:
        raise ValidationError(f"O {field} deve ser maior que zero.")
    return rounded


def quantity(value: Any, field: str = "quantidade") -> Decimal:
    parsed = _decimal(value, field)
    if not parsed.is_finite() or parsed <= 0:
        raise ValidationError(f"A {field} deve ser maior que zero.")
    rounded = _quantize(parsed, QUANTITY_PRECISION, field)
    if rounded == 0:
        raise ValidationError(f"A {field} deve ser maior que zero.")
    return rounded


def stock(value: Any, field: str = "estoque") -> Decimal:
    """Quantidade não negativa, em precisão de milésimo (adequada a KG)."""

    parsed = _decimal(value, field)
    if not parsed.is_finite() or parsed < 0:
        raise ValidationError(f"O {field} deve ser não negativo.")
    return _quantize(parsed, QUANTITY_PRECISION, field)


def signed_quantity(value: Any, field: str = "quantidade") -> Decimal:
    parsed = _decimal(value, field)
    if not parsed.is_finite() or parsed == 0:
        raise ValidationError(f"A {field} deve ser diferente de zero.")
    return _quantize(parsed, QUANTITY_PRECISION, field)


def as_float(value: Decimal | float | int) -> float:
    return float(value)
