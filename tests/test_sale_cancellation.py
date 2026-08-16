"""Cancelamento local idempotente, sem fingir estorno bancário."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from db.database import Database
from services.errors import AuthorizationError, ConflictError
from services.sales import SaleService


class SaleCancellationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "cancel.sqlite3")
        self.database.initialize()
        with self.database.transaction(write=True) as connection:
            connection.executemany(
                "INSERT INTO usuarios(id, nome, login, senha_hash, perfil, ativo) "
                "VALUES (?, ?, ?, 'hash', ?, 1)",
                ((1, "Admin", "admin", "admin"), (2, "Caixa", "caixa", "caixa")),
            )
            connection.execute(
                "INSERT INTO produtos(gtin, nome, preco, estoque, unidade, estoque_controlado, ativo) "
                "VALUES ('7891234567895', 'Farinha', 10, 10, 'UN', 1, 1)"
            )
            connection.execute(
                "INSERT INTO caixas(id, usuario_id, fundo_inicial, status) VALUES (1, 2, 20, 'ABERTO')"
            )
        self.sales = SaleService(self.database)

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    def _sale(self, payment: str = "PIX") -> dict:
        safe_payment = {"PIX": "PIX", "Cartão": "CARTAO", "Dinheiro": "DINHEIRO"}[payment]
        return self.sales.finalize(
            1,
            [
                {"gtin": "7891234567895", "quantidade": "2"},
                {
                    "tipo_lancamento": "MANUAL",
                    "descricao": "Item sem estoque",
                    "unidade": "UN",
                    "quantidade": "1",
                    "preco_unitario": "5.00",
                },
            ],
            payment,
            operador_id=2,
            chave_idempotencia=f"SALE-CANCEL-{safe_payment}",
        )

    def test_cancel_restores_catalog_stock_once_and_warns_about_financial_refund(self) -> None:
        sale = self._sale("PIX")
        self.assertEqual(
            self.database.fetch_one(
                "SELECT estoque FROM produtos WHERE gtin = '7891234567895'"
            )["estoque"],
            8,
        )

        cancellation = self.sales.cancel_sale(
            sale["id"],
            operator_id=2,
            authorizer_id=1,
            reason="Cliente devolveu os itens antes de sair",
            idempotency_key="CANCELAMENTO-0001",
        )

        self.assertEqual(cancellation["sale_status"], "CANCELADA")
        self.assertIn("não realiza estorno", cancellation["financial_warning"].lower())
        self.assertEqual(
            self.database.fetch_one(
                "SELECT estoque FROM produtos WHERE gtin = '7891234567895'"
            )["estoque"],
            10,
        )
        replay = self.sales.cancel_sale(
            sale["id"],
            operator_id=2,
            authorizer_id=1,
            reason="Cliente devolveu os itens antes de sair",
            idempotency_key="CANCELAMENTO-0001",
        )
        self.assertEqual(replay["id"], cancellation["id"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(
            self.database.fetch_one(
                "SELECT estoque FROM produtos WHERE gtin = '7891234567895'"
            )["estoque"],
            10,
        )

    def test_changed_retry_and_non_admin_authorizer_are_rejected(self) -> None:
        sale = self._sale("Cartão")
        self.sales.cancel_sale(
            sale["id"],
            operator_id=2,
            authorizer_id=1,
            reason="Cancelamento conferido pela administração",
            idempotency_key="CANCELAMENTO-0002",
        )
        with self.assertRaises(ConflictError):
            self.sales.cancel_sale(
                sale["id"],
                operator_id=2,
                authorizer_id=1,
                reason="Motivo diferente informado depois",
                idempotency_key="CANCELAMENTO-0002",
            )

        second_sale = self.sales.finalize(
            1,
            [{"gtin": "7891234567895", "quantidade": "1"}],
            "Dinheiro",
            "10.00",
            operador_id=2,
            chave_idempotencia="SALE-CANCEL-SECOND",
        )
        with self.assertRaises(AuthorizationError):
            self.sales.cancel_sale(
                second_sale["id"],
                operator_id=2,
                authorizer_id=2,
                reason="Tentativa feita sem administrador válido",
                idempotency_key="CANCELAMENTO-0003",
            )

    def test_closed_cash_refuses_cancellation_without_changing_stock(self) -> None:
        sale = self._sale("PIX")
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE caixas SET status = 'FECHADO', data_fechamento = CURRENT_TIMESTAMP WHERE id = 1"
            )
        before = self.database.fetch_one(
            "SELECT estoque FROM produtos WHERE gtin = '7891234567895'"
        )["estoque"]

        with self.assertRaises(ConflictError):
            self.sales.cancel_sale(
                sale["id"],
                operator_id=2,
                authorizer_id=1,
                reason="Tentativa após o fechamento do caixa",
                idempotency_key="CANCELAMENTO-0004",
            )

        self.assertEqual(
            self.database.fetch_one(
                "SELECT estoque FROM produtos WHERE gtin = '7891234567895'"
            )["estoque"],
            before,
        )
        self.assertEqual(
            self.database.fetch_one("SELECT status FROM vendas WHERE id = ?", (sale["id"],))["status"],
            "CONFIRMADA",
        )


if __name__ == "__main__":
    unittest.main()
