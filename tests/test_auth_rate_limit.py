"""Bloqueios temporarios e recuperacao administrativa segura."""

from __future__ import annotations

import inspect
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db.database import Database
from services.auth import AuthService
from services.errors import (
    AuthenticationError,
    AuthorizationError,
    PasswordChangeRequiredError,
    ValidationError,
)
from services.passwords import verify_password
from services.pdv_service import PDVService
from services.rate_limit import RateLimitPolicy
from services.sales import SaleService
from services.security import public_user
from tests.support import (
    TEST_ADMIN_LOGIN,
    TEST_ADMIN_PASSWORD,
    TEST_RECOVERY_CODE,
    provision_test_admin,
)


WRONG_PASSWORD = "senha-incorreta-segura"
NEW_PASSWORD = "NovaSenhaSegura9"
NEW_RECOVERY_CODE = "novo-codigo-recuperacao-seguro"
NEUTRAL_RECOVERY_MESSAGE = "Não foi possível validar a recuperação de acesso."


class MutableUtcClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: float) -> None:
        self.value += timedelta(**kwargs)


class FifthFailureBehaviorRedTest(unittest.TestCase):
    """Um RED comportamental isolado que nao depende da nova API de relogio."""

    def test_fifth_failure_blocks_a_correct_password(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "pdv.sqlite3")
            database.initialize()
            provision_test_admin(database)
            auth = AuthService(database)

            for _ in range(5):
                self.assertIsNone(auth.authenticate(TEST_ADMIN_LOGIN, WRONG_PASSWORD))

            self.assertIsNone(auth.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))


class AuthRateLimitTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "pdv.sqlite3")
        self.database.initialize()
        self.admin = provision_test_admin(self.database)
        self.clock = MutableUtcClock(datetime(2032, 5, 4, 12, 0, tzinfo=timezone.utc))
        self.auth = AuthService(self.database, clock=self.clock)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _row(self) -> dict:
        row = self.database.fetch_one("SELECT * FROM usuarios WHERE login = ?", (TEST_ADMIN_LOGIN,))
        assert row is not None
        return row

    def _fail_login(self, count: int) -> None:
        for _ in range(count):
            self.assertIsNone(self.auth.authenticate(TEST_ADMIN_LOGIN, WRONG_PASSWORD))

    def _unlock_recovery(self) -> None:
        self._fail_login(5)
        self.assertTrue(self.auth.password_recovery_available(TEST_ADMIN_LOGIN))

    def _ready_cashier(self, login: str = "caixa.rate") -> dict:
        cashier = self.auth.create_user(
            "Caixa de teste", login, "SenhaCaixaSegura8", "caixa", actor_id=self.admin["id"]
        )
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE usuarios SET deve_trocar_senha = 0 WHERE id = ?", (cashier["id"],)
            )
        return cashier

    def test_fifth_failure_blocks_for_fifteen_minutes_and_exact_expiry_releases(self) -> None:
        self._fail_login(4)
        self.assertIsNone(self._row()["login_bloqueado_ate"])
        self._fail_login(1)
        blocked = self._row()
        self.assertEqual(blocked["tentativas_login_falhas"], 5)
        self.assertIsNotNone(blocked["login_bloqueado_ate"])
        self.assertIsNone(self.auth.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))

        self.clock.advance(minutes=14, seconds=59)
        self.assertIsNone(self.auth.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))
        self.clock.advance(seconds=1)
        self.assertIsNotNone(self.auth.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))
        released = self._row()
        self.assertEqual(released["tentativas_login_falhas"], 0)
        self.assertIsNone(released["login_falhas_janela_inicio"])
        self.assertIsNone(released["login_bloqueado_ate"])

    def test_failure_at_exact_window_boundary_starts_a_new_window(self) -> None:
        self._fail_login(4)
        self.clock.advance(minutes=15)
        self._fail_login(1)

        row = self._row()
        self.assertEqual(row["tentativas_login_falhas"], 1)
        self.assertIsNone(row["login_bloqueado_ate"])
        self.assertIsNotNone(self.auth.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))

    def test_success_clears_password_window_and_counter(self) -> None:
        self._fail_login(3)
        self.assertIsNotNone(self.auth.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))
        row = self._row()
        self.assertEqual(row["tentativas_login_falhas"], 0)
        self.assertIsNone(row["login_falhas_janela_inicio"])
        self.assertIsNone(row["login_bloqueado_ate"])

        self._fail_login(4)
        self.assertIsNotNone(self.auth.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))

    def test_login_and_admin_approval_share_the_same_counter_in_both_directions(self) -> None:
        self._fail_login(2)
        for _ in range(3):
            self.assertIsNone(
                self.auth.verify_admin_credentials(TEST_ADMIN_LOGIN, WRONG_PASSWORD)
            )
        self.assertIsNone(self.auth.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))

        self.clock.advance(minutes=15)
        self.assertIsNotNone(self.auth.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))
        for _ in range(2):
            self.assertIsNone(
                self.auth.verify_admin_credentials(TEST_ADMIN_LOGIN, WRONG_PASSWORD)
            )
        self._fail_login(3)
        self.assertIsNone(
            self.auth.verify_admin_credentials(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)
        )

    def test_item_cancellation_uses_the_same_admin_password_counter(self) -> None:
        cashier = self.auth.create_user(
            "Caixa cancelamento",
            "caixa.cancelamento",
            "SenhaCaixaSegura8",
            "caixa",
            actor_id=self.admin["id"],
        )
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE usuarios SET deve_trocar_senha = 0 WHERE id = ?", (cashier["id"],)
            )
        realtime_auth = AuthService(self.database)
        for _ in range(2):
            self.assertIsNone(
                realtime_auth.authenticate(TEST_ADMIN_LOGIN, WRONG_PASSWORD)
            )
        for _ in range(3):
            with self.assertRaises(AuthorizationError):
                SaleService(self.database).authorize_item_cancellation(
                    {"gtin": "7891234567895", "nome": "Item"},
                    operador_id=cashier["id"],
                    admin_login=TEST_ADMIN_LOGIN,
                    admin_senha=WRONG_PASSWORD,
                )
        self.assertIsNone(
            realtime_auth.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)
        )
        rejected = self.database.fetch_one(
            "SELECT COUNT(*) AS total FROM logs_auditoria WHERE acao = 'CANCELAMENTO_ITEM_REJEITADO'"
        )
        self.assertEqual(rejected["total"], 3)

    def test_sale_service_direct_id_shortcut_is_only_for_the_same_active_admin(self) -> None:
        cashier = self._ready_cashier("caixa.atalho.id")
        sales = SaleService(self.database)
        with self.assertRaises(AuthorizationError):
            sales.authorize_item_cancellation(
                {"gtin": "7891234567895", "nome": "Item"},
                operador_id=cashier["id"],
                admin_user_id=self.admin["id"],
            )
        with self.assertRaises(AuthorizationError):
            sales.authorize_item_cancellation(
                {"gtin": "7891234567895", "nome": "Item"},
                operador_id=cashier["id"],
                admin_user_id=cashier["id"],
            )
        approved = sales.authorize_item_cancellation(
            {"gtin": "7891234567895", "nome": "Item"},
            operador_id=self.admin["id"],
            admin_user_id=self.admin["id"],
        )
        self.assertTrue(approved["authorized"])
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE usuarios SET deve_trocar_senha = 1 WHERE id = ?", (self.admin["id"],)
            )
        with self.assertRaises(AuthorizationError):
            sales.authorize_item_cancellation(
                {"gtin": "7891234567895", "nome": "Item"},
                operador_id=self.admin["id"],
                admin_user_id=self.admin["id"],
            )
        rejected = self.database.fetch_one(
            "SELECT COUNT(*) AS total FROM logs_auditoria WHERE acao = 'CANCELAMENTO_ITEM_REJEITADO'"
        )
        self.assertGreaterEqual(rejected["total"], 2)

    def test_sale_service_uses_the_injected_auth_clock_and_policy_without_nested_transaction(self) -> None:
        cashier = self._ready_cashier("caixa.politica.injetada")
        policy = RateLimitPolicy(
            threshold=2,
            window=timedelta(minutes=3),
            block=timedelta(minutes=4),
        )
        injected_auth = AuthService(
            self.database, clock=self.clock, login_policy=policy
        )
        try:
            sales = SaleService(self.database, auth=injected_auth)
        except TypeError:
            # Mantém o RED comportamental na implementação anterior, que não
            # aceitava a dependência e ignorava este atributo.
            sales = SaleService(self.database)
            sales.auth = injected_auth
        self.assertIsNone(
            injected_auth.authenticate(TEST_ADMIN_LOGIN, WRONG_PASSWORD)
        )
        with self.assertRaises(AuthorizationError):
            sales.authorize_item_cancellation(
                {"gtin": "7891234567895", "nome": "Item"},
                operador_id=cashier["id"],
                admin_login=TEST_ADMIN_LOGIN,
                admin_senha=WRONG_PASSWORD,
            )
        row = self._row()
        self.assertEqual(row["tentativas_login_falhas"], 2)
        self.assertIsNotNone(row["login_bloqueado_ate"])
        self.assertIs(sales.auth, injected_auth)

    def test_pdv_service_wires_the_same_auth_instance_into_sales(self) -> None:
        service = PDVService(database=self.database)
        self.assertIs(service.sales.auth, service.auth)

    def test_rate_limit_policy_is_injectable(self) -> None:
        policy = RateLimitPolicy(
            threshold=2,
            window=timedelta(minutes=3),
            block=timedelta(minutes=4),
        )
        auth = AuthService(self.database, clock=self.clock, login_policy=policy)
        self.assertIsNone(auth.authenticate(TEST_ADMIN_LOGIN, WRONG_PASSWORD))
        self.assertIsNone(auth.authenticate(TEST_ADMIN_LOGIN, WRONG_PASSWORD))
        self.assertTrue(auth.password_recovery_available(TEST_ADMIN_LOGIN))
        self.assertIsNone(auth.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))
        self.clock.advance(minutes=4)
        self.assertIsNotNone(auth.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))

    def test_orphan_legacy_counter_without_window_is_cleared_before_new_failure(self) -> None:
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE usuarios SET tentativas_login_falhas = 4, "
                "login_falhas_janela_inicio = NULL, login_bloqueado_ate = NULL "
                "WHERE id = ?",
                (self.admin["id"],),
            )
        self.assertIsNone(self.auth.authenticate(TEST_ADMIN_LOGIN, WRONG_PASSWORD))
        state = self._row()
        self.assertEqual(state["tentativas_login_falhas"], 1)
        self.assertIsNotNone(state["login_falhas_janela_inicio"])
        self.assertIsNone(state["login_bloqueado_ate"])

    def test_missing_inactive_and_blocked_accounts_have_neutral_results(self) -> None:
        self.assertIsNone(self.auth.authenticate("login.ausente", WRONG_PASSWORD))
        self.assertIsNone(
            self.auth.verify_admin_credentials("login.ausente", WRONG_PASSWORD)
        )
        with self.database.transaction(write=True) as connection:
            connection.execute("UPDATE usuarios SET ativo = 0 WHERE id = ?", (self.admin["id"],))
        self.assertIsNone(self.auth.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))
        self.assertIsNone(
            self.auth.verify_admin_credentials(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD)
        )

    def test_recovery_fifth_failure_blocks_thirty_minutes_and_success_rotates_code(self) -> None:
        self._unlock_recovery()
        for _ in range(5):
            with self.assertRaisesRegex(ValidationError, NEUTRAL_RECOVERY_MESSAGE):
                self.auth.recover_password_with_code(
                    TEST_ADMIN_LOGIN,
                    "codigo-recuperacao-incorreto",
                    NEW_PASSWORD,
                    NEW_RECOVERY_CODE,
                )
        blocked = self._row()
        self.assertEqual(blocked["recuperacao_falhas"], 5)
        self.assertIsNotNone(blocked["recuperacao_bloqueado_ate"])
        with self.assertRaisesRegex(ValidationError, NEUTRAL_RECOVERY_MESSAGE):
            self.auth.recover_password_with_code(
                TEST_ADMIN_LOGIN,
                TEST_RECOVERY_CODE,
                NEW_PASSWORD,
                NEW_RECOVERY_CODE,
            )

        self.clock.advance(minutes=29, seconds=59)
        with self.assertRaisesRegex(ValidationError, NEUTRAL_RECOVERY_MESSAGE):
            self.auth.recover_password_with_code(
                TEST_ADMIN_LOGIN,
                TEST_RECOVERY_CODE,
                NEW_PASSWORD,
                NEW_RECOVERY_CODE,
            )
        self.clock.advance(seconds=1)
        self._fail_login(5)
        self.assertTrue(self.auth.password_recovery_available(TEST_ADMIN_LOGIN))
        recovered = self.auth.recover_password_with_code(
            TEST_ADMIN_LOGIN,
            TEST_RECOVERY_CODE,
            NEW_PASSWORD,
            NEW_RECOVERY_CODE,
        )
        self.assertTrue(recovered["recovery_configured"])
        state = self._row()
        self.assertEqual(state["tentativas_login_falhas"], 0)
        self.assertEqual(state["recuperacao_falhas"], 0)
        self.assertIsNone(state["login_falhas_janela_inicio"])
        self.assertIsNone(state["login_bloqueado_ate"])
        self.assertIsNone(state["recuperacao_janela_inicio"])
        self.assertIsNone(state["recuperacao_bloqueado_ate"])
        self.assertFalse(verify_password(TEST_RECOVERY_CODE, state["codigo_recuperacao_hash"]))
        self.assertTrue(verify_password(NEW_RECOVERY_CODE, state["codigo_recuperacao_hash"]))

    def test_recovery_availability_normalizes_exact_login_expiry(self) -> None:
        self._unlock_recovery()
        self.clock.advance(minutes=15)

        self.assertFalse(self.auth.password_recovery_available(TEST_ADMIN_LOGIN))
        state = self._row()
        self.assertEqual(state["tentativas_login_falhas"], 0)
        self.assertIsNone(state["login_falhas_janela_inicio"])
        self.assertIsNone(state["login_bloqueado_ate"])

    def test_recovery_eligibility_rejects_and_normalizes_expired_login_window(self) -> None:
        self._unlock_recovery()
        self.clock.advance(minutes=15)

        with self.assertRaisesRegex(ValidationError, NEUTRAL_RECOVERY_MESSAGE):
            self.auth.recover_password_with_code(
                TEST_ADMIN_LOGIN,
                TEST_RECOVERY_CODE,
                NEW_PASSWORD,
                NEW_RECOVERY_CODE,
            )
        state = self._row()
        self.assertEqual(state["tentativas_login_falhas"], 0)
        self.assertIsNone(state["login_bloqueado_ate"])

    def test_recovery_rejections_commit_audit_without_secrets(self) -> None:
        self._unlock_recovery()
        supplied_code = "codigo-errado-nao-persistir"
        supplied_password = "SenhaNovaNaoPersistir9"  # gitleaks:allow - fixture fictícia
        supplied_new_code = "codigo-novo-nao-persistir"
        with self.assertRaises(ValidationError):
            self.auth.recover_password_with_code(
                TEST_ADMIN_LOGIN,
                supplied_code,
                supplied_password,
                supplied_new_code,
            )

        events = self.database.fetch_all(
            "SELECT acao, detalhes FROM logs_auditoria WHERE acao = 'RECUPERACAO_SENHA_REJEITADA'"
        )
        self.assertEqual(len(events), 1)
        serialized = repr(events)
        for secret in (supplied_code, supplied_password, supplied_new_code, self._row()["codigo_recuperacao_hash"]):
            self.assertNotIn(secret, serialized)

    def test_recovery_error_is_neutral_for_unknown_and_unavailable_accounts(self) -> None:
        messages: list[str] = []
        for login in ("login.ausente", TEST_ADMIN_LOGIN):
            try:
                self.auth.recover_password_with_code(
                    login,
                    TEST_RECOVERY_CODE,
                    NEW_PASSWORD,
                    NEW_RECOVERY_CODE,
                )
            except ValidationError as exc:
                messages.append(str(exc))
        self.assertEqual(messages, [NEUTRAL_RECOVERY_MESSAGE, NEUTRAL_RECOVERY_MESSAGE])

    def test_concurrent_failures_do_not_lose_increments_or_bypass_block(self) -> None:
        barrier = threading.Barrier(5)
        results: list[object] = []
        errors: list[BaseException] = []

        def fail() -> None:
            try:
                barrier.wait(timeout=10)
                results.append(AuthService(self.database, clock=self.clock).authenticate(TEST_ADMIN_LOGIN, WRONG_PASSWORD))
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=fail) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(results, [None] * 5)
        row = self._row()
        self.assertEqual(row["tentativas_login_falhas"], 5)
        self.assertIsNotNone(row["login_bloqueado_ate"])

    def test_legacy_admin_configures_and_rotates_only_own_recovery_code(self) -> None:
        service = PDVService(database=self.database)
        self.assertIsNotNone(service.authenticate(TEST_ADMIN_LOGIN, TEST_ADMIN_PASSWORD))
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE usuarios SET codigo_recuperacao_hash = NULL WHERE id = ?",
                (self.admin["id"],),
            )
        self.assertFalse(public_user(self._row())["recovery_configured"])
        code = service.prepare_own_recovery_code()
        self.assertGreaterEqual(len(code), 12)
        with self.assertRaises(AuthenticationError):
            service.configure_own_recovery_code(WRONG_PASSWORD, code)
        self.assertIsNone(self._row()["codigo_recuperacao_hash"])

        configured = service.configure_own_recovery_code(TEST_ADMIN_PASSWORD, code)
        self.assertTrue(configured["recovery_configured"])
        self.assertIsNotNone(service.current_user())
        stored = self._row()
        self.assertNotEqual(stored["codigo_recuperacao_hash"], code)
        self.assertTrue(verify_password(code, stored["codigo_recuperacao_hash"]))

        rotated_code = service.prepare_own_recovery_code()
        rotated = service.configure_own_recovery_code(TEST_ADMIN_PASSWORD, rotated_code)
        self.assertTrue(rotated["recovery_configured"])
        self.assertTrue(verify_password(rotated_code, self._row()["codigo_recuperacao_hash"]))
        self.assertFalse(verify_password(code, self._row()["codigo_recuperacao_hash"]))
        self.assertNotIn("user_id", inspect.signature(service.configure_own_recovery_code).parameters)
        audit_text = repr(
            self.database.fetch_all(
                "SELECT acao, detalhes FROM logs_auditoria WHERE acao LIKE '%RECUPERACAO%'"
            )
        )
        for secret in (TEST_ADMIN_PASSWORD, code, rotated_code, self._row()["codigo_recuperacao_hash"]):
            self.assertNotIn(secret, audit_text)

    def test_recovery_configuration_password_proof_uses_shared_limiter(self) -> None:
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE usuarios SET codigo_recuperacao_hash = NULL WHERE id = ?",
                (self.admin["id"],),
            )
        attempted_code = "codigo-configuracao-limitada"
        for _ in range(5):
            with self.assertRaises(AuthenticationError):
                self.auth.configure_own_recovery_code(
                    WRONG_PASSWORD, attempted_code, actor_id=self.admin["id"]
                )
        blocked = self._row()
        self.assertEqual(blocked["tentativas_login_falhas"], 5)
        self.assertIsNotNone(blocked["login_bloqueado_ate"])
        with self.assertRaises(AuthenticationError):
            self.auth.configure_own_recovery_code(
                TEST_ADMIN_PASSWORD, attempted_code, actor_id=self.admin["id"]
            )
        audit_text = repr(
            self.database.fetch_all(
                "SELECT acao, detalhes FROM logs_auditoria "
                "WHERE acao = 'CONFIGURACAO_RECUPERACAO_REJEITADA'"
            )
        )
        self.assertNotIn(WRONG_PASSWORD, audit_text)
        self.assertNotIn(attempted_code, audit_text)

    def test_change_password_current_proof_uses_shared_limiter(self) -> None:
        for _ in range(5):
            with self.assertRaises(AuthenticationError):
                self.auth.change_password(
                    self.admin["id"],
                    WRONG_PASSWORD,
                    NEW_PASSWORD,
                    actor_id=self.admin["id"],
                )
        blocked = self._row()
        self.assertEqual(blocked["tentativas_login_falhas"], 5)
        self.assertIsNotNone(blocked["login_bloqueado_ate"])
        with self.assertRaises(AuthenticationError):
            self.auth.change_password(
                self.admin["id"],
                TEST_ADMIN_PASSWORD,
                NEW_PASSWORD,
                actor_id=self.admin["id"],
            )

    def test_legacy_password_verification_is_own_only_and_rate_limited(self) -> None:
        cashier = self._ready_cashier("caixa.oracle")
        with self.assertRaises(AuthorizationError):
            self.auth.verify_user_password(
                cashier["id"], "SenhaCaixaSegura8", actor_id=self.admin["id"]
            )
        for _ in range(5):
            self.assertFalse(
                self.auth.verify_user_password(
                    self.admin["id"], WRONG_PASSWORD, actor_id=self.admin["id"]
                )
            )
        blocked = self._row()
        self.assertEqual(blocked["tentativas_login_falhas"], 5)
        self.assertIsNotNone(blocked["login_bloqueado_ate"])
        self.assertFalse(
            self.auth.verify_user_password(
                self.admin["id"], TEST_ADMIN_PASSWORD, actor_id=self.admin["id"]
            )
        )

    def test_legacy_password_verification_rejects_pending_password_change(self) -> None:
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE usuarios SET deve_trocar_senha = 1 WHERE id = ?",
                (self.admin["id"],),
            )

        with self.assertRaises(PasswordChangeRequiredError):
            self.auth.verify_user_password(
                self.admin["id"], TEST_ADMIN_PASSWORD, actor_id=self.admin["id"]
            )
        self.assertEqual(self._row()["deve_trocar_senha"], 1)

        changed = self.auth.change_password(
            self.admin["id"],
            TEST_ADMIN_PASSWORD,
            NEW_PASSWORD,
            actor_id=self.admin["id"],
        )
        self.assertFalse(changed["deve_trocar_senha"])

    def test_cancel_audit_uses_safe_whitelist_and_discards_arbitrary_sensitive_fields(self) -> None:
        cashier = self._ready_cashier("caixa.auditoria.segura")
        sentinels = (
            "sentinela-senha-nao-persistir",
            "sentinela-codigo-nao-persistir",
            "sentinela-hash-nao-persistir",
            "sentinela-aninhada-nao-persistir",
        )
        item = {
            "gtin": "7891234567895",
            "nome": "Item permitido",
            "quantidade": 1,
            "senha": sentinels[0],
            "codigo_recuperacao": sentinels[1],
            "codigo_recuperacao_hash": sentinels[2],
            "campo_arbitrario": {"valor": sentinels[3]},
        }
        with self.assertRaises(AuthorizationError):
            SaleService(self.database).authorize_item_cancellation(
                item,
                operador_id=cashier["id"],
                admin_login=TEST_ADMIN_LOGIN,
                admin_senha=WRONG_PASSWORD,
            )
        audit = self.database.fetch_one(
            "SELECT detalhes FROM logs_auditoria "
            "WHERE acao = 'CANCELAMENTO_ITEM_REJEITADO' ORDER BY id DESC LIMIT 1"
        )
        self.assertIsNotNone(audit)
        serialized = str(audit["detalhes"])
        self.assertIn("7891234567895", serialized)
        self.assertIn("Item permitido", serialized)
        for sentinel in sentinels:
            self.assertNotIn(sentinel, serialized)

    def test_cashier_cannot_prepare_or_configure_recovery_code(self) -> None:
        cashier = self.auth.create_user(
            "Caixa", "caixa.rate", "SenhaCaixaSegura8", "caixa", actor_id=self.admin["id"]
        )
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE usuarios SET deve_trocar_senha = 0 WHERE id = ?", (cashier["id"],)
            )
        service = PDVService(database=self.database)
        self.assertIsNotNone(service.authenticate("caixa.rate", "SenhaCaixaSegura8"))
        with self.assertRaises(AuthorizationError):
            service.prepare_own_recovery_code()
        with self.assertRaises(AuthorizationError):
            service.configure_own_recovery_code(
                "SenhaCaixaSegura8", "codigo-recuperacao-caixa"
            )

    def test_public_user_exposes_only_recovery_boolean(self) -> None:
        result = public_user(self._row())
        self.assertTrue(result["recovery_configured"])
        self.assertNotIn("codigo_recuperacao_hash", result)
        self.assertNotIn("recovery_code", result)


if __name__ == "__main__":
    unittest.main()
