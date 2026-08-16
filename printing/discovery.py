"""Discover printers installed in the Windows spooler.

The PDV deliberately asks Windows for the current printer list instead of
maintaining a model/name allow-list.  This covers local USB drivers,
network-installed printers and shared connections through the same path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from subprocess import CompletedProcess, run
from typing import Any


@dataclass(frozen=True)
class PrinterInfo:
    """Small, serialisable representation safe for the Tk UI."""

    name: str
    is_default: bool = False
    available: bool = True
    status: str = "Disponível"
    port: str = ""
    driver: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _status_from_win32(status: int, attributes: int) -> tuple[bool, str]:
    """Translate spooler flags into an operator-friendly status."""

    try:
        import win32print  # type: ignore[import-not-found]

        error_flags = (
            getattr(win32print, "PRINTER_STATUS_ERROR", 0x00000002)
            | getattr(win32print, "PRINTER_STATUS_OFFLINE", 0x00000080)
            | getattr(win32print, "PRINTER_STATUS_PAPER_OUT", 0x00000010)
            | getattr(win32print, "PRINTER_STATUS_PAPER_JAM", 0x00000008)
            | getattr(win32print, "PRINTER_STATUS_USER_INTERVENTION", 0x00100000)
            | getattr(win32print, "PRINTER_STATUS_SERVER_UNKNOWN", 0x00800000)
        )
        work_offline = getattr(win32print, "PRINTER_ATTRIBUTE_WORK_OFFLINE", 0x00000400)
    except ImportError:
        error_flags = 0x0090009A
        work_offline = 0x00000400
    if int(status or 0) & error_flags or int(attributes or 0) & work_offline:
        return False, "Indisponível"
    return True, "Disponível"


def _normalise_name(value: Any) -> str:
    return str(value or "").strip()


def _from_win32() -> list[PrinterInfo]:
    try:
        import win32print  # type: ignore[import-not-found]
    except ImportError:
        return []
    flags = (
        getattr(win32print, "PRINTER_ENUM_LOCAL", 2)
        | getattr(win32print, "PRINTER_ENUM_CONNECTIONS", 4)
    )
    try:
        rows = win32print.EnumPrinters(flags, None, 2)
        default_name = _normalise_name(win32print.GetDefaultPrinter())
    except Exception:
        return []
    result: list[PrinterInfo] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = _normalise_name(row.get("pPrinterName") or row.get("PrinterName"))
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        available, status = _status_from_win32(
            int(row.get("Status") or 0), int(row.get("Attributes") or 0)
        )
        result.append(
            PrinterInfo(
                name=name,
                is_default=name.casefold() == default_name.casefold() if default_name else False,
                available=available,
                status=status,
                port=_normalise_name(row.get("pPortName") or row.get("PortName")),
                driver=_normalise_name(row.get("pDriverName") or row.get("DriverName")),
            )
        )
    return sorted(result, key=lambda item: (not item.is_default, item.name.casefold()))


def _from_powershell() -> list[PrinterInfo]:
    """Fallback for Windows installations where pywin32 is unavailable."""

    if os.name != "nt":
        return []
    command = (
        "Get-Printer | Select-Object Name,Default,PrinterStatus,PortName,DriverName "
        "| ConvertTo-Json -Compress"
    )
    try:
        completed: CompletedProcess[str] = run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return []
        payload = json.loads(completed.stdout)
    except (OSError, ValueError, TimeoutError):
        return []
    rows = payload if isinstance(payload, list) else [payload]
    result: list[PrinterInfo] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _normalise_name(row.get("Name"))
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        raw_status = _normalise_name(row.get("PrinterStatus"))
        unavailable = raw_status.casefold() in {
            "offline",
            "error",
            "paperout",
            "paperjam",
            "notavailable",
        }
        result.append(
            PrinterInfo(
                name=name,
                is_default=bool(row.get("Default")),
                available=not unavailable,
                status="Indisponível" if unavailable else "Disponível",
                port=_normalise_name(row.get("PortName")),
                driver=_normalise_name(row.get("DriverName")),
            )
        )
    return sorted(result, key=lambda item: (not item.is_default, item.name.casefold()))


def list_windows_printers() -> list[dict[str, Any]]:
    """Return the live Windows printer list, with a safe empty fallback."""

    printers = _from_win32()
    if not printers:
        printers = _from_powershell()
    return [printer.as_dict() for printer in printers]


def default_printer_name() -> str:
    """Return Windows' current default printer without raising."""

    try:
        import win32print  # type: ignore[import-not-found]

        return _normalise_name(win32print.GetDefaultPrinter())
    except Exception:
        for printer in list_windows_printers():
            if printer.get("is_default"):
                return _normalise_name(printer.get("name"))
    return ""

