"""Outbox transacional e retomável de comprovantes."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from db.database import Database
from printing.outbox import PrintOutboxWorker
from printing.receipt_printer import PrintResult
from services.sales import SaleService


class FakePrinter:
    def __init__(self, *, printed: bool = True) -> None:
        self.printed = printed
        self.receipts: list[dict] = []

    def print_receipt(self, receipt: dict) -> PrintResult:
        self.receipts.append(receipt)
        return PrintResult(
            printed=self.printed,
            message="Impresso" if self.printed else "Impressora indisponível",
            receipt_text="COMPROVANTE TESTE",
        )


class PrintOutboxTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "outbox.sqlite3")
        self.database.initialize()
        with self.database.transaction(write=True) as connection:
            connection.executemany(
                "INSERT INTO usuarios(id, nome, login, senha_hash, perfil, ativo) "
                "VALUES (?, ?, ?, 'hash', ?, 1)",
                ((1, "Admin", "admin", "admin"), (2, "Caixa", "caixa", "caixa")),
            )
            connection.execute(
                "INSERT INTO produtos(gtin, nome, preco, estoque, unidade, estoque_controlado, ativo) "
                "VALUES ('7891234567895', 'Produto', 10, 10, 'UN', 1, 1)"
            )
            connection.execute(
                "INSERT INTO caixas(id, usuario_id, fundo_inicial, status) VALUES (1, 2, 20, 'ABERTO')"
            )
        self.sales = SaleService(self.database)
        self.workers: list[PrintOutboxWorker] = []

    def tearDown(self) -> None:
        for worker in self.workers:
            worker.shutdown(timeout=1)
        self.database.close()
        self.temporary.cleanup()

    def _sale(self, key: str, *, printing: bool) -> dict:
        return self.sales.finalize(
            1,
            [{"gtin": "7891234567895", "quantidade": "1"}],
            "Dinheiro",
            "20.00",
            operador_id=2,
            chave_idempotencia=key,
            receipt_context=(
                {
                    "business_name": "Padaria de Teste",
                    "business_document": "",
                    "address": "Balcão",
                }
                if printing
                else None
            ),
        )

    def _wait_status(self, expected: str, timeout: float = 3) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = self.database.fetch_one(
                "SELECT * FROM impressao_outbox ORDER BY id DESC LIMIT 1"
            )
            if row is not None and row["status"] == expected:
                return row
            time.sleep(0.02)
        self.fail(f"A impressão não chegou ao estado {expected}.")

    def test_active_printing_commits_original_outbox_with_sale_but_disabled_does_not(self) -> None:
        first = self._sale("OUTBOX-SALE-0001", printing=True)
        job = self.database.fetch_one(
            "SELECT * FROM impressao_outbox WHERE venda_id = ?", (first["id"],)
        )
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "PENDENTE")
        self.assertEqual(job["tipo"], "ORIGINAL")

        second = self._sale("OUTBOX-SALE-0002", printing=False)
        self.assertIsNone(
            self.database.fetch_one(
                "SELECT * FROM impressao_outbox WHERE venda_id = ?", (second["id"],)
            )
        )

    def test_worker_marks_printed_or_failed_and_restart_resumes_pending(self) -> None:
        sale = self._sale("OUTBOX-SALE-0003", printing=True)
        printer = FakePrinter(printed=False)
        worker = PrintOutboxWorker(self.database, lambda: printer)
        self.workers.append(worker)
        failed = self._wait_status("FALHOU")
        self.assertEqual(failed["tentativas"], 1)
        self.assertEqual(len(printer.receipts), 1)
        self.assertEqual(
            self.database.fetch_one("SELECT status FROM vendas WHERE id = ?", (sale["id"],))["status"],
            "CONFIRMADA",
        )

        printer.printed = True
        worker.retry(failed["id"], actor_id=2)
        printed = self._wait_status("IMPRESSO")
        self.assertEqual(printed["tentativas"], 2)

    def test_second_copy_is_distinct_audited_and_does_not_change_stock(self) -> None:
        sale = self._sale("OUTBOX-SALE-0004", printing=False)
        before = self.database.fetch_one(
            "SELECT estoque FROM produtos WHERE gtin = '7891234567895'"
        )["estoque"]

        job = self.sales.queue_receipt_copy(
            sale["id"],
            actor_id=2,
            idempotency_key="SEGUNDA-VIA-0001",
            receipt_context={"business_name": "Padaria de Teste", "address": "Balcão"},
        )
        replay = self.sales.queue_receipt_copy(
            sale["id"],
            actor_id=2,
            idempotency_key="SEGUNDA-VIA-0001",
            receipt_context={"business_name": "Padaria de Teste", "address": "Balcão"},
        )

        self.assertEqual(job["tipo"], "SEGUNDA_VIA")
        self.assertEqual(replay["id"], job["id"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(
            self.database.fetch_one(
                "SELECT estoque FROM produtos WHERE gtin = '7891234567895'"
            )["estoque"],
            before,
        )
        with self.database.transaction() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM logs_auditoria WHERE acao = 'SEGUNDA_VIA_SOLICITADA'"
                ).fetchone()[0],
                1,
            )


if __name__ == "__main__":
    unittest.main()
