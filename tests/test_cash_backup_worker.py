"""Backup pós-fechamento durável e fora da thread do operador."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from db.database import Database
from services.cash import CashService


class ControlledBackup:
    def __init__(self, root: Path, *, fail: bool = False) -> None:
        self.root = root
        self.fail = fail
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def backup_database(self) -> Path:
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=1.5)
        if self.fail:
            raise RuntimeError("falha simulada sem caminho sensível")
        return self.root / "backup-controlado.sqlite3"


class CashBackupWorkerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = Database(self.root / "cash.sqlite3")
        self.database.initialize()
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO usuarios(id, nome, login, senha_hash, perfil, ativo) "
                "VALUES (1, 'Operadora', 'caixa', 'hash', 'caixa', 1)"
            )
        self.services: list[CashService] = []

    def tearDown(self) -> None:
        for service in self.services:
            service.shutdown(timeout=1)
        self.database.close()
        self.temporary.cleanup()

    def _service(self, backup: ControlledBackup) -> CashService:
        service = CashService(self.database, backup_service=backup)
        self.services.append(service)
        return service

    def _wait_status(self, expected: str, timeout: float = 3) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = self.database.fetch_one("SELECT * FROM backups_caixa ORDER BY id DESC LIMIT 1")
            if row is not None and row["status"] == expected:
                return row
            time.sleep(0.02)
        self.fail(f"O backup não chegou ao estado {expected}.")

    def test_close_returns_while_slow_backup_remains_pending(self) -> None:
        backup = ControlledBackup(self.root)
        service = self._service(backup)
        opened = service.open_cash(1, "100.00", actor_id=1)

        started_at = time.monotonic()
        result = service.close_cash(opened["id"], "100.00", actor_id=1)
        elapsed = time.monotonic() - started_at

        self.assertLess(elapsed, 0.25)
        self.assertEqual(result["backup_status"], "PENDENTE")
        self.assertTrue(backup.started.wait(timeout=1))
        pending = self.database.fetch_one("SELECT * FROM backups_caixa WHERE caixa_id = ?", (opened["id"],))
        self.assertEqual(pending["status"], "PENDENTE")
        backup.release.set()
        completed = self._wait_status("CONCLUIDO")
        self.assertEqual(completed["arquivo"], str(self.root / "backup-controlado.sqlite3"))

    def test_failure_does_not_reopen_cash_and_retry_is_auditable(self) -> None:
        backup = ControlledBackup(self.root, fail=True)
        backup.release.set()
        service = self._service(backup)
        opened = service.open_cash(1, "80.00", actor_id=1)
        service.close_cash(opened["id"], "80.00", actor_id=1)

        failed = self._wait_status("FALHOU")
        self.assertIn("falha simulada", failed["ultimo_erro"])
        self.assertEqual(
            self.database.fetch_one("SELECT status FROM caixas WHERE id = ?", (opened["id"],))["status"],
            "FECHADO",
        )

        backup.fail = False
        service.retry_cash_backup(opened["id"], actor_id=1)
        completed = self._wait_status("CONCLUIDO")
        self.assertGreaterEqual(completed["tentativas"], 2)
        with self.database.transaction() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM logs_auditoria WHERE acao = 'BACKUP_CAIXA_REENVIADO'"
                ).fetchone()[0],
                1,
            )

    def test_restart_resumes_pending_job_without_duplicate_close(self) -> None:
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO caixas(id, usuario_id, fundo_inicial, valor_informado, valor_esperado, "
                "quebra, status) VALUES (1, 1, 20, 20, 20, 0, 'FECHADO')"
            )
            connection.execute(
                "INSERT INTO backups_caixa(caixa_id, solicitado_por, status) VALUES (1, 1, 'PENDENTE')"
            )
        backup = ControlledBackup(self.root)
        backup.release.set()

        self._service(backup)

        completed = self._wait_status("CONCLUIDO")
        self.assertEqual(completed["caixa_id"], 1)
        self.assertEqual(backup.calls, 1)
        self.assertEqual(
            self.database.fetch_one("SELECT COUNT(*) AS total FROM caixas")["total"], 1
        )


if __name__ == "__main__":
    unittest.main()
