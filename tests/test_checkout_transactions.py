"""Venda normal, manual e excepcional dentro de uma única transação."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from db.database import Database
from services.errors import (
    AuthorizationError,
    AuthorizationRequiredError,
    ConflictError,
    ValidationError,
)
from services.sales import SaleService


class CheckoutTransactionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "checkout.sqlite3")
        self.database.initialize()
        with self.database.transaction(write=True) as connection:
            connection.executemany(
                "INSERT INTO usuarios(id, nome, login, senha_hash, perfil, ativo) "
                "VALUES (?, ?, ?, 'hash-de-teste', ?, 1)",
                (
                    (1, "Administradora", "admin", "admin"),
                    (2, "Operadora", "caixa", "caixa"),
                    (3, "Outro caixa", "caixa2", "caixa"),
                ),
            )
            connection.executemany(
                "INSERT INTO produtos(gtin, nome, preco, estoque, unidade, estoque_controlado, ativo) "
                "VALUES (?, ?, ?, ?, ?, 1, 1)",
                (
                    ("7891234567895", "Farinha", 12.50, 10, "UN"),
                    ("7894900011517", "Refrigerante", 10.00, 10, "UN"),
                ),
            )
            connection.execute(
                "INSERT INTO caixas(id, usuario_id, fundo_inicial, status) VALUES (1, 2, 50, 'ABERTO')"
            )
        self.sales = SaleService(self.database)

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    @staticmethod
    def _manual(price: str, *, description: str = "Item avulso") -> dict:
        return {
            "tipo_lancamento": "MANUAL",
            "descricao": description,
            "unidade": "UN",
            "quantidade": "1",
            "preco_unitario": price,
        }

    def test_mixed_sale_persists_manual_trace_without_touching_manual_stock(self) -> None:
        sale = self.sales.finalize(
            1,
            [
                {"gtin": "7891234567895", "quantidade": "1"},
                self._manual("5.00", description="Pão artesanal sem código"),
                self._manual("5.00", description="Bolo vendido no balcão"),
            ],
            "Dinheiro",
            "25.00",
            operador_id=2,
            chave_idempotencia="VENDA-MISTA-001",
        )

        self.assertEqual(sale["total"], 22.50)
        self.assertEqual(sale["total_manual"], 10.00)
        self.assertIsNone(sale["autorizador_excecao_id"])
        with self.database.transaction() as connection:
            stock = connection.execute(
                "SELECT estoque FROM produtos WHERE gtin = '7891234567895'"
            ).fetchone()[0]
            items = connection.execute(
                "SELECT gtin, tipo_lancamento, nome_produto FROM itens_venda "
                "WHERE venda_id = ? ORDER BY id",
                (sale["id"],),
            ).fetchall()
            actions = [
                str(row[0])
                for row in connection.execute(
                    "SELECT acao FROM logs_auditoria WHERE entidade_id = ? ORDER BY id",
                    (str(sale["id"]),),
                )
            ]
        self.assertEqual(stock, 9)
        self.assertEqual([row["tipo_lancamento"] for row in items], ["CATALOGO", "MANUAL", "MANUAL"])
        self.assertIsNone(items[1]["gtin"])
        self.assertIn("ITEM_MANUAL_VENDIDO", actions)
        self.assertIn("VENDA_CONFIRMADA", actions)

    def test_aggregate_manual_limit_and_price_exception_require_admin_and_reason(self) -> None:
        above_limit = [self._manual("30.00"), self._manual("20.01")]
        with self.assertRaises(AuthorizationRequiredError) as captured:
            self.sales.finalize(
                1, above_limit, "PIX", operador_id=2, chave_idempotencia="MANUAL-ACIMA-001"
            )
        self.assertIn("TOTAL_MANUAL_ACIMA_LIMITE", captured.exception.requirement.reasons)
        self.assertEqual(captured.exception.requirement.manual_total, "50.01")

        with self.assertRaises(ValidationError):
            self.sales.finalize(
                1,
                above_limit,
                "PIX",
                operador_id=2,
                chave_idempotencia="MANUAL-ACIMA-001",
                exception_authorizer_id=1,
                exception_reason="curto",
            )

        manual_sale = self.sales.finalize(
            1,
            above_limit,
            "PIX",
            operador_id=2,
            chave_idempotencia="MANUAL-ACIMA-001",
            exception_authorizer_id=1,
            exception_reason="Venda avulsa conferida no balcão",
        )
        self.assertEqual(manual_sale["autorizador_excecao_id"], 1)

        exceptional_item = {
            "gtin": "7891234567895",
            "quantidade": "1",
            "preco_unitario": "10.00",
        }
        with self.assertRaises(AuthorizationRequiredError) as captured_price:
            self.sales.finalize(
                1,
                [exceptional_item],
                "Cartão",
                operador_id=2,
                chave_idempotencia="PRECO-EXCECAO-01",
            )
        self.assertIn("PRECO_EXCEPCIONAL", captured_price.exception.requirement.reasons)

        exceptional_sale = self.sales.finalize(
            1,
            [exceptional_item],
            "Cartão",
            operador_id=2,
            chave_idempotencia="PRECO-EXCECAO-01",
            exception_authorizer_id=1,
            exception_reason="Preço especial autorizado pela administração",
        )
        self.assertEqual(exceptional_sale["total"], 10.00)
        with self.database.transaction() as connection:
            stored_item = connection.execute(
                "SELECT preco_original, preco_unitario FROM itens_venda WHERE venda_id = ?",
                (exceptional_sale["id"],),
            ).fetchone()
            actions = {
                str(row[0])
                for row in connection.execute(
                    "SELECT acao FROM logs_auditoria WHERE entidade_id = ?",
                    (str(exceptional_sale["id"]),),
                )
            }
        self.assertEqual(stored_item["preco_original"], 12.50)
        self.assertEqual(stored_item["preco_unitario"], 10.00)
        self.assertIn("PRECO_EXCEPCIONAL_APLICADO", actions)
        self.assertIn("EXCECAO_VENDA_AUTORIZADA", actions)

    def test_non_admin_cannot_authorize_exception(self) -> None:
        with self.assertRaises(AuthorizationError):
            self.sales.finalize(
                1,
                [self._manual("50.01")],
                "PIX",
                operador_id=2,
                chave_idempotencia="AUTORIZA-INVALIDA",
                exception_authorizer_id=3,
                exception_reason="Tentativa com usuário sem permissão",
            )

    def test_exact_retry_replays_but_changed_fingerprint_conflicts(self) -> None:
        items = [
            {"gtin": "7891234567895", "quantidade": "1"},
            {"gtin": "7894900011517", "quantidade": "1"},
        ]
        original = self.sales.finalize(
            1,
            items,
            "Dinheiro",
            "30.00",
            operador_id=2,
            chave_idempotencia="RETRY-COMPLETO-01",
        )
        replay = self.sales.finalize(
            1,
            items,
            "Dinheiro",
            "30.00",
            operador_id=2,
            chave_idempotencia="RETRY-COMPLETO-01",
        )
        self.assertEqual(replay["id"], original["id"])
        self.assertTrue(replay["idempotent_replay"])

        variants = (
            ([items[1], items[0]], "Dinheiro", "30.00", 2),
            ([{"gtin": "7891234567895", "quantidade": "2"}, items[1]], "Dinheiro", "40.00", 2),
            (items, "PIX", None, 2),
            (items, "Dinheiro", "31.00", 2),
            (items, "Dinheiro", "30.00", 1),
        )
        for changed_items, payment, received, operator in variants:
            with self.subTest(payment=payment, received=received, operator=operator):
                with self.assertRaises(ConflictError):
                    self.sales.finalize(
                        1,
                        changed_items,
                        payment,
                        received,
                        operador_id=operator,
                        chave_idempotencia="RETRY-COMPLETO-01",
                    )

        with self.database.transaction() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM vendas").fetchone()[0], 1)
            self.assertEqual(
                connection.execute(
                    "SELECT estoque FROM produtos WHERE gtin = '7891234567895'"
                ).fetchone()[0],
                9,
            )


if __name__ == "__main__":
    unittest.main()
