from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from db.schema import SCHEMA_VERSION
from services.catalog_bootstrap import (
    CATALOG_COLUMNS,
    CatalogBootstrapError,
    bootstrap_database_from_catalog,
    logical_product_digest,
)
from init_db import initialize


class CatalogBootstrapTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.catalog = self.root / "catalogo-produtos.sqlite3"
        self.manifest = self.root / "catalogo-produtos.manifest.json"
        self.rows = [
            {
                "gtin": "7894900011517", "nome": "Refrigerante 2L", "marca": "Marca A", "preco": 10.5,
                "estoque": 0.0, "data_validade": None, "unidade": "UN", "estoque_controlado": 0,
                "item_balcao": 0, "ativo": 1, "origem": "manual", "tipo_codigo": "GTIN",
                "categoria": "Bebidas", "subcategoria": None, "detalhes_embalagem": "2 L",
                "validacao_codigo": "CONFIRMADO", "fonte_validacao": "catalogo", "validado_em": None,
                "criado_em": "2026-08-15 00:00:00", "atualizado_em": "2026-08-15 00:00:00",
            },
            {
                "gtin": "PLU1001", "nome": "Pão francês", "marca": None, "preco": 18.0,
                "estoque": 0.0, "data_validade": None, "unidade": "KG", "estoque_controlado": 0,
                "item_balcao": 1, "ativo": 1, "origem": "manual", "tipo_codigo": "PLU",
                "categoria": "Padaria", "subcategoria": None, "detalhes_embalagem": None,
                "validacao_codigo": "VALIDO_INTERNO", "fonte_validacao": "interno", "validado_em": None,
                "criado_em": "2026-08-15 00:00:00", "atualizado_em": "2026-08-15 00:00:00",
            },
        ]
        connection = sqlite3.connect(self.catalog)
        definitions = (
            "gtin TEXT PRIMARY KEY, nome TEXT NOT NULL, marca TEXT, preco REAL NOT NULL, estoque REAL NOT NULL, "
            "data_validade TEXT, unidade TEXT NOT NULL, estoque_controlado INTEGER NOT NULL, item_balcao INTEGER NOT NULL, "
            "ativo INTEGER NOT NULL, origem TEXT NOT NULL, tipo_codigo TEXT NOT NULL, categoria TEXT NOT NULL, "
            "subcategoria TEXT, detalhes_embalagem TEXT, validacao_codigo TEXT NOT NULL, fonte_validacao TEXT, "
            "validado_em TEXT, criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL"
        )
        connection.execute(f"CREATE TABLE produtos ({definitions})")
        connection.executemany(
            f"INSERT INTO produtos({', '.join(CATALOG_COLUMNS)}) VALUES ({', '.join('?' for _ in CATALOG_COLUMNS)})",
            [tuple(row[column] for column in CATALOG_COLUMNS) for row in self.rows],
        )
        connection.commit()
        connection.close()
        self._write_manifest()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_manifest(self) -> None:
        self.manifest.write_text(json.dumps({
            "format_version": 1,
            "product_count": len(self.rows),
            "file_sha256": hashlib.sha256(self.catalog.read_bytes()).hexdigest(),
            "logical_digest": logical_product_digest(self.rows),
        }), encoding="utf-8")

    def test_two_installs_are_v9_empty_and_receive_distinct_ids(self) -> None:
        identifiers = []
        for name in ("loja-a.sqlite3", "loja-b.sqlite3"):
            target = self.root / name
            self.assertTrue(bootstrap_database_from_catalog(target, self.catalog, self.manifest))
            connection = sqlite3.connect(target)
            try:
                self.assertEqual(int(connection.execute(
                    "SELECT valor FROM schema_meta WHERE chave='schema_version'"
                ).fetchone()[0]), SCHEMA_VERSION)
                identifiers.append(connection.execute(
                    "SELECT installation_id FROM installation_state WHERE state='UNINITIALIZED'"
                ).fetchone()[0])
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM produtos").fetchone()[0], 2)
                for table in ("usuarios", "caixas", "vendas", "itens_venda", "movimentacoes_caixa", "logs_auditoria", "cache_gtin"):
                    self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            finally:
                connection.close()
        self.assertNotEqual(identifiers[0], identifiers[1])

    def test_existing_database_is_preserved_byte_for_byte(self) -> None:
        target = self.root / "existing.sqlite3"
        target.write_bytes(b"banco-existente")
        before = target.read_bytes()
        self.assertFalse(bootstrap_database_from_catalog(target, self.catalog, self.manifest))
        self.assertEqual(target.read_bytes(), before)

    def test_orphan_sidecar_and_pending_update_fail_without_creating_database(self) -> None:
        for suffix in ("-wal", "-shm"):
            target = self.root / f"orphan{suffix}.sqlite3"
            sidecar = Path(f"{target}{suffix}")
            sidecar.write_bytes(b"preservar")
            with self.assertRaises(CatalogBootstrapError):
                bootstrap_database_from_catalog(target, self.catalog, self.manifest)
            self.assertFalse(target.exists())
            self.assertEqual(sidecar.read_bytes(), b"preservar")
        target = self.root / "pending.sqlite3"
        with self.assertRaises(CatalogBootstrapError):
            bootstrap_database_from_catalog(target, self.catalog, self.manifest, update_pending=True)
        self.assertFalse(target.exists())

    def test_tampered_catalog_is_rejected_without_staging_residue(self) -> None:
        connection = sqlite3.connect(self.catalog)
        connection.execute("UPDATE produtos SET nome='Adulterado' WHERE gtin='7894900011517'")
        connection.commit()
        connection.close()
        target = self.root / "new.sqlite3"
        with self.assertRaises(CatalogBootstrapError):
            bootstrap_database_from_catalog(target, self.catalog, self.manifest)
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob(".*.staging")))

    def test_real_initializer_uses_packaged_catalog_before_database_services(self) -> None:
        configuration = self.root / "install" / "config.ini"
        database_path = initialize(configuration)
        connection = sqlite3.connect(database_path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM produtos").fetchone()[0], 192)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT state FROM installation_state WHERE singleton=1"
            ).fetchone()[0], "UNINITIALIZED")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
