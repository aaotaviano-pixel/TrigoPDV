from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.database import Database
from services.backup import BackupService
from services.errors import AuthorizationError, ConflictError, ValidationError
from services.production import ProductionPreparationService
from tests.support import provision_test_admin


class ProductionPreparationServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temporary.name)
        self.database = Database(self.root / "pdv.sqlite3")
        self.database.initialize()
        self.admin = provision_test_admin(self.database)
        self.backup = BackupService(self.database, self.root / "backups")
        self.service = ProductionPreparationService(self.database, self.backup)

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    def _seed_training_operation(self) -> None:
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO produtos(gtin, nome, preco, estoque, estoque_controlado) "
                "VALUES ('7891234567895', 'Produto real', 12.5, 7, 1)"
            )
            cash_id = connection.execute(
                "INSERT INTO caixas(usuario_id, fundo_inicial, valor_informado, valor_esperado, "
                "quebra, justificativa, status, data_fechamento) "
                "VALUES (?, 20, 32.5, 32.5, 0, '', 'FECHADO', CURRENT_TIMESTAMP)",
                (self.admin["id"],),
            ).lastrowid
            sale_id = connection.execute(
                "INSERT INTO vendas(caixa_id, operador_id, total, forma_pagamento, "
                "valor_recebido, troco, chave_idempotencia, fingerprint) "
                "VALUES (?, ?, 12.5, 'Dinheiro', 20, 7.5, 'venda-teste', 'fp-venda')",
                (cash_id, self.admin["id"]),
            ).lastrowid
            connection.execute(
                "INSERT INTO itens_venda(venda_id, gtin, nome_produto, quantidade, "
                "preco_unitario, subtotal) VALUES (?, '7891234567895', 'Produto real', 1, 12.5, 12.5)",
                (sale_id,),
            )
            connection.execute(
                "INSERT INTO movimentacoes_caixa(caixa_id, usuario_id, tipo, valor, observacao) "
                "VALUES (?, ?, 'SUPRIMENTO', 5, 'treinamento')",
                (cash_id, self.admin["id"]),
            )
            connection.execute(
                "INSERT INTO impressao_outbox(venda_id, tipo, solicitado_por, chave_idempotencia, "
                "fingerprint, status, payload) VALUES (?, 'ORIGINAL', ?, 'print-teste', "
                "'fp-print', 'IMPRESSO', '{}')",
                (sale_id, self.admin["id"]),
            )

    def test_backup_precedes_one_time_cleanup_and_preserves_setup(self) -> None:
        self._seed_training_operation()
        self.assertFalse(self.service.status(actor_id=self.admin["id"])["prepared"])

        result = self.service.prepare(
            actor_id=self.admin["id"], confirmation=" iniciar   producao "
        )

        self.assertEqual(result["integrity"], "ok")
        self.assertTrue(self.service.status(actor_id=self.admin["id"])["prepared"])
        backup_path = Path(result["backup_path"])
        self.assertTrue(backup_path.is_file())
        with sqlite3.connect(backup_path) as backup:
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM vendas").fetchone()[0], 1)

        with self.database.transaction() as connection:
            for table in (
                "caixas",
                "vendas",
                "itens_venda",
                "movimentacoes_caixa",
                "cancelamentos_venda",
                "backups_caixa",
                "impressao_outbox",
            ):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            product = connection.execute(
                "SELECT nome, preco, estoque, estoque_controlado FROM produtos WHERE gtin = '7891234567895'"
            ).fetchone()
            self.assertEqual(tuple(product), ("Produto real", 12.5, 0.0, 0))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)
            audit = connection.execute("SELECT acao FROM logs_auditoria").fetchall()
            self.assertEqual([row["acao"] for row in audit], ["PREPARACAO_PRODUCAO_CONCLUIDA"])
            self.assertIsNotNone(
                connection.execute(
                    "SELECT valor FROM schema_meta WHERE chave = 'production_prepared_at'"
                ).fetchone()
            )

        with self.assertRaises(ConflictError):
            self.service.prepare(
                actor_id=self.admin["id"], confirmation="INICIAR PRODUCAO"
            )

    def test_invalid_confirmation_and_non_admin_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.prepare(actor_id=self.admin["id"], confirmation="confirmar")
        with self.database.transaction(write=True) as connection:
            cashier_id = connection.execute(
                "INSERT INTO usuarios(nome, login, senha_hash, perfil) VALUES "
                "('Caixa', 'caixa', 'hash-teste', 'caixa')"
            ).lastrowid
        with self.assertRaises(AuthorizationError):
            self.service.prepare(actor_id=cashier_id, confirmation="INICIAR PRODUCAO")
        self.assertFalse((self.root / "backups").exists())

    def test_open_cash_blocks_cleanup_without_creating_backup(self) -> None:
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO caixas(usuario_id, fundo_inicial, status) VALUES (?, 20, 'ABERTO')",
                (self.admin["id"],),
            )
        with self.assertRaises(ConflictError):
            self.service.prepare(
                actor_id=self.admin["id"], confirmation="INICIAR PRODUCAO"
            )
        self.assertFalse((self.root / "backups").exists())


if __name__ == "__main__":
    unittest.main()
