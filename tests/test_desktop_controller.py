from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from db.database import Database
from desktop_controller import DesktopController
from services.pdv_service import PDVService
from services.errors import AuthorizationError
from tests.support import (
    TEST_ADMIN_LOGIN,
    TEST_ADMIN_PASSWORD,
    TEST_RECOVERY_CODE,
    provision_test_admin,
)


class DesktopControllerTestCase(unittest.TestCase):
    """Smoke test for the UI adapter, including safe post-commit printing."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.controller = DesktopController(PDVService(database=Database(self.root / "pdv.sqlite3")))
        provision_test_admin(self.controller.service.database)
        self.admin = self.controller.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)
        assert self.admin is not None

    def tearDown(self) -> None:
        self.controller.shutdown()
        self.tempdir.cleanup()

    def test_sale_is_queued_once_when_printer_is_disabled(self) -> None:
        product = self.controller.create_product(
            {
                "gtin": "7891234567895",
                "nome": "Pão Francês",
                "preco": "15,00",
                "estoque": "5,000",
                "unidade": "KG",
                "item_balcao": True,
                "estoque_controlado": True,
            },
            self.admin["id"],
        )
        cash = self.controller.open_cash(self.admin["id"], "20,00")
        sale = self.controller.finalize_sale(
            cash["id"],
            self.admin["id"],
            [{"gtin": product["gtin"], "nome": product["nome"], "quantidade": "0,200", "preco_unitario": "15,00"}],
            "Dinheiro",
            "5,00",
            chave_idempotencia="desktop-controller-001",
        )
        self.assertEqual(sale["total"], 3.0)
        self.assertFalse(sale["printed"])
        self.assertIn("Impressão desativada", sale["print_warning"])
        queued_before = list((self.root / "data" / "print_queue").glob("*.txt"))
        self.assertEqual(len(queued_before), 0)
        with self.controller.service.database.transaction() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM impressao_outbox").fetchone()[0], 0)

        replay = self.controller.finalize_sale(
            cash["id"],
            self.admin["id"],
            [{"gtin": product["gtin"], "nome": product["nome"], "quantidade": "0,200", "preco_unitario": "15,00"}],
            "Dinheiro",
            "5,00",
            chave_idempotencia="desktop-controller-001",
        )
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(len(list((self.root / "data" / "print_queue").glob("*.txt"))), 0)

    def test_admin_can_prepare_printer_test_without_sale(self) -> None:
        result = self.controller.test_printer()
        self.assertFalse(result["printed"])
        self.assertIn("Impressão desativada", result["message"])
        self.assertIn("Teste de impressão", result["receipt_text"])

    def test_admin_user_creation_is_available_from_controller(self) -> None:
        account = self.controller.create_user_admin(
            "Caixa Dois", "caixa-dois", "SenhaSegura8", "caixa", self.admin["id"]
        )
        self.assertEqual(account["perfil"], "caixa")
        self.assertTrue(account["deve_trocar_senha"])
        self.assertEqual(len(self.controller.admin_users()), 2)

    def test_admin_can_reset_another_users_password(self) -> None:
        account = self.controller.create_user_admin(
            "Caixa Esqueceu", "caixa-esqueceu", "SenhaInicial8", "caixa", self.admin["id"]
        )
        reset = self.controller.reset_user_password_admin(account["id"], "SenhaTemporaria9", self.admin["id"])
        self.assertTrue(reset["deve_trocar_senha"])
        self.assertIsNotNone(self.controller.authenticate("caixa-esqueceu", "SenhaTemporaria9"))

    def test_controller_exposes_admin_recovery_after_five_errors(self) -> None:
        for _ in range(5):
            self.assertIsNone(self.controller.authenticate(TEST_ADMIN_LOGIN, "senha-incorreta"))
        self.assertTrue(self.controller.password_recovery_available(TEST_ADMIN_LOGIN))
        self.controller.recover_password_with_code(
            TEST_ADMIN_LOGIN, TEST_RECOVERY_CODE, "NovaSenhaSegura9", "novo-codigo-seguro"
        )
        self.assertIsNotNone(self.controller.authenticate(TEST_ADMIN_LOGIN, "NovaSenhaSegura9"))

    def test_quote_and_typed_manual_authorization_never_persist_credentials(self) -> None:
        cashier = self.controller.create_user_admin(
            "Operadora Checkout", "checkout-caixa", "SenhaInicial8", "caixa", self.admin["id"]
        )
        self.controller.authenticate("checkout-caixa", "SenhaInicial8")
        self.controller.change_password(cashier["id"], "SenhaInicial8", "SenhaDefinitiva9")
        cash = self.controller.open_cash(cashier["id"], "20.00")
        items = [
            {
                "tipo_lancamento": "MANUAL",
                "descricao": "Encomenda avulsa",
                "unidade": "UN",
                "quantidade": "1",
                "preco_unitario": "50.01",
            }
        ]

        quote = self.controller.quote_sale(cashier["id"], items)
        self.assertTrue(quote["requer_autorizacao"])
        self.assertEqual(quote["total"], "50.01")

        with self.assertRaises(AuthorizationError):
            self.controller.finalize_sale(
                cash["id"],
                cashier["id"],
                items,
                "PIX",
                chave_idempotencia="AUTH-CONTROLLER-001",
                manual_authorization={
                    "login": TEST_ADMIN_LOGIN,
                    "password": "senha-incorreta",
                    "reason": "Venda conferida pela administração",
                },
            )

        sale = self.controller.finalize_sale(
            cash["id"],
            cashier["id"],
            items,
            "PIX",
            chave_idempotencia="AUTH-CONTROLLER-001",
            manual_authorization={
                "login": TEST_ADMIN_LOGIN,
                "password": TEST_ADMIN_PASSWORD,
                "reason": "Venda conferida pela administração",
            },
        )
        self.assertEqual(sale["total"], 50.01)

        with self.assertRaises(AuthorizationError):
            self.controller.finalize_sale(
                cash["id"],
                cashier["id"],
                items,
                "PIX",
                chave_idempotencia="AUTH-CONTROLLER-001",
                manual_authorization={
                    "login": TEST_ADMIN_LOGIN,
                    "password": "senha-incorreta",
                    "reason": "Venda conferida pela administração",
                },
            )

        with self.controller.service.database.transaction() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM vendas").fetchone()[0], 1)
            serialized = "\n".join(
                str(row[0] or "")
                for row in connection.execute("SELECT detalhes FROM logs_auditoria")
            )
        self.assertNotIn(TEST_ADMIN_PASSWORD, serialized)


if __name__ == "__main__":
    unittest.main()
