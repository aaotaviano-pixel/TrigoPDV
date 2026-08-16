from __future__ import annotations

import sys
import tempfile
import threading
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from config.settings import EXAMPLE_CONFIG_PATH, load_settings, save_printer_settings
from desktop_controller import DesktopController
from printing.discovery import list_windows_printers
from printing.ipp import IPPError, build_print_job, print_job
from printing.receipt_printer import ReceiptPrinter, build_receipt_text
from services.pdv_service import PDVService
from tests.support import TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD, provision_test_admin


class PrintingTestCase(unittest.TestCase):
    def test_windows_list_comes_from_spooler_and_marks_default(self) -> None:
        fake = types.SimpleNamespace(
            PRINTER_ENUM_LOCAL=2,
            PRINTER_ENUM_CONNECTIONS=4,
            PRINTER_STATUS_OFFLINE=0x80,
            PRINTER_STATUS_ERROR=0x02,
            PRINTER_STATUS_PAPER_OUT=0x10,
            PRINTER_STATUS_PAPER_JAM=0x08,
            PRINTER_STATUS_USER_INTERVENTION=0x100000,
            PRINTER_STATUS_SERVER_UNKNOWN=0x800000,
            PRINTER_ATTRIBUTE_WORK_OFFLINE=0x400,
            EnumPrinters=Mock(
                return_value=[
                    {
                        "pPrinterName": "USB Térmica",
                        "pPortName": "USB001",
                        "pDriverName": "Driver Térmico",
                        "Status": 0,
                        "Attributes": 0,
                    },
                    {
                        "pPrinterName": r"\\caixa\Epson Rede",
                        "pPortName": r"\\caixa\Epson Rede",
                        "pDriverName": "Driver de Rede",
                        "Status": 0x80,
                        "Attributes": 0,
                    },
                ]
            ),
            GetDefaultPrinter=Mock(return_value="USB Térmica"),
        )
        with patch.dict(sys.modules, {"win32print": fake}):
            printers = list_windows_printers()
        self.assertEqual(printers[0]["name"], "USB Térmica")
        self.assertTrue(printers[0]["is_default"])
        self.assertTrue(printers[0]["available"])
        self.assertFalse(printers[1]["available"])
        self.assertEqual(printers[1]["status"], "Indisponível")

    def test_selection_is_persisted_and_reloaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.ini"
            config_path.write_text(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            settings = load_settings(config_path)
            service = PDVService(settings=settings)
            provision_test_admin(service.database)
            controller = DesktopController(service, settings)
            controller.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)
            printers = [{"name": "Elgin i9", "available": True, "status": "Disponível"}]
            with patch("desktop_controller.list_windows_printers", return_value=printers), patch(
                "desktop_controller.default_printer_name", return_value="Elgin i9"
            ):
                result = controller.save_printer_selection("Elgin i9")
            self.assertEqual(result["configured_name"], "Elgin i9")
            self.assertTrue(result["enabled"])
            reloaded = load_settings(config_path)
            self.assertEqual(reloaded.printer_name, "Elgin i9")
            self.assertTrue(reloaded.printer_enabled)
            self.assertEqual(reloaded.printer_mode, "SELECIONADA")
            self.assertEqual(reloaded.printer_driver, "win32raw")
            controller.shutdown()

    def test_removed_printer_is_reported_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.ini"
            config_path.write_text(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            settings = load_settings(config_path)
            settings = replace(
                settings,
                printer_name="Impressora removida",
                printer_enabled=True,
                printer_mode="SELECIONADA",
            )
            service = PDVService(settings=settings)
            provision_test_admin(service.database)
            controller = DesktopController(service, settings)
            controller.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)
            with patch("desktop_controller.list_windows_printers", return_value=[]), patch(
                "desktop_controller.default_printer_name", return_value=""
            ):
                configuration = controller.printer_configuration()
            self.assertFalse(configuration["selected_found"])
            self.assertEqual(configuration["status"], "Não encontrada")
            self.assertEqual(configuration["effective_name"], "Impressora removida")
            controller.shutdown()

    def test_default_selected_and_disabled_modes_persist_without_silent_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.ini"
            config_path.write_text(
                EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8"
            )

            save_printer_settings(
                config_path,
                printer_name="",
                enabled=True,
                mode="PADRAO_WINDOWS",
            )
            default_settings = load_settings(config_path)
            self.assertTrue(default_settings.printer_enabled)
            self.assertEqual(default_settings.printer_mode, "PADRAO_WINDOWS")
            self.assertEqual(default_settings.printer_name, "")

            save_printer_settings(
                config_path,
                printer_name="Impressora que não deve sobreviver",
                enabled=False,
                mode="DESATIVADA",
            )
            disabled = load_settings(config_path)
            self.assertFalse(disabled.printer_enabled)
            self.assertEqual(disabled.printer_mode, "DESATIVADA")
            self.assertEqual(disabled.printer_name, "")

    def test_paper_width_is_validated_persisted_and_controls_receipt_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.ini"
            config_path.write_text(
                EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8"
            )
            settings = load_settings(config_path)
            self.assertEqual(settings.printer_paper_width, 80)

            save_printer_settings(
                config_path,
                printer_name="",
                enabled=False,
                paper_width=58,
            )
            narrow = load_settings(config_path)
            self.assertEqual(narrow.printer_paper_width, 58)
            rendered = ReceiptPrinter(
                {"enabled": False, "paper_width": 58, "queue_dir": directory}
            ).print_receipt(
                {
                    "items": [{"nome": "Produto longo para validar largura", "quantidade": 1, "preco_unitario": 10, "subtotal": 10}],
                    "total": 10,
                }
            ).receipt_text
            self.assertTrue(all(len(row) <= 32 for row in rendered.splitlines()))

            with self.assertRaisesRegex(ValueError, "58 ou 80"):
                save_printer_settings(
                    config_path,
                    printer_name="",
                    enabled=False,
                    paper_width=57,
                )

    def test_saving_only_paper_width_preserves_direct_ipp_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.ini"
            content = EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8").replace(
                "uri =", "uri = http://printer.local/p/virtual"
            )
            config_path.write_text(content, encoding="utf-8")
            settings = replace(
                load_settings(config_path),
                printer_enabled=True,
                printer_mode="PADRAO_WINDOWS",
                printer_driver="ipp",
                printer_uri="http://printer.local/p/virtual",
            )
            service = PDVService(settings=settings)
            provision_test_admin(service.database)
            controller = DesktopController(service, settings)
            controller.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)
            with patch("desktop_controller.list_windows_printers", return_value=[]), patch(
                "desktop_controller.default_printer_name", return_value=""
            ):
                controller.save_printer_paper_width(58)

            reloaded = load_settings(config_path)
            self.assertEqual(reloaded.printer_uri, "http://printer.local/p/virtual")
            self.assertEqual(reloaded.printer_paper_width, 58)
            controller.shutdown()

    def test_default_windows_mode_can_run_test_print_without_named_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.ini"
            config_path.write_text(
                EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8"
            )
            settings = replace(
                load_settings(config_path),
                printer_enabled=True,
                printer_mode="PADRAO_WINDOWS",
                printer_name="",
            )
            service = PDVService(settings=settings)
            provision_test_admin(service.database)
            controller = DesktopController(service, settings)
            controller.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)
            controller.printer.print_receipt = Mock(
                return_value=types.SimpleNamespace(
                    printed=True, message="ok", receipt_text="teste"
                )
            )
            with patch("desktop_controller.default_printer_name", return_value="Elgin padrão"):
                result = controller.test_printer()
            self.assertTrue(result["printed"])
            receipt = controller.printer.print_receipt.call_args.args[0]
            self.assertEqual(receipt["printer_name"], "Elgin padrão")
            controller.shutdown()

    def test_thermal_receipt_profiles_keep_totals_and_accents_legible(self) -> None:
        receipt = {
            "business_name": "Padaria Trigo de Minas",
            "sale_id": 27,
            "date": "15/08/2026 17:30",
            "items": [
                {
                    "nome": "Pão de queijo mineiro",
                    "quantidade": "0.250",
                    "preco_unitario": "12.50",
                    "subtotal": "3.13",
                }
            ],
            "total": "3.13",
            "payment_method": "PIX",
            "operator": "José",
        }
        for paper_width, columns in ((58, 32), (80, 42)):
            with self.subTest(paper_width=paper_width):
                text = ReceiptPrinter(
                    {"enabled": False, "paper_width": paper_width, "queue_dir": tempfile.gettempdir()}
                ).print_receipt(receipt).receipt_text
                self.assertTrue(all(len(row) <= columns for row in text.splitlines()))
                self.assertIn("Pão de queijo", text)
                self.assertIn("R$ 3,13", text)
                self.assertIn("PIX", text)
                self.assertIn("José", text)

    def test_test_receipt_reports_spooler_failure_clearly(self) -> None:
        printer = ReceiptPrinter({"enabled": True, "printer_name": "USB Térmica", "queue_dir": tempfile.gettempdir()})
        printer._print_win32raw = Mock(side_effect=RuntimeError("OpenPrinter failed"))  # type: ignore[method-assign]
        result = printer.print_receipt({"test_print": True, "printer_name": "USB Térmica", "date": "agora"})
        self.assertFalse(result.printed)
        self.assertIn("não está disponível", result.message)
        self.assertIn("TESTE DE IMPRESSÃO", result.receipt_text)

    def test_printing_runs_in_daemon_worker(self) -> None:
        finished = threading.Event()
        received: list[object] = []
        printer = ReceiptPrinter({"enabled": True, "printer_name": "USB Térmica"})
        printer._print_win32raw = Mock(side_effect=lambda _text: finished.set())  # type: ignore[method-assign]
        thread = printer.print_receipt_async(
            {"test_print": True, "printer_name": "USB Térmica"}, lambda result: received.append(result)
        )
        self.assertTrue(finished.wait(2))
        thread.join(2)
        self.assertTrue(thread.daemon)
        self.assertEqual(len(received), 1)
        self.assertTrue(received[0].printed)

    def test_config_save_preserves_other_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            save_printer_settings(path, printer_name="Rede Padaria", enabled=True)
            loaded = load_settings(path)
            self.assertEqual(loaded.pix_city, "SAO PAULO")
            self.assertEqual(loaded.printer_name, "Rede Padaria")

    def test_ipp_print_job_builds_protocol_request_and_accepts_success(self) -> None:
        body = b"\x01\x01\x00\x00\x00\x00\x00\x01\x03"
        response = types.SimpleNamespace(
            status_code=200,
            headers={"Content-Length": str(len(body))},
            iter_content=Mock(return_value=iter([body])),
            close=Mock(),
        )
        with patch("printing.ipp.requests.post", return_value=response) as post:
            result = print_job("http://printer.local:10631/p/virtual", b"teste", timeout=1)
        self.assertIsNone(result["job_id"])
        request = post.call_args.kwargs["data"]
        self.assertIn(b"printer-uri", request)
        self.assertIn(b"ipp://printer.local:10631/p/virtual", request)
        self.assertIn(b"teste", request)

    def test_ipp_invalid_endpoint_is_clear(self) -> None:
        with self.assertRaises(IPPError):
            print_job("not-an-ipp-url", b"teste")

    def test_receipt_printer_supports_ipp_driver(self) -> None:
        with patch("printing.ipp.print_job", return_value={"job_id": 4, "job_state": 5}) as submit:
            result = ReceiptPrinter(
                {"enabled": True, "driver": "ipp", "uri": "http://printer.local/p/virtual"}
            ).print_receipt({"test_print": True, "printer_name": "virtual"})
        self.assertTrue(result.printed)
        submit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
