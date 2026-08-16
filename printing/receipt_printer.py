"""Receipt rendering plus resilient Windows/ESC-POS printing.

The sale itself must never be rolled back because a printer is unavailable.
``ReceiptPrinter`` therefore returns a result object instead of raising for an
operational printer failure.  Its caller can save the rendered receipt for a
later reprint and show the operator a clear warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Thread
from typing import Any, Mapping, Sequence

from services.text import normalize_display_text


RECEIPT_WIDTH = 42
PAPER_COLUMNS = {58: 32, 80: RECEIPT_WIDTH}


@dataclass(frozen=True)
class PrintResult:
    """Outcome of a print attempt, safe to expose in the operator interface."""

    printed: bool
    message: str
    receipt_text: str


def _money(value: Any) -> str:
    """Format a numeric value in Brazilian currency without floating artefacts."""
    try:
        amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        amount = Decimal("0.00")
    return "R$ " + f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _decimal(value: Any) -> Decimal:
    """Parse receipt numbers defensively; invalid display data becomes zero."""

    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _field(value: Any) -> str:
    """Central boundary for every external value rendered on a receipt."""

    return normalize_display_text(value)


def _truncate(text: str, width: int) -> str:
    """Clip with a CP860-safe marker so the result never exceeds ``width``."""

    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    marker = "..." if width >= 3 else "." * width
    return text[: width - len(marker)] + marker


def _line(left: str, right: str = "", *, width: int = RECEIPT_WIDTH) -> str:
    """Place two values legibly within one fixed-width receipt row."""

    width = max(1, int(width))
    left = _field(left)
    right = _field(right)
    if not right:
        return _truncate(left, width)
    if not left:
        return _truncate(right, width).rjust(width)
    if len(left) + len(right) + 1 <= width:
        return f"{left}{' ' * (width - len(left) - len(right))}{right}"

    minimum_left = min(len(left), max(1, width // 3))
    right = _truncate(right, max(1, width - minimum_left - 1))
    left = _truncate(left, max(0, width - len(right) - 1))
    return _truncate(f"{left} {right}", width)


def _center(text: str, *, width: int = RECEIPT_WIDTH) -> str:
    width = max(1, int(width))
    return _truncate(_field(text), width).center(width)


def build_receipt_text(receipt: Mapping[str, Any], *, width: int = RECEIPT_WIDTH) -> str:
    """Create a portable plain-text non-fiscal receipt.

    Expected keys include ``business_name``, ``business_document``, ``address``,
    ``sale_id``, ``items``, ``total``, ``payment_method`` and ``change``.  Missing
    optional fields simply remain out of the receipt, making the function useful
    for previews and tests too.
    """
    width = max(1, int(width))
    divider = "-" * width
    business_name = receipt.get("business_name") or "TRIGO DE MINAS"
    now = receipt.get("date") or datetime.now().strftime("%d/%m/%Y %H:%M")
    lines = [_center(business_name, width=width)]
    if receipt.get("test_print"):
        lines += ["", _center("TESTE DE IMPRESSÃO", width=width)]
    else:
        lines.append(_center("COMPROVANTE NAO FISCAL", width=width))
    if receipt.get("business_document"):
        lines.append(_center(str(receipt["business_document"]), width=width))
    if receipt.get("address"):
        lines.append(_center(str(receipt["address"]), width=width))
    if receipt.get("test_print"):
        lines += [divider, _line("Impressora", str(receipt.get("printer_name") or "—"), width=width), _line("Data/Hora", str(now), width=width), _center("Impressão realizada com sucesso.", width=width), divider]
    lines += [divider, _line(f"Venda #{receipt.get('sale_id', '-')}", str(now), width=width), divider]

    items: Sequence[Mapping[str, Any]] = receipt.get("items") or []
    for item in items:
        name = item.get("nome") or item.get("name") or "Produto"
        qty = item.get("quantidade", item.get("quantity", 0))
        unit = item.get("preco_unitario", item.get("unit_price", 0))
        subtotal = item.get("subtotal", item.get("total", 0))
        lines.append(_truncate(_field(name), width))
        lines.append(_line(f"{qty} x {_money(unit)}", _money(subtotal), width=width))

    lines += [divider, _line("TOTAL", _money(receipt.get("total")), width=width), _line("Pagamento", str(receipt.get("payment_method") or "-"), width=width)]
    change = receipt.get("change", receipt.get("troco", 0))
    if _decimal(change) > 0:
        lines.append(_line("Troco", _money(change), width=width))
    operator = receipt.get("operator") or receipt.get("usuario")
    if operator:
        lines.append(_line("Operador", str(operator), width=width))
    lines += [divider, _center("Obrigado pela preferencia!", width=width), "\n\n\n"]
    return "\n".join(lines)


class ReceiptPrinter:
    """Print receipts through ESC/POS or Windows raw spooler when configured.

    Config keys: ``enabled`` (bool), ``driver`` (``win32raw`` or ``network``),
    ``printer_name``, ``host``, ``port`` and ``queue_dir``. With printing disabled
    it deliberately returns a successful preview result, so a workstation can be
    configured before a thermal printer is physically installed.
    """

    def __init__(self, settings: Mapping[str, Any] | None = None) -> None:
        self.settings = dict(settings or {})

    def _receipt_width(self) -> int:
        try:
            paper_width = int(self.settings.get("paper_width", 80) or 80)
        except (TypeError, ValueError):
            paper_width = 80
        return PAPER_COLUMNS.get(paper_width, RECEIPT_WIDTH)

    def print_receipt(self, receipt: Mapping[str, Any]) -> PrintResult:
        text = build_receipt_text(receipt, width=self._receipt_width())
        if not self._as_bool(self.settings.get("enabled", False)):
            self._save_preview(receipt, text)
            return PrintResult(False, "Impressão desativada; comprovante salvo para consulta.", text)

        try:
            driver = str(self.settings.get("driver", "win32raw")).lower().strip()
            if driver == "network":
                self._print_network(text)
            elif driver == "ipp":
                self._print_ipp(text)
            else:
                self._print_win32raw(text)
            message = "Comprovante enviado para a impressora."
            if driver == "ipp":
                from .ipp import transport_security

                warning = str(
                    transport_security(self.settings.get("uri") or self.settings.get("printer_uri") or "").get(
                        "warning", ""
                    )
                    or ""
                ).strip()
                if warning:
                    message = f"{message} {warning}"
            return PrintResult(True, message, text)
        except Exception as exc:  # Hardware/spooler failures must not invalidate the sale.
            self._save_preview(receipt, text)
            return PrintResult(False, f"{self._friendly_error(exc)} Comprovante salvo para reimpressão.", text)

    def print_receipt_async(self, receipt: Mapping[str, Any], callback: Any = None) -> Thread:
        """Print outside Tk's main thread and optionally report the result.

        The returned daemon thread is intentionally small: the sale is already
        committed before this method is called, so a spooler outage can never
        block or roll back the checkout.
        """

        def worker() -> None:
            result = self.print_receipt(receipt)
            if callable(callback):
                try:
                    callback(result)
                except Exception:
                    # A UI callback must never turn a successful spooler job
                    # into an application-level error.
                    pass

        thread = Thread(target=worker, name="trigopdv-print", daemon=True)
        thread.start()
        return thread

    @staticmethod
    def _as_bool(value: Any) -> bool:
        return str(value).strip().lower() in {"1", "true", "yes", "sim", "on"}

    def _save_preview(self, receipt: Mapping[str, Any], text: str) -> None:
        """Keep a local printable copy when a printer is offline or disabled."""
        try:
            queue_dir = Path(str(self.settings.get("queue_dir") or "data/print_queue"))
            queue_dir.mkdir(parents=True, exist_ok=True)
            raw_sale_id = _field(receipt.get("sale_id", "sem-id"))[:60]
            sale_id = "".join(
                character if character.isalnum() or character in {"-", "_"} else "_"
                for character in raw_sale_id
            ).strip("_") or "sem-id"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            (queue_dir / f"comprovante_{sale_id}_{timestamp}.txt").write_text(text, encoding="utf-8")
        except OSError:
            # A queued copy is convenient but is not more important than informing the caller.
            pass

    @staticmethod
    def _friendly_error(error: Exception) -> str:
        message = str(error or "").strip().lower()
        if (
            not message
            or "openprinter" in message
            or "printer name" in message
            or "invalid printer" in message
            or "does not exist" in message
            or ("printer" in message and "not" in message)
        ):
            return "A impressora configurada não está disponível. Escolha outra impressora ou atualize a lista."
        if "pywin32" in message or "win32print" in message:
            return "O suporte de impressão do Windows não está instalado. Instale o driver oficial da impressora."
        if "escpos" in message or "endereço" in message or "ip" in message:
            return "A impressora de rede não respondeu. Verifique energia, cabo e rede."
        if "timeout" in message or "timed out" in message:
            return "A impressora não respondeu a tempo. Verifique se está ligada e atualize a lista."
        return "Não foi possível imprimir. Verifique papel, conexão e a fila do Windows."

    def _print_win32raw(self, text: str) -> None:
        printer_name = str(self.settings.get("printer_name") or "").strip()
        if not printer_name:
            try:
                from .discovery import default_printer_name

                printer_name = default_printer_name()
            except Exception:
                printer_name = ""
        if not printer_name:
            raise RuntimeError("nome da impressora não configurado")
        try:
            import win32print  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("pywin32 não está instalado") from exc

        handle = win32print.OpenPrinter(printer_name)
        try:
            job_id = win32print.StartDocPrinter(handle, 1, ("PDV Trigo de Minas", None, "RAW"))
            try:
                win32print.StartPagePrinter(handle)
                payload = text.encode("cp860", errors="replace")
                if self._as_bool(self.settings.get("cut_paper", True)):
                    payload += b"\x1b\x64\x05\x1d\x56\x00"
                win32print.WritePrinter(handle, payload)
                win32print.EndPagePrinter(handle)
            finally:
                win32print.EndDocPrinter(handle)
        finally:
            win32print.ClosePrinter(handle)

    def _print_network(self, text: str) -> None:
        host = str(self.settings.get("host") or "").strip()
        if not host:
            raise RuntimeError("endereço IP da impressora não configurado")
        try:
            from escpos.printer import Network  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("python-escpos não está instalado") from exc
        port = int(self.settings.get("port") or 9100)
        printer = Network(host, port=port, timeout=3)
        try:
            printer.text(text)
            if self._as_bool(self.settings.get("cut_paper", True)):
                printer.cut()
        finally:
            printer.close()

    def _print_ipp(self, text: str) -> None:
        uri = str(self.settings.get("uri") or self.settings.get("printer_uri") or "").strip()
        if not uri:
            raise RuntimeError("endereço IPP não configurado")
        try:
            from .ipp import print_job

            print_job(uri, text.encode("cp860", errors="replace"), timeout=float(self.settings.get("timeout", 5) or 5))
        except ImportError as exc:
            raise RuntimeError("requests não está instalado") from exc
