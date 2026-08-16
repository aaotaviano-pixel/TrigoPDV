from __future__ import annotations

import sqlite3
import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal, ROUND_HALF_UP, getcontext, localcontext
from random import Random

from services.checkout import CheckoutLine, LineKind, SaleQuote, quote_lines
from services.errors import ValidationError
from services.money import money


class CheckoutQuoteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            "CREATE TABLE produtos ("
            "gtin TEXT PRIMARY KEY, nome TEXT NOT NULL, preco REAL NOT NULL, "
            "unidade TEXT NOT NULL, ativo INTEGER NOT NULL)"
        )
        self.connection.executemany(
            "INSERT INTO produtos(gtin, nome, preco, unidade, ativo) VALUES (?, ?, ?, ?, ?)",
            (
                ("7891234567895", "Biscoito por peso", 12.50, "KG", 1),
                ("7894900011517", "Refrigerante", 10.00, "UN", 1),
            ),
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_rounds_each_line_half_up_before_adding_totals(self) -> None:
        quote = quote_lines(
            self.connection,
            [{"gtin": "7891234567895", "quantidade": "0.250", "preco_unitario": "12.50"}],
        )

        self.assertEqual(quote.lines[0].subtotal, Decimal("3.13"))
        self.assertEqual(quote.total, Decimal("3.13"))

    def test_rejects_positive_subtotal_that_rounds_to_zero_for_manual_and_catalog(self) -> None:
        self.assertEqual(money(0), Decimal("0.00"))
        self.connection.execute(
            "INSERT INTO produtos(gtin, nome, preco, unidade, ativo) VALUES (?, ?, ?, ?, ?)",
            ("MICRO-CATALOG", "Produto de centavo", 0.01, "KG", 1),
        )
        manual = {
            "tipo_lancamento": "MANUAL",
            "descricao": "Item avulso de centavo",
            "unidade": "KG",
            "quantidade": "0.001",
            "preco_unitario": "0.01",
        }
        catalog = {
            "tipo_lancamento": "CATALOGO",
            "gtin": "MICRO-CATALOG",
            "quantidade": "0.001",
        }

        for item in (manual, catalog):
            with self.subTest(kind=item["tipo_lancamento"]), self.assertRaises(ValidationError):
                quote_lines(self.connection, [item])

    def test_large_exact_product_keeps_the_half_up_cent(self) -> None:
        quote = quote_lines(
            self.connection,
            [
                {
                    "tipo_lancamento": "MANUAL",
                    "descricao": "Carga decimal",
                    "unidade": "KG",
                    "quantidade": "104946.389",
                    "preco_unitario": "359999772457008772205.00",
                }
            ],
        )

        self.assertEqual(quote.lines[0].subtotal, Decimal("37780676160184728384238317.75"))
        self.assertEqual(quote.total, Decimal("37780676160184728384238317.75"))

    def test_quote_is_independent_from_global_decimal_precision(self) -> None:
        item = {
            "tipo_lancamento": "MANUAL",
            "descricao": "Carga decimal",
            "unidade": "KG",
            "quantidade": "104946.389",
            "preco_unitario": "359999772457008772205.00",
        }
        original_precision = getcontext().prec
        try:
            getcontext().prec = 6
            low_precision = quote_lines(self.connection, [item])
            getcontext().prec = 100
            high_precision = quote_lines(self.connection, [item])
        finally:
            getcontext().prec = original_precision

        expected = Decimal("37780676160184728384238317.75")
        self.assertEqual(low_precision.total, expected)
        self.assertEqual(high_precision.total, expected)
        self.assertEqual(low_precision.to_payload(), high_precision.to_payload())

    def test_sum_of_many_large_lines_is_exact(self) -> None:
        price = "999999999999999999999999.99"
        items = [self._manual_item(price) for _ in range(1000)]

        quote = quote_lines(self.connection, items)

        self.assertEqual(quote.total, Decimal("999999999999999999999999990.00"))
        self.assertEqual(quote.manual_total, quote.total)

    def test_rejects_pathological_numeric_exponent_with_structural_error(self) -> None:
        with self.assertRaisesRegex(ValidationError, "muito extenso"):
            quote_lines(self.connection, [self._manual_item("1e1000000")])

    def test_deterministic_decimal_fuzz_matches_high_precision_reference(self) -> None:
        random = Random(20260815)
        for _ in range(500):
            price_cents = random.randint(1, 10**12)
            quantity_millis = random.randint(1, 10**9)
            price = f"{price_cents // 100}.{price_cents % 100:02d}"
            amount = f"{quantity_millis // 1000}.{quantity_millis % 1000:03d}"
            quote = quote_lines(
                self.connection,
                [
                    {
                        "tipo_lancamento": "MANUAL",
                        "descricao": "Item fuzz",
                        "unidade": "KG",
                        "quantidade": amount,
                        "preco_unitario": price,
                    }
                ],
            )
            with localcontext() as context:
                context.prec = 100
                expected = (Decimal(price) * Decimal(amount)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            with self.subTest(price=price, amount=amount):
                self.assertEqual(quote.total, expected)

    def test_rejects_fractional_unit_and_accepts_kg_with_three_decimals(self) -> None:
        with self.assertRaises(ValidationError):
            quote_lines(
                self.connection,
                [{"gtin": "7894900011517", "quantidade": "1.500"}],
            )

        quote = quote_lines(
            self.connection,
            [{"gtin": "7891234567895", "quantidade": "1.234"}],
        )
        self.assertEqual(quote.lines[0].quantity, Decimal("1.234"))

        with self.assertRaises(ValidationError):
            quote_lines(
                self.connection,
                [{"gtin": "7891234567895", "quantidade": "1.2345"}],
            )

    def test_rejects_sub_milligram_quantity_instead_of_rounding_to_zero(self) -> None:
        with self.assertRaises(ValidationError):
            quote_lines(
                self.connection,
                [{"gtin": "7891234567895", "quantidade": "0.0004"}],
            )

    def test_rejects_non_finite_boolean_and_invalid_numeric_inputs(self) -> None:
        invalid_values = ("NaN", "Infinity", "-Infinity", True, False, "não é número")
        for invalid in invalid_values:
            with self.subTest(quantity=invalid), self.assertRaises(ValidationError):
                quote_lines(
                    self.connection,
                    [{"gtin": "7891234567895", "quantidade": invalid}],
                )
            with self.subTest(price=invalid), self.assertRaises(ValidationError):
                quote_lines(
                    self.connection,
                    [
                        {
                            "tipo_lancamento": "MANUAL",
                            "descricao": "Item avulso",
                            "unidade": "KG",
                            "quantidade": "1.000",
                            "preco_unitario": invalid,
                        }
                    ],
                )

    def test_requires_strictly_positive_price_and_quantity(self) -> None:
        for invalid_quantity in ("0", "-1"):
            with self.subTest(quantity=invalid_quantity), self.assertRaises(ValidationError):
                quote_lines(
                    self.connection,
                    [{"gtin": "7894900011517", "quantidade": invalid_quantity}],
                )
        for invalid_price in ("0", "-0.01", "0.004"):
            with self.subTest(price=invalid_price), self.assertRaises(ValidationError):
                quote_lines(
                    self.connection,
                    [
                        {
                            "tipo_lancamento": "MANUAL",
                            "descricao": "Item avulso",
                            "unidade": "UN",
                            "quantidade": "1",
                            "preco_unitario": invalid_price,
                        }
                    ],
                )

    def test_classifies_catalog_manual_and_catalog_price_exception(self) -> None:
        quote = quote_lines(
            self.connection,
            [
                {"gtin": "7894900011517", "quantidade": "1"},
                {
                    "tipo_lancamento": "MANUAL",
                    "descricao": "Fatia de bolo",
                    "codigo_informado": "BALCAO-01",
                    "unidade": "UN",
                    "quantidade": "2",
                    "preco_unitario": "7.50",
                },
                {
                    "tipo_lancamento": "CATALOGO",
                    "gtin": "7891234567895",
                    "quantidade": "0.250",
                    "preco_unitario": "10.00",
                },
            ],
        )

        catalog, manual, exceptional = quote.lines
        self.assertIs(catalog.kind, LineKind.CATALOG)
        self.assertFalse(catalog.has_price_exception)
        self.assertIs(manual.kind, LineKind.MANUAL)
        self.assertIsNone(manual.gtin)
        self.assertEqual(manual.entered_code, "BALCAO-01")
        self.assertTrue(exceptional.has_price_exception)
        self.assertEqual(exceptional.original_price, Decimal("12.50"))
        self.assertEqual(quote.total, Decimal("27.50"))
        self.assertEqual(quote.manual_total, Decimal("15.00"))
        self.assertEqual(quote.manual_line_count, 1)
        self.assertEqual(quote.price_exception_count, 1)
        self.assertTrue(quote.requires_authorization)
        self.assertIn("PRECO_EXCEPCIONAL", quote.authorization_reasons)

    def test_manual_limit_is_aggregate_and_uses_exact_money_boundary(self) -> None:
        at_limit = quote_lines(
            self.connection,
            [
                self._manual_item("30.00"),
                self._manual_item("20.00"),
            ],
        )
        self.assertEqual(at_limit.manual_total, Decimal("50.00"))
        self.assertFalse(at_limit.requires_authorization)

        above_limit = quote_lines(
            self.connection,
            [
                self._manual_item("30.00"),
                self._manual_item("20.01"),
            ],
        )
        self.assertEqual(above_limit.manual_total, Decimal("50.01"))
        self.assertTrue(above_limit.requires_authorization)
        self.assertIn("TOTAL_MANUAL_ACIMA_LIMITE", above_limit.authorization_reasons)

    def test_payload_uses_canonical_decimal_strings_and_immutable_lines(self) -> None:
        quote = quote_lines(
            self.connection,
            [
                {"gtin": "7891234567895", "quantidade": "0,250", "preco_unitario": "12,50"},
                self._manual_item("2.5", quantity="2"),
            ],
        )

        catalog_payload = quote.lines[0].to_payload()
        manual_payload = quote.lines[1].to_payload()
        quote_payload = quote.to_payload()
        self.assertEqual(catalog_payload["quantidade"], "0.250")
        self.assertEqual(catalog_payload["preco_unitario"], "12.50")
        self.assertEqual(catalog_payload["preco_original"], "12.50")
        self.assertEqual(catalog_payload["subtotal"], "3.13")
        self.assertEqual(manual_payload["quantidade"], "2")
        self.assertEqual(manual_payload["preco_unitario"], "2.50")
        self.assertEqual(manual_payload["subtotal"], "5.00")
        self.assertEqual(quote_payload["total"], "8.13")
        self.assertEqual(quote_payload["total_manual"], "5.00")
        self.assertTrue(all(isinstance(item["quantidade"], str) for item in quote_payload["itens"]))

        with self.assertRaises(FrozenInstanceError):
            quote.lines[0].subtotal = Decimal("0.00")
        with self.assertRaises(FrozenInstanceError):
            quote.total = Decimal("0.00")
        self.assertIsInstance(quote, SaleQuote)
        self.assertIsInstance(quote.lines[0], CheckoutLine)

    @staticmethod
    def _manual_item(price: str, *, quantity: str = "1") -> dict:
        return {
            "tipo_lancamento": "MANUAL",
            "descricao": "Item avulso",
            "unidade": "UN",
            "quantidade": quantity,
            "preco_unitario": price,
        }


if __name__ == "__main__":
    unittest.main()
