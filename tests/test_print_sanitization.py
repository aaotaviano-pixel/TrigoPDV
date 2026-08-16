from __future__ import annotations

import sys
import tempfile
import types
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

from db.database import Database
from integrations.cosmos import CosmosClient
from integrations.open_food_facts import OpenFoodFactsClient, normalize_gtin
from printing.receipt_printer import ReceiptPrinter, _line, build_receipt_text
from services.products import ProductService, _brand, _name, _short_text
from services.text import normalize_display_text
from tests.support import provision_test_admin


UNSAFE_CONTROLS = (
    "".join(chr(code) for code in range(0x20))
    + "".join(chr(code) for code in range(0x7F, 0xA0))
    + "\u00ad\u061c\u200b\u200e\u202e\u2066\u2069\u206f\ufeff"
)


def malicious(label: str) -> str:
    return f"{label}\r\nSeguro{UNSAFE_CONTROLS}Final"


class _Response:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response

    def get(self, *_args, **_kwargs) -> _Response:
        return self.response


class PrintSanitizationTestCase(unittest.TestCase):
    def assert_safe_rendered_text(self, text: str) -> None:
        self.assertNotIn("\r", text)
        for character in text:
            if character == "\n":
                continue  # Separadores adicionados deliberadamente pelo renderizador.
            self.assertNotIn(
                unicodedata.category(character),
                {"Cc", "Cf", "Zl", "Zp"},
                f"controle inseguro U+{ord(character):04X} atravessou a renderização",
            )

    @staticmethod
    def hostile_receipt() -> dict:
        return {
            "business_name": malicious("Padaria São José"),
            "business_document": malicious("Documento"),
            "address": malicious("Rua João Pinheiro"),
            "sale_id": malicious("42"),
            "date": malicious("15/08/2026 12:00"),
            "test_print": True,
            "printer_name": malicious("Térmica"),
            "items": [
                {
                    "nome": malicious("Pão de queijo"),
                    "quantidade": malicious("2"),
                    "preco_unitario": "3.50",
                    "subtotal": "7.00",
                },
                {
                    "name": malicious("Café"),
                    "quantity": "1",
                    "unit_price": "2.00",
                    "total": "2.00",
                },
            ],
            "total": "9.00",
            "payment_method": malicious("PIX"),
            "change": 0,
            "operator": malicious("José"),
        }

    def test_every_external_receipt_field_is_sanitized_before_rendering(self) -> None:
        text = build_receipt_text(self.hostile_receipt())

        self.assert_safe_rendered_text(text)
        for expected in (
            "Padaria São José Seguro Final",
            "Documento Seguro Final",
            "Rua João Pinheiro Seguro Final",
            "Pão de queijo Seguro Final",
            "Café Seguro Final",
            "PIX Seguro Final",
            "José Seguro Final",
            "Térmica Seguro Final",
        ):
            self.assertIn(expected, text)

        sale_id_text = build_receipt_text(
            {"sale_id": malicious("42"), "date": "D", "items": [], "total": 0}
        )
        self.assertIn("Venda #42 Seguro Final", sale_id_text)

    def test_all_unicode_cc_and_cf_characters_are_blocked_at_render_boundary(self) -> None:
        every_control = "".join(
            chr(code)
            for code in range(sys.maxunicode + 1)
            if unicodedata.category(chr(code)) in {"Cc", "Cf"}
        )

        text = build_receipt_text(
            {"business_name": f"Início{every_control}Fim", "items": [], "total": 0}
        )

        self.assertIn("Início Fim", text)
        self.assert_safe_rendered_text(text)

    def test_invalid_numeric_fields_cannot_inject_commands_or_break_receipt(self) -> None:
        receipt = self.hostile_receipt()
        receipt.update(
            {
                "total": "9\x1b\x1d",
                "change": "1\x00\u200b",
            }
        )

        text = build_receipt_text(receipt)

        self.assert_safe_rendered_text(text)
        self.assertIn("TOTAL", text)

    def test_decomposed_accents_are_nfc_in_raw_and_ipp_payloads(self) -> None:
        receipt = {
            "business_name": "Cafe\u0301 e pa\u0303o",
            "date": "15/08/2026 12:00",
            "sale_id": "NFC",
            "items": [],
            "total": 0,
            "payment_method": "Dinheiro",
            "change": 0,
        }
        writes: list[bytes] = []
        fake_win32print = types.SimpleNamespace(
            OpenPrinter=lambda _name: object(),
            StartDocPrinter=lambda *_args: 1,
            StartPagePrinter=lambda *_args: None,
            WritePrinter=lambda _handle, payload: writes.append(payload),
            EndPagePrinter=lambda *_args: None,
            EndDocPrinter=lambda *_args: None,
            ClosePrinter=lambda *_args: None,
        )
        with patch.dict(sys.modules, {"win32print": fake_win32print}):
            raw_result = ReceiptPrinter(
                {"enabled": True, "printer_name": "Mock", "cut_paper": False}
            ).print_receipt(receipt)
        self.assertTrue(raw_result.printed)
        raw_text = writes[0].decode("cp860")
        self.assertIn("Café e pão", raw_text)
        self.assertNotIn("?", raw_text)

        with patch("printing.ipp.print_job", return_value={"job_id": 2}) as submit:
            ipp_result = ReceiptPrinter(
                {
                    "enabled": True,
                    "driver": "ipp",
                    "uri": "http://mock/p/virtual",
                    "cut_paper": False,
                }
            ).print_receipt(receipt)
        self.assertTrue(ipp_result.printed)
        ipp_text = submit.call_args.args[1].decode("cp860")
        self.assertIn("Café e pão", ipp_text)
        self.assertNotIn("?", ipp_text)

    def test_line_and_every_receipt_row_never_exceed_configured_width(self) -> None:
        sentinel = "X" * 100
        right_heavy = _line("Pagamento", sentinel, width=42)
        self.assertEqual(len(right_heavy), 42)
        self.assertTrue(right_heavy.startswith("Pagamento "))
        self.assertTrue(right_heavy.endswith("..."))

        left_heavy = _line(sentinel, "R$ 1,00", width=42)
        self.assertEqual(len(left_heavy), 42)
        self.assertTrue(left_heavy.endswith("R$ 1,00"))
        self.assertIn("...", left_heavy)

        receipt = {
            "business_name": sentinel,
            "business_document": sentinel,
            "address": sentinel,
            "sale_id": sentinel,
            "date": sentinel,
            "test_print": True,
            "printer_name": sentinel,
            "items": [
                {
                    "nome": sentinel,
                    "quantidade": sentinel,
                    "preco_unitario": "9" * 26,
                    "subtotal": "8" * 26,
                }
            ],
            "total": "7" * 26,
            "payment_method": sentinel,
            "operator": sentinel,
        }
        for width in (42, 24):
            rendered = build_receipt_text(receipt, width=width)
            self.assertTrue(rendered.splitlines())
            self.assertTrue(all(len(row) <= width for row in rendered.splitlines()))

    def test_normalization_preserves_legitimate_repeated_spaces(self) -> None:
        spaced = "Pão  artesanal   mineiro"
        self.assertEqual(normalize_display_text(spaced), spaced)
        self.assertEqual(_name(spaced), spaced)
        receipt = build_receipt_text(
            {"items": [{"nome": spaced, "quantidade": 1, "preco_unitario": 1, "subtotal": 1}], "total": 1}
        )
        self.assertIn(spaced, receipt)

    def test_unicode_line_separators_are_spaces_in_every_field_and_transport(self) -> None:
        def injected(label: str) -> str:
            return f"{label}\u2028Meio\u2029Fim"

        receipt = {
            "business_name": injected("Padaria"),
            "business_document": injected("Documento"),
            "address": injected("Endereço"),
            "sale_id": injected("Venda"),
            "date": injected("Data"),
            "test_print": True,
            "printer_name": injected("Térmica"),
            "items": [
                {
                    "nome": injected("Produto"),
                    "quantidade": injected("2"),
                    "preco_unitario": 3,
                    "subtotal": 6,
                }
            ],
            "total": 6,
            "payment_method": injected("PIX"),
            "operator": injected("José"),
        }
        safe_receipt = {
            key: (
                value.replace("\u2028", " ").replace("\u2029", " ")
                if isinstance(value, str)
                else value
            )
            for key, value in receipt.items()
        }
        safe_receipt["items"] = [
            {
                key: (
                    value.replace("\u2028", " ").replace("\u2029", " ")
                    if isinstance(value, str)
                    else value
                )
                for key, value in receipt["items"][0].items()
            }
        ]
        expected = build_receipt_text(safe_receipt)
        rendered = build_receipt_text(receipt)
        self.assertEqual(rendered, expected)
        self.assertNotIn("\u2028", rendered)
        self.assertNotIn("\u2029", rendered)
        self.assertEqual(len(rendered.splitlines()), len(expected.splitlines()))

        with tempfile.TemporaryDirectory() as directory:
            preview = ReceiptPrinter({"enabled": False, "queue_dir": directory}).print_receipt(receipt)
            saved = next(Path(directory).glob("comprovante_*.txt")).read_text(encoding="utf-8")
        self.assertEqual(preview.receipt_text, expected)
        self.assertEqual(saved, expected)

        writes: list[bytes] = []
        fake_win32print = types.SimpleNamespace(
            OpenPrinter=lambda _name: object(),
            StartDocPrinter=lambda *_args: 1,
            StartPagePrinter=lambda *_args: None,
            WritePrinter=lambda _handle, payload: writes.append(payload),
            EndPagePrinter=lambda *_args: None,
            EndDocPrinter=lambda *_args: None,
            ClosePrinter=lambda *_args: None,
        )
        with patch.dict(sys.modules, {"win32print": fake_win32print}):
            raw = ReceiptPrinter(
                {"enabled": True, "printer_name": "Mock", "cut_paper": False}
            ).print_receipt(receipt)
        self.assertTrue(raw.printed)
        self.assertEqual(writes[0].decode("cp860"), expected)

        network_text: list[str] = []

        class FakeNetwork:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def text(self, value: str) -> None:
                network_text.append(value)

            def cut(self) -> None:
                raise AssertionError("corte não deveria ser chamado")

            def close(self) -> None:
                pass

        escpos = types.ModuleType("escpos")
        escpos_printer = types.ModuleType("escpos.printer")
        escpos_printer.Network = FakeNetwork  # type: ignore[attr-defined]
        escpos.printer = escpos_printer  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"escpos": escpos, "escpos.printer": escpos_printer}):
            network = ReceiptPrinter(
                {"enabled": True, "driver": "network", "host": "mock", "cut_paper": False}
            ).print_receipt(receipt)
        self.assertTrue(network.printed)
        self.assertEqual(network_text, [expected])

        with patch("printing.ipp.print_job", return_value={"job_id": 3}) as submit:
            ipp = ReceiptPrinter(
                {"enabled": True, "driver": "ipp", "uri": "http://mock/p/virtual"}
            ).print_receipt(receipt)
        self.assertTrue(ipp.printed)
        self.assertEqual(submit.call_args.args[1].decode("cp860"), expected)

    def test_preview_uses_the_same_safe_rendering_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            printer = ReceiptPrinter({"enabled": False, "queue_dir": directory})
            result = printer.print_receipt(self.hostile_receipt())
            previews = list(Path(directory).glob("comprovante_*.txt"))

            self.assertEqual(len(previews), 1)
            self.assertEqual(previews[0].read_text(encoding="utf-8"), result.receipt_text)
            self.assert_safe_rendered_text(result.receipt_text)

    def test_win32_raw_payload_has_no_data_commands_and_cut_is_configuration_gated(self) -> None:
        writes: list[bytes] = []
        fake_win32print = types.SimpleNamespace(
            OpenPrinter=lambda _name: object(),
            StartDocPrinter=lambda *_args: 1,
            StartPagePrinter=lambda *_args: None,
            WritePrinter=lambda _handle, payload: writes.append(payload),
            EndPagePrinter=lambda *_args: None,
            EndDocPrinter=lambda *_args: None,
            ClosePrinter=lambda *_args: None,
        )
        with patch.dict(sys.modules, {"win32print": fake_win32print}):
            result = ReceiptPrinter(
                {"enabled": True, "printer_name": "Mock", "cut_paper": False}
            ).print_receipt(self.hostile_receipt())

        self.assertTrue(result.printed)
        self.assertEqual(len(writes), 1)
        self.assertNotIn(b"\x00", writes[0])
        self.assertNotIn(b"\x1b", writes[0])
        self.assertNotIn(b"\x1d", writes[0])
        self.assert_safe_rendered_text(writes[0].decode("cp860"))

        writes.clear()
        with patch.dict(sys.modules, {"win32print": fake_win32print}):
            ReceiptPrinter(
                {"enabled": True, "printer_name": "Mock", "cut_paper": True}
            ).print_receipt(self.hostile_receipt())
        cut = b"\x1b\x64\x05\x1d\x56\x00"
        self.assertTrue(writes[0].endswith(cut))
        self.assertNotIn(b"\x1b", writes[0][:-len(cut)])
        self.assertNotIn(b"\x1d", writes[0][:-len(cut)])
        self.assertNotIn(b"\x00", writes[0][:-len(cut)])

    def test_network_and_ipp_transports_receive_only_sanitized_text(self) -> None:
        network_text: list[str] = []
        network_cuts: list[bool] = []

        class FakeNetwork:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def text(self, value: str) -> None:
                network_text.append(value)

            def cut(self) -> None:
                network_cuts.append(True)

            def close(self) -> None:
                pass

        escpos = types.ModuleType("escpos")
        escpos_printer = types.ModuleType("escpos.printer")
        escpos_printer.Network = FakeNetwork  # type: ignore[attr-defined]
        escpos.printer = escpos_printer  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"escpos": escpos, "escpos.printer": escpos_printer}):
            result = ReceiptPrinter(
                {"enabled": True, "driver": "network", "host": "mock", "cut_paper": False}
            ).print_receipt(self.hostile_receipt())
        self.assertTrue(result.printed)
        self.assertEqual(network_cuts, [])
        self.assert_safe_rendered_text(network_text[0])

        with patch("printing.ipp.print_job", return_value={"job_id": 1}) as submit:
            result = ReceiptPrinter(
                {"enabled": True, "driver": "ipp", "uri": "http://mock/p/virtual"}
            ).print_receipt(self.hostile_receipt())
        self.assertTrue(result.printed)
        document = submit.call_args.args[1]
        self.assertNotIn(b"\x00", document)
        self.assertNotIn(b"\x1b", document)
        self.assertNotIn(b"\x1d", document)
        self.assert_safe_rendered_text(document.decode("cp860"))

    def test_product_and_integration_normalization_removes_controls_without_damaging_gtin(self) -> None:
        code = "7894900011517"
        self.assertEqual(normalize_gtin(code), code)
        self.assertEqual(_name(malicious("Pão francês")), "Pão francês Seguro Final")
        self.assertEqual(_brand(malicious("Café Três Corações")), "Café Três Corações Seguro Final")
        self.assertEqual(_short_text(malicious("Pacote 500 g"), "embalagem", 120), "Pacote 500 g Seguro Final")
        response = _Response(
            {
                "status": 1,
                "product": {
                    "product_name_pt": malicious("Bolo de fubá"),
                    "brands": malicious("Marca mineira"),
                    "categories_pt": malicious("Padaria"),
                    "quantity": malicious("400 g"),
                },
            }
        )
        product = OpenFoodFactsClient(session=_Session(response)).lookup(code)
        self.assertIsNotNone(product)
        self.assertEqual(product.gtin, code)
        self.assertEqual(product.name, "Bolo de fubá Seguro Final")
        self.assertEqual(product.brand, "Marca mineira Seguro Final")
        self.assertEqual(product.category, "Padaria Seguro Final")
        self.assertEqual(product.packaging, "400 g Seguro Final")

        database = Database(":memory:")
        database.initialize()
        admin = provision_test_admin(database)
        unsafe_external = types.SimpleNamespace(
            lookup=lambda _gtin: types.SimpleNamespace(
                gtin="0000000000000",
                name=malicious("Queijo Minas"),
                brand=malicious("Marca local"),
                category="Padaria",
                packaging=malicious("500 g"),
            )
        )
        persisted = ProductService(database, external_client=unsafe_external).lookup_external(
            code, actor_id=admin["id"]
        )["product"]
        self.assertEqual(persisted["gtin"], code)
        self.assertEqual(persisted["nome"], "Queijo Minas Seguro Final")
        self.assertEqual(persisted["marca"], "Marca local Seguro Final")
        self.assertEqual(persisted["detalhes_embalagem"], "500 g Seguro Final")

    def test_manual_and_cosmos_hostile_text_are_safe_in_database_and_cache(self) -> None:
        database = Database(":memory:")
        database.initialize()
        admin = provision_test_admin(database)
        products = ProductService(
            database,
            external_client=types.SimpleNamespace(lookup=lambda _gtin: None),
        )
        manual_code = "7891234567895"
        created = products.create_product(
            manual_code,
            "Cafe\u0301  artesanal\r\nSeguro\x1b\x1d\u200b",
            10,
            marca="Pa\u0303o  Mineiro\x00",
            subcategoria="Fresco\r\nHoje\x1b",
            detalhes_embalagem="Pacote  500 g\x1d",
            fonte_validacao="Cadastro\u200b local",
            actor_id=admin["id"],
        )
        self.assertEqual(created["gtin"], manual_code)
        self.assertEqual(created["nome"], "Café  artesanal Seguro")
        self.assertEqual(created["marca"], "Pão  Mineiro")
        self.assertEqual(created["subcategoria"], "Fresco Hoje")
        self.assertEqual(created["detalhes_embalagem"], "Pacote  500 g")
        self.assertEqual(created["fonte_validacao"], "Cadastro  local")

        updated = products.update_product(
            manual_code,
            actor_id=admin["id"],
            nome="Cafe\u0301  atualizado\x00\x1b",
            marca="Marca  nova\r\nSegura",
        )
        self.assertEqual(updated["gtin"], manual_code)
        self.assertEqual(updated["nome"], "Café  atualizado")
        self.assertEqual(updated["marca"], "Marca  nova Segura")

        cosmos_code = "7894900011517"
        cosmos_response = _Response(
            {
                "product": {
                    "description": "Cafe\u0301 Minas\r\nSeguro\x1b\x1d\u200b",
                    "brand": "Pa\u0303o Mineiro\x00",
                    "category": {"name": "Padaria\x1b"},
                    "quantity": "500 g\r\nCaixa\u200b",
                }
            }
        )
        cosmos = CosmosClient(
            token="token-ficticio",
            user_agent="agente-ficticio",
            session=_Session(cosmos_response),
        )
        cosmos_products = ProductService(
            database,
            external_client=types.SimpleNamespace(lookup=lambda _gtin: None),
            cosmos_client=cosmos,
        )
        looked_up = cosmos_products.lookup_external(cosmos_code, actor_id=admin["id"])
        self.assertEqual(looked_up["source"], "cosmos")
        self.assertEqual(looked_up["product"]["gtin"], cosmos_code)
        self.assertEqual(looked_up["product"]["nome"], "Café Minas Seguro")
        self.assertEqual(looked_up["product"]["marca"], "Pão Mineiro")

        with database.transaction() as connection:
            manual_row = connection.execute(
                "SELECT gtin, nome, marca, subcategoria, detalhes_embalagem, fonte_validacao "
                "FROM produtos WHERE gtin = ?",
                (manual_code,),
            ).fetchone()
            product_row = connection.execute(
                "SELECT gtin, nome, marca, detalhes_embalagem FROM produtos WHERE gtin = ?",
                (cosmos_code,),
            ).fetchone()
            cache_row = connection.execute(
                "SELECT gtin, nome, marca, detalhes_embalagem FROM cache_gtin WHERE gtin = ?",
                (cosmos_code,),
            ).fetchone()
        self.assertIsNotNone(manual_row)
        self.assertEqual(manual_row["gtin"], manual_code)
        self.assertEqual(manual_row["nome"], "Café  atualizado")
        self.assertEqual(manual_row["marca"], "Marca  nova Segura")
        self.assertEqual(manual_row["subcategoria"], "Fresco Hoje")
        self.assertEqual(manual_row["detalhes_embalagem"], "Pacote  500 g")
        self.assertEqual(manual_row["fonte_validacao"], "Cadastro  local")
        self.assert_safe_rendered_text(" ".join(str(value or "") for value in manual_row))
        for row in (product_row, cache_row):
            self.assertIsNotNone(row)
            self.assertEqual(row["gtin"], cosmos_code)
            self.assertEqual(row["nome"], "Café Minas Seguro")
            self.assertEqual(row["marca"], "Pão Mineiro")
            self.assertEqual(row["detalhes_embalagem"], "500 g Caixa")
            self.assert_safe_rendered_text(" ".join(str(value or "") for value in row))

    def test_cosmos_repeated_spaces_survive_client_database_and_cache(self) -> None:
        from integrations import cosmos as cosmos_module

        self.assertTrue(hasattr(cosmos_module, "_clean_text"))
        self.assertEqual(cosmos_module._clean_text("Café  Minas", 180), "Café  Minas")

        code = "7894900011517"
        response = _Response(
            {
                "product": {
                    "description": "Cafe\u0301  Minas\r\nSeguro\x1b",
                    "brand": "Pa\u0303o  Mineiro\x00",
                    "category": {"name": "Padaria"},
                    "quantity": "500  g\r\nCaixa\u200b",
                }
            }
        )
        cosmos = CosmosClient(
            token="token-ficticio",
            user_agent="agente-ficticio",
            session=_Session(response),
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "cosmos.sqlite3")
            database.initialize()
            admin = provision_test_admin(database)
            products = ProductService(
                database,
                external_client=types.SimpleNamespace(lookup=lambda _gtin: None),
                cosmos_client=cosmos,
            )
            result = products.lookup_external(code, actor_id=admin["id"])
            with database.transaction() as connection:
                product_row = connection.execute(
                    "SELECT gtin, nome, marca, detalhes_embalagem FROM produtos WHERE gtin = ?",
                    (code,),
                ).fetchone()
                cache_row = connection.execute(
                    "SELECT gtin, nome, marca, detalhes_embalagem FROM cache_gtin WHERE gtin = ?",
                    (code,),
                ).fetchone()

        self.assertEqual(result["product"]["gtin"], code)
        for row in (product_row, cache_row):
            self.assertIsNotNone(row)
            self.assertEqual(row["gtin"], code)
            self.assertEqual(row["nome"], "Café  Minas Seguro")
            self.assertEqual(row["marca"], "Pão  Mineiro")
            self.assertEqual(row["detalhes_embalagem"], "500  g Caixa")
            self.assert_safe_rendered_text(" ".join(str(value or "") for value in row))


if __name__ == "__main__":
    unittest.main()
