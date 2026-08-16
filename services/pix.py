"""Geração local de Pix estático BR Code e QR Code."""

from __future__ import annotations

import io
import re
import unicodedata
from typing import Any

from .errors import PaymentError, ValidationError
from .money import money


def _field(identifier: str, value: str) -> str:
    encoded_length = len(value.encode("utf-8"))
    if encoded_length > 99:
        raise ValidationError("Campo Pix excede o tamanho permitido.")
    return f"{identifier}{encoded_length:02d}{value}"


def _sanitize(value: Any, max_length: int, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9 ]", "", normalized).upper().strip()
    return (normalized[:max_length] or fallback)[:max_length]


def crc16_ccitt(payload: str) -> str:
    """CRC16-CCITT (poly 0x1021, init 0xFFFF) exigido pelo BR Code."""

    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def build_pix_payload(
    pix_key: str,
    merchant_name: str,
    merchant_city: str,
    amount: Any,
    *,
    description: str = "",
    txid: str = "***",
) -> str:
    """Monta um BR Code de cobrança estática com valor fixo."""

    key = str(pix_key or "").strip()
    if not key:
        raise PaymentError("Chave PIX não configurada. Configure-a antes de cobrar por PIX.")
    if len(key.encode("utf-8")) > 77:
        raise ValidationError("A chave PIX excede o tamanho permitido.")
    total = money(amount, "valor PIX", allow_zero=False)
    merchant_account = _field("00", "BR.GOV.BCB.PIX") + _field("01", key)
    clean_description = _sanitize(description, 40, "")
    if clean_description:
        merchant_account += _field("02", clean_description)
    clean_txid = re.sub(r"[^A-Za-z0-9]", "", str(txid or "***").upper())[:25] or "***"
    payload = (
        _field("00", "01")
        + _field("01", "12")
        + _field("26", merchant_account)
        + _field("52", "0000")
        + _field("53", "986")
        + _field("54", f"{total:.2f}")
        + _field("58", "BR")
        + _field("59", _sanitize(merchant_name, 25, "TRIGO DE MINAS"))
        + _field("60", _sanitize(merchant_city, 15, "SAO PAULO"))
        + _field("62", _field("05", clean_txid))
    )
    payload_with_crc_id = payload + "6304"
    return payload_with_crc_id + crc16_ccitt(payload_with_crc_id)


def generate_qr_bytes(payload: str) -> bytes:
    """Gera PNG de QR Code; mantém import opcional para o banco continuar local."""

    if not isinstance(payload, str) or not payload:
        raise ValidationError("Payload PIX inválido.")
    try:
        import qrcode
    except ImportError as exc:  # pragma: no cover - depende da instalação do operador
        raise PaymentError("Biblioteca de QR Code não instalada.") from exc
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=3)
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class PixService:
    """Adaptador para a configuração do estabelecimento."""

    def __init__(self, *, pix_key: str, receiver_name: str, city: str, description: str = ""):
        self.pix_key = pix_key
        self.receiver_name = receiver_name
        self.city = city
        self.description = description

    def create_charge(self, amount: Any, *, txid: str = "***") -> dict:
        total = money(amount, "valor PIX", allow_zero=False)
        payload = build_pix_payload(
            self.pix_key,
            self.receiver_name,
            self.city,
            total,
            description=self.description,
            txid=txid,
        )
        return {"payload": payload, "valor": float(total), "txid": txid}

    def qr_code(self, amount: Any, *, txid: str = "***") -> bytes:
        return generate_qr_bytes(self.create_charge(amount, txid=txid)["payload"])
