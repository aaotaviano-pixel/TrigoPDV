from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from db.database import Database, DatabaseError
from desktop_controller import DesktopController
from services import errors as service_errors
from services.auth import AuthService
from services.cash import CashService
from services.errors import AuthenticationError, AuthorizationError, ValidationError
from services.passwords import verify_password
from services.pdv_service import PDVService
from services.products import ProductService
from services.sales import SaleService
from services.security import get_active_user
from tests.support import TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD, provision_test_admin


class _MissingPasswordChangeRequiredError(AuthorizationError):
    pass


PASSWORD_CHANGE_REQUIRED = getattr(
    service_errors,
    "PasswordChangeRequiredError",
    _MissingPasswordChangeRequiredError,
)


class PasswordChangeRequiredTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "pdv.sqlite3")
        self.database.initialize()
        self.auth = AuthService(self.database)
        self.admin = provision_test_admin(self.database)
        self.cashier = self.auth.create_user(
            "Caixa com senha temporária",
            "caixa.troca",
            "SenhaTemporaria8",
            "caixa",
            actor_id=self.admin["id"],
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _stored_credentials(self, user_id: int) -> tuple[str, int]:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT senha_hash, deve_trocar_senha FROM usuarios WHERE id = ?",
                (user_id,),
            ).fetchone()
        return str(row["senha_hash"]), int(row["deve_trocar_senha"])

    def _cashier_service(self) -> PDVService:
        service = PDVService(database=self.database)
        user = service.authenticate("caixa.troca", "SenhaTemporaria8")
        self.assertIsNotNone(user)
        self.assertTrue(user["deve_trocar_senha"])
        return service

    def test_flagged_user_authenticates_but_default_active_user_guard_rejects(self) -> None:
        authenticated = self.auth.authenticate("caixa.troca", "SenhaTemporaria8")
        self.assertIsNotNone(authenticated)
        self.assertTrue(authenticated["deve_trocar_senha"])
        with self.database.transaction() as connection:
            with self.assertRaises(PASSWORD_CHANGE_REQUIRED):
                get_active_user(connection, self.cashier["id"])

    def test_flagged_session_cannot_use_commercial_or_administrative_operations(self) -> None:
        service = self._cashier_service()
        blocked_calls = (
            lambda: service.search_products("pão"),
            lambda: service.open_cash(self.cashier["id"], 10),
            lambda: service.get_pix_payload(10),
            lambda: service.list_audit_log(),
        )
        for call in blocked_calls:
            with self.subTest(call=call):
                with self.assertRaises(PASSWORD_CHANGE_REQUIRED):
                    call()

        with self.assertRaises(PASSWORD_CHANGE_REQUIRED):
            self.auth.get_user(self.cashier["id"], actor_id=self.cashier["id"])
        with self.assertRaises(PASSWORD_CHANGE_REQUIRED):
            CashService(self.database).open_cash(
                self.cashier["id"], 0, actor_id=self.cashier["id"]
            )
        with self.assertRaises(PASSWORD_CHANGE_REQUIRED):
            ProductService(self.database).lookup_external(
                "7891234567895", actor_id=self.cashier["id"]
            )

    def test_another_instance_sees_flag_immediately_instead_of_trusting_session(self) -> None:
        service = PDVService(database=self.database)
        authenticated = service.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)
        self.assertIsNotNone(authenticated)
        service.search_products("")
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE usuarios SET deve_trocar_senha = 1 WHERE id = ?",
                (self.admin["id"],),
            )
        with self.assertRaises(PASSWORD_CHANGE_REQUIRED):
            service.search_products("")

    def test_wrong_current_password_invalid_new_password_and_other_id_preserve_state(self) -> None:
        service = self._cashier_service()
        original = self._stored_credentials(self.cashier["id"])

        with self.assertRaises(AuthenticationError):
            service.change_password(
                self.cashier["id"], "SenhaAtualErrada8", "NovaSenhaSegura9"
            )
        self.assertEqual(self._stored_credentials(self.cashier["id"]), original)

        with self.assertRaises(ValidationError):
            service.change_password(self.cashier["id"], "SenhaTemporaria8", "curta")
        self.assertEqual(self._stored_credentials(self.cashier["id"]), original)

        with self.assertRaises(AuthorizationError):
            service.change_password(
                self.admin["id"], "SenhaTemporaria8", "NovaSenhaSegura9"
            )
        self.assertEqual(self._stored_credentials(self.cashier["id"]), original)

    def test_correct_own_password_change_clears_session_flag_and_unblocks_same_session(self) -> None:
        service = self._cashier_service()
        service.change_password(
            self.cashier["id"], "SenhaTemporaria8", "NovaSenhaSegura9"
        )
        self.assertFalse(service.current_user()["deve_trocar_senha"])
        service.search_products("")
        self.assertIsNone(self.auth.authenticate("caixa.troca", "SenhaTemporaria8"))
        self.assertIsNotNone(self.auth.authenticate("caixa.troca", "NovaSenhaSegura9"))

    def test_password_verification_update_flag_clear_and_audit_share_one_transaction(self) -> None:
        original_transaction = self.database.transaction
        opened_connections: list[int] = []
        write_modes: list[bool] = []

        @contextmanager
        def counted_transaction(*, write: bool = False):
            with original_transaction(write=write) as connection:
                opened_connections.append(id(connection))
                write_modes.append(write)
                yield connection

        with patch.object(self.database, "transaction", counted_transaction):
            updated = self.auth.change_password(
                self.cashier["id"],
                "SenhaTemporaria8",
                "NovaSenhaAtomica9",
                actor_id=self.cashier["id"],
            )
        self.assertEqual(len(opened_connections), 1)
        self.assertEqual(write_modes, [True])
        self.assertFalse(updated["deve_trocar_senha"])
        stored_hash, flag = self._stored_credentials(self.cashier["id"])
        self.assertTrue(verify_password("NovaSenhaAtomica9", stored_hash))
        self.assertEqual(flag, 0)

    def test_injected_update_failure_rolls_back_hash_flag_and_audit(self) -> None:
        before = self._stored_credentials(self.cashier["id"])
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "CREATE TRIGGER falha_troca_senha BEFORE UPDATE OF senha_hash ON usuarios "
                "BEGIN SELECT RAISE(ABORT, 'falha injetada'); END"
            )
        with self.assertRaises(DatabaseError):
            self.auth.change_password(
                self.cashier["id"],
                "SenhaTemporaria8",
                "NovaSenhaSegura9",
                actor_id=self.cashier["id"],
            )
        self.assertEqual(self._stored_credentials(self.cashier["id"]), before)
        with self.database.transaction() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM logs_auditoria "
                "WHERE entidade_id = ? AND acao = 'SENHA_ALTERADA'",
                (str(self.cashier["id"]),),
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_admin_with_pending_change_cannot_approve_or_call_auth_admin_operations(self) -> None:
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE usuarios SET deve_trocar_senha = 1 WHERE id = ?",
                (self.admin["id"],),
            )
        self.assertIsNone(
            self.auth.verify_admin_credentials(
                TEST_ADMIN_LOGIN,
                TEST_ADMIN_PASSWORD,
            )
        )
        with self.assertRaises(PASSWORD_CHANGE_REQUIRED):
            self.auth.create_user(
                "Outra pessoa", "outra.pessoa", "SenhaSegura8", "caixa", actor_id=self.admin["id"]
            )

    def test_flagged_requester_cannot_validate_an_admin_credential_directly(self) -> None:
        with self.assertRaises(PASSWORD_CHANGE_REQUIRED):
            self.auth.verify_admin_credentials(
                TEST_ADMIN_LOGIN,
                TEST_ADMIN_PASSWORD,
                requested_by=self.cashier["id"],
            )

    def test_admin_reset_is_seen_by_existing_session_and_blocks_sale_close_and_approval(self) -> None:
        ready = self.auth.change_password(
            self.cashier["id"],
            "SenhaTemporaria8",
            "SenhaDefinitiva9",
            actor_id=self.cashier["id"],
        )
        service = PDVService(database=self.database)
        self.assertIsNotNone(service.authenticate("caixa.troca", "SenhaDefinitiva9"))
        product = ProductService(self.database).create_product(
            "7891234567895", "Produto", 5, actor_id=self.admin["id"]
        )
        cash = CashService(self.database).open_cash(ready["id"], 0, actor_id=ready["id"])

        self.auth.reset_user_password(
            ready["id"], "OutraTemporaria8", actor_id=self.admin["id"]
        )
        blocked_calls = (
            lambda: service.finalize_sale(
                cash["id"],
                ready["id"],
                [{"gtin": product["gtin"], "quantidade": 1, "preco_unitario": 5}],
                "Cartão",
            ),
            lambda: service.close_cash(cash["id"], ready["id"], 0),
            lambda: service.admin_approve_and_price(
                ready["id"],
                product["gtin"],
                6,
                TEST_ADMIN_LOGIN,
                TEST_ADMIN_PASSWORD,
            ),
        )
        for call in blocked_calls:
            with self.subTest(call=call):
                with self.assertRaises(PASSWORD_CHANGE_REQUIRED):
                    call()

    def test_flagged_admin_cannot_authorize_item_cancellation_directly_or_through_facade(self) -> None:
        ready = self.auth.change_password(
            self.cashier["id"],
            "SenhaTemporaria8",
            "SenhaDefinitiva9",
            actor_id=self.cashier["id"],
        )
        service = PDVService(database=self.database)
        self.assertIsNotNone(service.authenticate("caixa.troca", "SenhaDefinitiva9"))
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE usuarios SET deve_trocar_senha = 1 WHERE id = ?",
                (self.admin["id"],),
            )

        calls = (
            lambda: SaleService(self.database).authorize_item_cancellation(
                {"gtin": "7891234567895", "nome": "Item"},
                operador_id=ready["id"],
                admin_login=TEST_ADMIN_LOGIN,
                admin_senha=TEST_ADMIN_PASSWORD,
            ),
            lambda: service.authorize_item_cancel(
                ready["id"],
                {"gtin": "7891234567895", "nome": "Item"},
                admin_login=TEST_ADMIN_LOGIN,
                admin_password=TEST_ADMIN_PASSWORD,
            ),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(AuthorizationError):
                    call()
        with self.database.transaction() as connection:
            rejected = connection.execute(
                "SELECT COUNT(*) FROM logs_auditoria WHERE acao = 'CANCELAMENTO_ITEM_REJEITADO'"
            ).fetchone()[0]
            approved = connection.execute(
                "SELECT COUNT(*) FROM logs_auditoria WHERE acao = 'ITEM_CARRINHO_CANCELADO'"
            ).fetchone()[0]
        self.assertEqual(rejected, 2)
        self.assertEqual(approved, 0)

        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE usuarios SET deve_trocar_senha = 0 WHERE id = ?",
                (self.admin["id"],),
            )
        authorized = SaleService(self.database).authorize_item_cancellation(
            {"gtin": "7891234567895", "nome": "Item"},
            operador_id=ready["id"],
            admin_login=TEST_ADMIN_LOGIN,
            admin_senha=TEST_ADMIN_PASSWORD,
        )
        self.assertTrue(authorized["authorized"])
        self.assertEqual(authorized["admin_id"], self.admin["id"])

    def test_printer_actions_revalidate_admin_in_database(self) -> None:
        service = PDVService(database=self.database)
        controller = DesktopController(service)
        self.assertIsNotNone(controller.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE usuarios SET deve_trocar_senha = 1 WHERE id = ?",
                (self.admin["id"],),
            )
        for call in (
            controller.list_printers,
            controller.printer_configuration,
            lambda: controller.save_printer_selection(""),
            controller.test_printer,
        ):
            with self.subTest(call=call):
                with self.assertRaises(PASSWORD_CHANGE_REQUIRED):
                    call()

    def test_printer_save_revalidates_after_enumeration_before_persisting(self) -> None:
        service = PDVService(database=self.database)
        settings = service.settings
        controller = DesktopController(service, settings)
        self.assertIsNotNone(controller.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))

        def flag_admin() -> None:
            with self.database.transaction(write=True) as connection:
                connection.execute(
                    "UPDATE usuarios SET deve_trocar_senha = 1 WHERE id = ?",
                    (self.admin["id"],),
                )

        persisted = Mock()

        def enumerate_then_flag() -> list[dict]:
            flag_admin()
            return [{"name": "Impressora Mock", "available": True, "status": "Disponível"}]

        with patch("desktop_controller.list_windows_printers", side_effect=enumerate_then_flag), patch(
            "desktop_controller.save_printer_settings", persisted
        ):
            with self.assertRaises(PASSWORD_CHANGE_REQUIRED):
                controller.save_printer_selection("Impressora Mock")
        persisted.assert_not_called()

    def test_printer_test_revalidates_immediately_before_spooler(self) -> None:
        service = PDVService(database=self.database)
        settings = replace(service.settings, printer_name="Impressora Mock", printer_enabled=True)
        controller = DesktopController(service, settings)
        self.assertIsNotNone(controller.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))

        def flag_admin() -> None:
            with self.database.transaction(write=True) as connection:
                connection.execute(
                    "UPDATE usuarios SET deve_trocar_senha = 1 WHERE id = ?",
                    (self.admin["id"],),
                )

        printed = Mock()
        controller.printer.print_receipt = printed
        real_require_admin = controller._require_admin
        checks = 0

        def first_guard_then_flag() -> dict:
            nonlocal checks
            actor = real_require_admin()
            checks += 1
            if checks == 1:
                flag_admin()
            return actor

        with patch.object(controller, "_require_admin", side_effect=first_guard_then_flag):
            with self.assertRaises(PASSWORD_CHANGE_REQUIRED):
                controller.test_printer()
        self.assertEqual(checks, 1)
        printed.assert_not_called()

    def test_logout_remains_available_and_rejected_attempt_audit_has_no_secret(self) -> None:
        service = self._cashier_service()
        rejected_password = "SenhaAtualErrada8"
        new_password = "NovaSenhaSegura9"
        with self.assertRaises(AuthenticationError):
            service.change_password(self.cashier["id"], rejected_password, new_password)
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT acao, detalhes FROM logs_auditoria WHERE entidade_id = ?",
                (str(self.cashier["id"]),),
            ).fetchall()
        serialized = " ".join(str(value) for row in rows for value in row)
        self.assertNotIn(rejected_password, serialized)
        self.assertNotIn(new_password, serialized)
        self.assertNotIn(self._stored_credentials(self.cashier["id"])[0], serialized)
        service.logout()
        self.assertIsNone(service.current_user())


if __name__ == "__main__":
    unittest.main()
