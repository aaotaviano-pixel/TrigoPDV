from __future__ import annotations

import sqlite3
from pathlib import Path
import unittest

from integrations.open_food_facts import normalize_gtin
from services.catalog_bootstrap import CATALOG_COLUMNS, CatalogManifest, logical_product_digest


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "TrigoPDV_Instalacao_PenDrive" / "dados-iniciais" / "catalogo-produtos.sqlite3"
MANIFEST = ROOT / "TrigoPDV_Instalacao_PenDrive" / "dados-iniciais" / "catalogo-produtos.manifest.json"


class DistributedProductCatalogTestCase(unittest.TestCase):
    def test_catalog_contains_only_validated_products(self) -> None:
        manifest = CatalogManifest.load(MANIFEST)
        connection = sqlite3.connect(f"{CATALOG.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            objects = connection.execute(
                "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()
            self.assertEqual([(row["type"], row["name"]) for row in objects], [("table", "produtos")])
            columns = [row["name"] for row in connection.execute("PRAGMA table_info(produtos)").fetchall()]
            self.assertEqual(columns, list(CATALOG_COLUMNS))
            rows = [dict(row) for row in connection.execute(
                f"SELECT {', '.join(CATALOG_COLUMNS)} FROM produtos ORDER BY gtin"
            ).fetchall()]
        finally:
            connection.close()
        self.assertEqual(len(rows), 192)
        gtins = [row for row in rows if row["tipo_codigo"] == "GTIN"]
        plus = [row for row in rows if row["tipo_codigo"] == "PLU"]
        self.assertEqual(len(gtins), 167)
        self.assertEqual(len(plus), 25)
        self.assertTrue(all(normalize_gtin(row["gtin"]) == row["gtin"] for row in gtins))
        self.assertTrue(all(float(row["preco"]) > 0 for row in rows))
        self.assertEqual(len({row["gtin"] for row in rows}), len(rows))
        self.assertEqual(manifest.product_count, len(rows))
        self.assertEqual(manifest.logical_digest, logical_product_digest(rows))


if __name__ == "__main__":
    unittest.main()
