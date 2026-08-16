"""Contratos unificados do schema 9 em bancos SQLite temporários."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.database import Database, DatabaseError


V8_SCHEMA = """
CREATE TABLE schema_meta (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
INSERT INTO schema_meta VALUES ('schema_version', '8');
CREATE TABLE usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    login TEXT NOT NULL UNIQUE COLLATE NOCASE,
    senha_hash TEXT NOT NULL,
    perfil TEXT NOT NULL,
    ativo INTEGER NOT NULL DEFAULT 1,
    deve_trocar_senha INTEGER NOT NULL DEFAULT 0,
    codigo_recuperacao_hash TEXT,
    tentativas_login_falhas INTEGER NOT NULL DEFAULT 0,
    login_falhas_janela_inicio TEXT,
    login_bloqueado_ate TEXT,
    recuperacao_falhas INTEGER NOT NULL DEFAULT 0,
    recuperacao_janela_inicio TEXT,
    recuperacao_bloqueado_ate TEXT,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE produtos (
    gtin TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    preco REAL NOT NULL DEFAULT 0,
    estoque REAL NOT NULL DEFAULT 0,
    unidade TEXT NOT NULL DEFAULT 'UN',
    ativo INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE caixas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    data_abertura TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    data_fechamento TEXT,
    fundo_inicial REAL NOT NULL,
    valor_informado REAL,
    valor_esperado REAL,
    quebra REAL,
    justificativa TEXT,
    status TEXT NOT NULL DEFAULT 'ABERTO'
);
CREATE UNIQUE INDEX uq_caixa_aberto_por_usuario
    ON caixas(usuario_id) WHERE status = 'ABERTO';
CREATE TABLE vendas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caixa_id INTEGER NOT NULL REFERENCES caixas(id),
    operador_id INTEGER REFERENCES usuarios(id),
    total REAL NOT NULL,
    forma_pagamento TEXT NOT NULL,
    valor_recebido REAL,
    troco REAL NOT NULL DEFAULT 0,
    data_venda TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'CONFIRMADA',
    chave_idempotencia TEXT
);
CREATE UNIQUE INDEX uq_vendas_chave_idempotencia
    ON vendas(chave_idempotencia) WHERE chave_idempotencia IS NOT NULL;
CREATE TABLE itens_venda (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venda_id INTEGER NOT NULL REFERENCES vendas(id),
    gtin TEXT REFERENCES produtos(gtin),
    nome_produto TEXT NOT NULL,
    unidade TEXT NOT NULL DEFAULT 'UN',
    quantidade REAL NOT NULL,
    preco_unitario REAL NOT NULL,
    subtotal REAL NOT NULL
);
CREATE TABLE movimentacoes_caixa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caixa_id INTEGER NOT NULL REFERENCES caixas(id),
    usuario_id INTEGER REFERENCES usuarios(id),
    tipo TEXT NOT NULL,
    valor REAL NOT NULL,
    observacao TEXT,
    data_movimentacao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class SchemaV9TestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "pdv.sqlite3"
        self.database = Database(self.path)

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    def _create_v8(self, *, open_cash_users: tuple[int, ...] = (1,)) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.executescript(V8_SCHEMA)
            connection.executemany(
                "INSERT INTO usuarios(id, nome, login, senha_hash, perfil) VALUES (?, ?, ?, 'hash', ?)",
                (
                    (1, "Admin", "admin", "admin"),
                    (2, "Caixa", "caixa", "caixa"),
                ),
            )
            connection.execute(
                "INSERT INTO produtos(gtin, nome, preco, estoque, unidade) "
                "VALUES ('7890000000001', 'Produto legado', 12.5, 10, 'UN')"
            )
            for user_id in open_cash_users:
                connection.execute(
                    "INSERT INTO caixas(usuario_id, fundo_inicial, status) VALUES (?, 100, 'ABERTO')",
                    (user_id,),
                )
            if open_cash_users:
                cash_id = connection.execute("SELECT id FROM caixas ORDER BY id LIMIT 1").fetchone()[0]
                connection.execute(
                    "INSERT INTO vendas(caixa_id, operador_id, total, forma_pagamento, "
                    "valor_recebido, troco, chave_idempotencia) "
                    "VALUES (?, 1, 12.5, 'Dinheiro', 20, 7.5, 'VENDA-LEGADA')",
                    (cash_id,),
                )
                connection.execute(
                    "INSERT INTO itens_venda(venda_id, gtin, nome_produto, unidade, quantidade, "
                    "preco_unitario, subtotal) VALUES (1, '7890000000001', 'Produto legado', "
                    "'UN', 1, 12.5, 12.5)"
                )
                connection.execute(
                    "INSERT INTO movimentacoes_caixa(caixa_id, usuario_id, tipo, valor, observacao) "
                    "VALUES (?, 1, 'SUPRIMENTO', 5, 'Legado')",
                    (cash_id,),
                )
            connection.commit()
        finally:
            connection.close()

    def _columns(self, table: str) -> set[str]:
        with self.database.transaction() as connection:
            return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}

    def _logical_snapshot(self) -> tuple[tuple[object, ...], ...]:
        connection = sqlite3.connect(self.path)
        try:
            rows: list[tuple[object, ...]] = []
            for table in ("schema_meta", "usuarios", "produtos", "caixas", "vendas", "itens_venda"):
                rows.extend(
                    (table, *row)
                    for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                )
            rows.extend(
                ("sqlite_master", *row)
                for row in connection.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
                ).fetchall()
            )
            return tuple(rows)
        finally:
            connection.close()

    def test_v8_to_v9_preserves_legacy_rows_and_adds_checkout_fields(self) -> None:
        self._create_v8()

        result = self.database.initialize(backup_dir=self.root / "backups")

        self.assertEqual((result.from_version, result.to_version), (8, 9))
        self.assertEqual(result.applied_versions, (9,))
        self.assertIsNotNone(result.backup_path)
        self.assertTrue(
            {"total_manual", "autorizador_excecao_id", "motivo_excecao", "fingerprint"}
            .issubset(self._columns("vendas"))
        )
        self.assertTrue(
            {"tipo_lancamento", "codigo_informado", "preco_original"}
            .issubset(self._columns("itens_venda"))
        )
        self.assertTrue(
            {"chave_idempotencia", "fingerprint"}
            .issubset(self._columns("movimentacoes_caixa"))
        )
        sale = self.database.fetch_one("SELECT * FROM vendas WHERE id = 1")
        item = self.database.fetch_one("SELECT * FROM itens_venda WHERE id = 1")
        assert sale is not None and item is not None
        self.assertEqual(sale["total_manual"], 0)
        self.assertIsNone(sale["autorizador_excecao_id"])
        self.assertIsNone(sale["fingerprint"])
        self.assertEqual(item["tipo_lancamento"], "CATALOGO")
        self.assertIsNone(item["codigo_informado"])
        self.assertIsNone(item["preco_original"])

    def test_duplicate_open_cash_aborts_before_any_v9_ddl(self) -> None:
        self._create_v8(open_cash_users=(1, 2))
        before = self._logical_snapshot()

        with self.assertRaisesRegex(DatabaseError, "mais de um caixa aberto"):
            self.database.initialize(backup_dir=self.root / "backups")

        self.assertEqual(self._logical_snapshot(), before)
        self.assertNotIn("total_manual", self._columns("vendas"))

    def test_global_open_cash_and_idempotency_constraints_are_enforced(self) -> None:
        self._create_v8(open_cash_users=(1,))
        self.database.initialize(backup_dir=self.root / "backups")

        with self.assertRaises(DatabaseError):
            with self.database.transaction(write=True) as connection:
                connection.execute(
                    "INSERT INTO caixas(usuario_id, fundo_inicial, status) VALUES (2, 25, 'ABERTO')"
                )

        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE movimentacoes_caixa SET chave_idempotencia = 'MOV-1', fingerprint = 'abc' WHERE id = 1"
            )
        with self.assertRaises(DatabaseError):
            with self.database.transaction(write=True) as connection:
                connection.execute(
                    "INSERT INTO movimentacoes_caixa(caixa_id, usuario_id, tipo, valor, "
                    "chave_idempotencia, fingerprint) VALUES (1, 1, 'SUPRIMENTO', 1, 'MOV-1', 'abc')"
                )

    def test_cancellation_backup_and_print_outbox_contracts(self) -> None:
        self._create_v8()
        self.database.initialize(backup_dir=self.root / "backups")

        with self.database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO cancelamentos_venda(venda_id, operador_id, autorizador_id, motivo, "
                "chave_idempotencia, fingerprint) VALUES (1, 1, 1, 'Cancelamento autorizado', "
                "'CANCEL-1', 'cancel-fingerprint')"
            )
            connection.execute(
                "INSERT INTO backups_caixa(caixa_id, status) VALUES (1, 'PENDENTE')"
            )
            connection.execute(
                "INSERT INTO impressao_outbox(venda_id, tipo, chave_idempotencia, fingerprint, status, payload) "
                "VALUES (1, 'ORIGINAL', 'PRINT-1', 'print-fingerprint', 'PENDENTE', '{}')"
            )

        for sql in (
            "INSERT INTO cancelamentos_venda(venda_id, operador_id, autorizador_id, motivo, "
            "chave_idempotencia, fingerprint) VALUES (1, 1, 1, 'Outro motivo válido', "
            "'CANCEL-2', 'outro')",
            "INSERT INTO backups_caixa(caixa_id, status) VALUES (1, 'DESCONHECIDO')",
            "INSERT INTO impressao_outbox(venda_id, tipo, chave_idempotencia, fingerprint, status, payload) "
            "VALUES (1, 'SEGUNDA_VIA', 'PRINT-2', 'print-fingerprint-2', 'DESCONHECIDO', '{}')",
        ):
            with self.assertRaises(DatabaseError):
                with self.database.transaction(write=True) as connection:
                    connection.execute(sql)

    def test_fresh_v9_and_second_initialize_are_idempotent(self) -> None:
        first = self.database.initialize(backup_dir=self.root / "backups")
        second = self.database.initialize(backup_dir=self.root / "backups")

        self.assertEqual((first.from_version, first.to_version), (0, 9))
        self.assertEqual((second.from_version, second.to_version), (9, 9))
        self.assertEqual(second.applied_versions, ())
        with self.database.transaction() as connection:
            names = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertTrue(
            {"cancelamentos_venda", "backups_caixa", "impressao_outbox"}.issubset(names)
        )


if __name__ == "__main__":
    unittest.main()
