"""Contratos do carrinho decimal, sem abrir banco ou hardware real."""

from __future__ import annotations

import unittest
from decimal import Decimal

from ui.views import CartLine


class CartLineDecimalTestCase(unittest.TestCase):
    def test_visual_subtotal_and_payload_use_the_same_canonical_decimals(self) -> None:
        line = CartLine(
            key=1,
            gtin="7891234567895",
            name="Produto por peso",
            price="12.50",
            quantity="0.250",
            unit="KG",
        )

        self.assertEqual(line.subtotal, Decimal("3.13"))
        self.assertEqual(
            line.as_payload(),
            {
                "tipo_lancamento": "CATALOGO",
                "gtin": "7891234567895",
                "codigo_informado": "7891234567895",
                "quantidade": "0.250",
                "preco_unitario": "12.50",
                "nome": "Produto por peso",
                "unidade": "KG",
            },
        )

    def test_manual_line_has_no_catalog_gtin(self) -> None:
        line = CartLine(
            key=2,
            gtin=None,
            entered_code="BALCAO-01",
            name="Fatia de bolo",
            price="7.50",
            quantity="2",
            unit="UN",
            line_kind="MANUAL",
        )

        self.assertEqual(line.subtotal, Decimal("15.00"))
        payload = line.as_payload()
        self.assertIsNone(payload["gtin"])
        self.assertEqual(payload["tipo_lancamento"], "MANUAL")
        self.assertEqual(payload["descricao"], "Fatia de bolo")


if __name__ == "__main__":
    unittest.main()
