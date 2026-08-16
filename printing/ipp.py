"""Minimal IPP Print-Job client for driverless/virtual printers.

The normal production path remains the Windows spooler.  This adapter is
useful for a printer installed through an IPP URL (and for the Android virtual
printer used in integration tests) without adding a second UI or dependency.
"""

from __future__ import annotations

import struct
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests


MAX_IPP_RESPONSE_BYTES = 64 * 1024
_RESPONSE_CHUNK_BYTES = 8 * 1024
_IPP_VERSION = (1, 1)
_IPP_REQUEST_ID = 1
_IPP_GROUP_TAGS = {0x01, 0x02, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A}
_IPP_INTEGER_TAGS = {0x21, 0x23}
_IPP_RESERVED_VALUE_TAGS = {0x25, 0x27}
_PLAINTEXT_WARNING = (
    "Aviso: conexão IPP não criptografada. "
    "Prefira HTTPS/IPPS quando a impressora oferecer suporte."
)


class IPPError(RuntimeError):
    """A transport or IPP protocol error."""


def _attribute(tag: int, name: str, value: str) -> bytes:
    name_bytes = name.encode("utf-8")
    value_bytes = value.encode("utf-8")
    return bytes([tag]) + struct.pack(">H", len(name_bytes)) + name_bytes + struct.pack(">H", len(value_bytes)) + value_bytes


def _http_uri(uri: str) -> str:
    parsed = urlparse(uri.strip())
    if parsed.scheme == "ipp":
        parsed = parsed._replace(scheme="http")
    elif parsed.scheme == "ipps":
        parsed = parsed._replace(scheme="https")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise IPPError("endereço IPP inválido")
    return urlunparse(parsed)


def _ipp_uri(uri: str) -> str:
    parsed = urlparse(uri.strip())
    if parsed.scheme in {"http", "https"}:
        parsed = parsed._replace(scheme="ipp" if parsed.scheme == "http" else "ipps")
    return urlunparse(parsed)


def transport_security(uri: str) -> dict[str, Any]:
    """Describe IPP transport security without returning the configured URI.

    Plain HTTP/IPP remains supported for local and legacy printers, but callers
    receive a clear diagnostic so the administration screen can prefer TLS
    whenever the device actually offers HTTPS/IPPS.
    """

    parsed = urlparse(str(uri or "").strip())
    scheme = parsed.scheme.lower()
    if parsed.netloc and scheme in {"https", "ipps"}:
        return {
            "valid": True,
            "encrypted": True,
            "status": "Conexão IPP criptografada.",
            "warning": "",
        }
    if parsed.netloc and scheme in {"http", "ipp"}:
        return {
            "valid": True,
            "encrypted": False,
            "status": "Conexão IPP configurada sem criptografia.",
            "warning": _PLAINTEXT_WARNING,
        }
    return {
        "valid": False,
        "encrypted": False,
        "status": "Endereço IPP ausente ou inválido.",
        "warning": "Configure um endereço IPP válido e prefira HTTPS/IPPS quando houver suporte.",
    }


def build_print_job(printer_uri: str, document: bytes, *, document_format: str = "application/octet-stream") -> bytes:
    """Build an IPP/1.1 Print-Job request carrying raw receipt bytes."""

    payload = bytearray(bytes(_IPP_VERSION) + struct.pack(">H", 0x0002) + struct.pack(">I", _IPP_REQUEST_ID) + b"\x01")
    payload += _attribute(0x47, "attributes-charset", "utf-8")
    payload += _attribute(0x48, "attributes-natural-language", "en")
    payload += _attribute(0x45, "printer-uri", _ipp_uri(printer_uri))
    payload += _attribute(0x42, "requesting-user-name", "TrigoPDV")
    payload += _attribute(0x42, "job-name", "TrigoPDV comprovante")
    payload += _attribute(0x49, "document-format", document_format)
    payload += b"\x03" + document
    return bytes(payload)


def _parse_ipp_response(response: bytes) -> dict[str, int | None]:
    """Validate one complete IPP/1.1 response envelope before accepting it."""

    if len(response) < 8:
        raise IPPError("resposta IPP incompleta")
    if tuple(response[:2]) != _IPP_VERSION:
        raise IPPError("resposta IPP inválida")
    status = struct.unpack(">H", response[2:4])[0]
    request_id = struct.unpack(">I", response[4:8])[0]
    if request_id != _IPP_REQUEST_ID:
        raise IPPError("resposta IPP inválida")

    offset = 8
    last_name = ""
    integer_attributes: dict[str, int] = {}
    found_end = False
    in_group = False
    while offset < len(response):
        tag = response[offset]
        offset += 1
        if tag in _IPP_GROUP_TAGS:
            last_name = ""
            in_group = True
            continue
        if tag == 0x03:
            if offset != len(response):
                raise IPPError("resposta IPP inválida")
            found_end = True
            break
        if tag < 0x10:
            raise IPPError("resposta IPP inválida")
        if not in_group or tag in _IPP_RESERVED_VALUE_TAGS:
            raise IPPError("resposta IPP inválida")
        if offset + 2 > len(response):
            raise IPPError("resposta IPP inválida")
        name_length = struct.unpack(">H", response[offset : offset + 2])[0]
        offset += 2
        if offset + name_length > len(response):
            raise IPPError("resposta IPP inválida")
        try:
            name = response[offset : offset + name_length].decode("utf-8") if name_length else last_name
        except UnicodeDecodeError:
            raise IPPError("resposta IPP inválida") from None
        if not name:
            raise IPPError("resposta IPP inválida")
        offset += name_length
        if offset + 2 > len(response):
            raise IPPError("resposta IPP inválida")
        value_length = struct.unpack(">H", response[offset : offset + 2])[0]
        offset += 2
        if offset + value_length > len(response):
            raise IPPError("resposta IPP inválida")
        value = response[offset : offset + value_length]
        offset += value_length
        last_name = name
        if tag in _IPP_INTEGER_TAGS:
            if len(value) != 4:
                raise IPPError("resposta IPP inválida")
            if name not in integer_attributes:
                integer_attributes[name] = struct.unpack(">I", value)[0]

    if not found_end:
        raise IPPError("resposta IPP inválida")
    if status & 0xFF00:
        raise IPPError("a impressora IPP recusou o comprovante")
    return {
        "job_id": integer_attributes.get("job-id"),
        "job_state": integer_attributes.get("job-state"),
    }


def _limited_response_body(response: Any) -> bytes:
    """Read an IPP response incrementally with a strict decoded-body limit."""

    headers = getattr(response, "headers", None) or {}
    declared = headers.get("Content-Length")
    if declared is not None:
        try:
            declared_length = int(str(declared).strip())
        except (TypeError, ValueError):
            raise IPPError("resposta HTTP inválida") from None
        if declared_length < 0:
            raise IPPError("resposta HTTP inválida")
        if declared_length > MAX_IPP_RESPONSE_BYTES:
            raise IPPError("resposta IPP excedeu o limite seguro")

    body = bytearray()
    try:
        chunks = response.iter_content(chunk_size=_RESPONSE_CHUNK_BYTES)
        for chunk in chunks:
            if not chunk:
                continue
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise IPPError("resposta HTTP inválida")
            if len(body) + len(chunk) > MAX_IPP_RESPONSE_BYTES:
                raise IPPError("resposta IPP excedeu o limite seguro")
            body.extend(chunk)
    except IPPError:
        raise
    except requests.RequestException:
        raise
    except (AttributeError, TypeError, ValueError):
        raise IPPError("resposta HTTP inválida") from None
    except Exception:
        # A transport adapter can surface implementation-specific exceptions;
        # never expose their URL, query string or response fragment upstream.
        raise IPPError("a impressora IPP não respondeu") from None
    return bytes(body)


def _close_response(response: Any) -> None:
    try:
        response.close()
    except Exception:
        # Closing the transport is best-effort and must not hide the real
        # protocol result already obtained above.
        pass


def print_job(printer_uri: str, document: bytes, *, timeout: float = 5.0) -> dict[str, Any]:
    """Submit a document and return the protocol-confirmed job identifiers."""

    http_url = _http_uri(printer_uri)
    try:
        response = requests.post(
            http_url,
            data=build_print_job(printer_uri, document),
            headers={"Content-Type": "application/ipp", "Accept": "application/ipp"},
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
        try:
            try:
                status_code = int(response.status_code)
            except (TypeError, ValueError):
                raise IPPError("resposta HTTP inválida") from None
            if status_code != 200:
                raise IPPError(f"a impressora IPP retornou HTTP {status_code}")
            response_body = _limited_response_body(response)
        finally:
            _close_response(response)
    except requests.RequestException:
        raise IPPError("a impressora IPP não respondeu") from None
    return _parse_ipp_response(response_body)
