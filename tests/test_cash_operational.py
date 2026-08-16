"""Invariantes operacionais do único caixa físico da padaria."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from db.database import Database
from services.cash import CashService
from services.errors import AuthorizationError, ConflictError


class CashOperationalTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "cash.sqlite3")
        self.database.initialize()
        with self.database.transaction(write=True) as connection:
            connection.executemany(
                "INSERT INTO usuarios(id, nome, login, senha_hash, perfil, ativo) "
                "VALUES (?, ?, ?, 'hash-teste', ?, 1)",
                (
                    (1, "Admin", "admin", "admin"),
                    (2, "Caixa Um", "caixa1", "caixa"),
                    (3, "Caixa Dois", "caixa2", "caixa"),
                ),
            )
        self.cash = CashService(self.database)

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    def test_two_users_open_concurrently_and_exactly_one_wins(self) -> None:
        start = threading.Barrier(2)
        successes: list[dict] = []
        failures: list[BaseException] = []

        def open_for(user_id: int) -> None:
            try:
                start.wait(timeout=5)
                successes.append(self.cash.open_cash(user_id, "50.00", actor_id=user_id))
            except BaseException as exc:
                failures.append(exc)

        threads = [
            threading.Thread(target=open_for, args=(2,)),
            threading.Thread(target=open_for, args=(3,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ConflictError)
        self.assertEqual(
            self.database.fetch_one(
                "SELECT COUNT(*) AS total FROM caixas WHERE status = 'ABERTO'"
            )["total"],
            1,
        )

    def test_owner_resumes_and_admin_resume_requires_reason_and_audits(self) -> None:
        opened = self.cash.open_cash(2, "40.00", actor_id=2)
        self.assertEqual(self.cash.get_open_cash(2, actor_id=2)["id"], opened["id"])
        occupied = self.cash.get_global_open_cash(actor_id=3)
        self.assertEqual(occupied["id"], opened["id"])

        with self.assertRaises(AuthorizationError):
            self.cash.resume_open_cash(opened["id"], actor_id=3, reason="Tentativa comum")
        with self.assertRaisesRegex(Exception, "8 e 250"):
            self.cash.resume_open_cash(opened["id"], actor_id=1, reason="curto")

        resumed = self.cash.resume_open_cash(
            opened["id"],
            actor_id=1,
            reason="Retomada administrativa para continuar o atendimento",
        )
        self.assertEqual(resumed["id"], opened["id"])
        with self.database.transaction() as connection:
            event = connection.execute(
                "SELECT detalhes FROM logs_auditoria WHERE acao = 'CAIXA_RETOMADO_ADMIN'"
            ).fetchone()
        self.assertIsNotNone(event)
        self.assertIn("Retomada administrativa", str(event["detalhes"]))

    def test_movement_idempotency_replays_equal_and_conflicts_when_changed(self) -> None:
        opened = self.cash.open_cash(2, "50.00", actor_id=2)
        first = self.cash.add_movement(
            opened["id"],
            "SUPRIMENTO",
            "10.00",
            "Troco adicional",
            actor_id=2,
            chave_idempotencia="MOVIMENTO-0001",
        )
        replay = self.cash.add_movement(
            opened["id"],
            "SUPRIMENTO",
            "10.00",
            "Troco adicional",
            actor_id=2,
            chave_idempotencia="MOVIMENTO-0001",
        )
        self.assertEqual(replay["id"], first["id"])
        self.assertTrue(replay["idempotent_replay"])

        with self.assertRaises(ConflictError):
            self.cash.add_movement(
                opened["id"],
                "SANGRIA",
                "10.00",
                "Troco adicional",
                actor_id=2,
                chave_idempotencia="MOVIMENTO-0001",
            )
        self.assertEqual(
            self.database.fetch_one("SELECT COUNT(*) AS total FROM movimentacoes_caixa")["total"],
            1,
        )

    def test_concurrent_close_has_one_success_and_keeps_financial_result(self) -> None:
        opened = self.cash.open_cash(2, "100.00", actor_id=2)
        start = threading.Barrier(2)
        successes: list[dict] = []
        failures: list[BaseException] = []

        def close() -> None:
            try:
                start.wait(timeout=5)
                successes.append(
                    self.cash.close_cash(opened["id"], "100.00", actor_id=2)
                )
            except BaseException as exc:
                failures.append(exc)

        threads = [threading.Thread(target=close), threading.Thread(target=close)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ConflictError)
        stored = self.database.fetch_one("SELECT * FROM caixas WHERE id = ?", (opened["id"],))
        self.assertEqual(stored["status"], "FECHADO")
        self.assertEqual(stored["valor_esperado"], 100)
        self.assertEqual(stored["valor_informado"], 100)


if __name__ == "__main__":
    unittest.main()
