"""Thermal-receipt generation and printing adapters."""

from .receipt_printer import PrintResult, ReceiptPrinter, build_receipt_text
from .discovery import PrinterInfo, default_printer_name, list_windows_printers
from .ipp import IPPError, build_print_job, print_job

__all__ = [
    "PrintResult",
    "ReceiptPrinter",
    "build_receipt_text",
    "PrinterInfo",
    "default_printer_name",
    "list_windows_printers",
    "IPPError",
    "build_print_job",
    "print_job",
]
