"""Exporta somente produtos de um banco auditado para um catálogo novo."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.catalog_bootstrap import CATALOG_COLUMNS, logical_product_digest


def export_catalog(source: Path, destination: Path, manifest_path: Path) -> None:
    source_connection = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    source_connection.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in source_connection.execute(
            f"SELECT {', '.join(CATALOG_COLUMNS)} FROM produtos WHERE ativo = 1 ORDER BY gtin"
        ).fetchall()]
    finally:
        source_connection.close()
    if not rows:
        raise RuntimeError("A origem não contém produtos ativos.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.{uuid4().hex}.staging")
    try:
        connection = sqlite3.connect(staging)
        definitions = (
            "gtin TEXT PRIMARY KEY, nome TEXT NOT NULL, marca TEXT, preco REAL NOT NULL, estoque REAL NOT NULL, "
            "data_validade TEXT, unidade TEXT NOT NULL, estoque_controlado INTEGER NOT NULL, item_balcao INTEGER NOT NULL, "
            "ativo INTEGER NOT NULL, origem TEXT NOT NULL, tipo_codigo TEXT NOT NULL, categoria TEXT NOT NULL, "
            "subcategoria TEXT, detalhes_embalagem TEXT, validacao_codigo TEXT NOT NULL, fonte_validacao TEXT, "
            "validado_em TEXT, criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL"
        )
        try:
            connection.execute(f"CREATE TABLE produtos ({definitions})")
            placeholders = ", ".join("?" for _ in CATALOG_COLUMNS)
            connection.executemany(
                f"INSERT INTO produtos({', '.join(CATALOG_COLUMNS)}) VALUES ({placeholders})",
                [tuple(row[column] for column in CATALOG_COLUMNS) for row in rows],
            )
            connection.commit()
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("O catálogo exportado falhou na verificação.")
        finally:
            connection.close()
        os.replace(staging, destination)
    finally:
        staging.unlink(missing_ok=True)
    file_digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    manifest = {
        "format_version": 1,
        "product_count": len(rows),
        "file_sha256": file_digest,
        "logical_digest": logical_product_digest(rows),
    }
    temporary_manifest = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_manifest, manifest_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    export_catalog(args.source, args.destination, args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

