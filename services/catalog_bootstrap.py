"""Criação atômica do banco operacional a partir de catálogo sem identidade."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Iterable, Mapping
from uuid import uuid4

from db.database import Database
from db.schema import SCHEMA_VERSION


CATALOG_COLUMNS = (
    "gtin", "nome", "marca", "preco", "estoque", "data_validade", "unidade",
    "estoque_controlado", "item_balcao", "ativo", "origem", "tipo_codigo",
    "categoria", "subcategoria", "detalhes_embalagem", "validacao_codigo",
    "fonte_validacao", "validado_em", "criado_em", "atualizado_em",
)


class CatalogBootstrapError(RuntimeError):
    """Falha segura; banco operacional existente nunca é alterado."""


@dataclass(frozen=True)
class CatalogManifest:
    format_version: int
    product_count: int
    file_sha256: str
    logical_digest: str

    @classmethod
    def load(cls, path: str | Path) -> "CatalogManifest":
        try:
            values = json.loads(Path(path).read_text(encoding="utf-8"))
            manifest = cls(
                format_version=int(values["format_version"]),
                product_count=int(values["product_count"]),
                file_sha256=str(values["file_sha256"]).lower(),
                logical_digest=str(values["logical_digest"]).lower(),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CatalogBootstrapError("O manifesto do catálogo é inválido.") from exc
        if manifest.format_version != 1 or manifest.product_count < 0:
            raise CatalogBootstrapError("O manifesto do catálogo é incompatível.")
        for value in (manifest.file_sha256, manifest.logical_digest):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise CatalogBootstrapError("O manifesto do catálogo é inválido.")
        return manifest


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_value(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, (int, bool)):
        return int(value)
    if isinstance(value, float):
        decimal = Decimal(str(value))
        return format(decimal.normalize(), "f")
    return str(value)


def logical_product_digest(rows: Iterable[Mapping[str, object]]) -> str:
    canonical = [
        [_canonical_value(row[column]) for column in CATALOG_COLUMNS]
        for row in sorted(rows, key=lambda item: str(item["gtin"]))
    ]
    payload = json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _open_catalog(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
        connection.row_factory = sqlite3.Row
        return connection
    except sqlite3.Error as exc:
        raise CatalogBootstrapError("Não foi possível abrir o catálogo de produtos.") from exc


def _validated_catalog_rows(catalog_path: Path, manifest: CatalogManifest) -> list[dict]:
    if not catalog_path.is_file() or _file_sha256(catalog_path) != manifest.file_sha256:
        raise CatalogBootstrapError("O catálogo de produtos falhou na verificação de integridade.")
    connection = _open_catalog(catalog_path)
    try:
        objects = connection.execute(
            "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        if [(row["type"], row["name"]) for row in objects] != [("table", "produtos")]:
            raise CatalogBootstrapError("O catálogo contém estruturas não permitidas.")
        columns = [row["name"] for row in connection.execute("PRAGMA table_info(produtos)").fetchall()]
        if columns != list(CATALOG_COLUMNS):
            raise CatalogBootstrapError("A estrutura do catálogo é incompatível.")
        rows = [dict(row) for row in connection.execute(
            f"SELECT {', '.join(CATALOG_COLUMNS)} FROM produtos ORDER BY gtin"
        ).fetchall()]
    except sqlite3.Error as exc:
        raise CatalogBootstrapError("O catálogo de produtos é inválido.") from exc
    finally:
        connection.close()
    if len(rows) != manifest.product_count or logical_product_digest(rows) != manifest.logical_digest:
        raise CatalogBootstrapError("O conteúdo do catálogo não corresponde ao manifesto.")
    return rows


def bootstrap_database_from_catalog(
    database_path: str | Path,
    catalog_path: str | Path,
    manifest_path: str | Path,
    *,
    update_pending: bool = False,
) -> bool:
    """Cria um banco fresh v9; retorna ``False`` se ele já existe.

    Deve ser chamado sob ``SingleInstanceGuard``. Sidecars órfãos bloqueiam a
    operação e nenhum arquivo operacional é removido.
    """

    target = Path(database_path)
    if target.exists():
        return False
    if update_pending:
        raise CatalogBootstrapError("A instalação precisa concluir a atualização antes do primeiro uso.")
    sidecars = (Path(f"{target}-wal"), Path(f"{target}-shm"))
    if any(path.exists() for path in sidecars):
        raise CatalogBootstrapError("Foram encontrados arquivos pendentes do banco; a instalação foi preservada.")
    manifest = CatalogManifest.load(manifest_path)
    rows = _validated_catalog_rows(Path(catalog_path), manifest)
    target.parent.mkdir(parents=True, exist_ok=True)
    reservation = target.with_name(f".{target.name}.bootstrap.lock")
    try:
        descriptor = os.open(reservation, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
    except FileExistsError as exc:
        raise CatalogBootstrapError("Outra inicialização do banco já está em andamento.") from exc
    except OSError as exc:
        raise CatalogBootstrapError("Não foi possível reservar a inicialização do banco.") from exc

    staging = target.with_name(f".{target.name}.{uuid4().hex}.staging")
    staging_sidecars = (Path(f"{staging}-wal"), Path(f"{staging}-shm"))
    published = False
    try:
        if target.exists() or any(path.exists() for path in sidecars):
            raise CatalogBootstrapError("O banco local mudou durante a inicialização; nada foi substituído.")
        database = Database(staging)
        database.initialize(backup_dir=target.parent / "backups")
        placeholders = ", ".join("?" for _ in CATALOG_COLUMNS)
        with database.transaction(write=True) as connection:
            connection.executemany(
                f"INSERT INTO produtos({', '.join(CATALOG_COLUMNS)}) VALUES ({placeholders})",
                [tuple(row[column] for column in CATALOG_COLUMNS) for row in rows],
            )
        connection = sqlite3.connect(staging)
        connection.row_factory = sqlite3.Row
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise CatalogBootstrapError("O banco novo falhou na verificação de integridade.")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise CatalogBootstrapError("O banco novo falhou na verificação de vínculos.")
            version = int(connection.execute(
                "SELECT valor FROM schema_meta WHERE chave = 'schema_version'"
            ).fetchone()[0])
            state = connection.execute(
                "SELECT installation_id, state FROM installation_state WHERE singleton = 1"
            ).fetchone()
            operational_counts = sum(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("usuarios", "caixas", "vendas", "itens_venda", "movimentacoes_caixa", "logs_auditoria", "cache_gtin")
            )
            operational_rows = [dict(row) for row in connection.execute(
                f"SELECT {', '.join(CATALOG_COLUMNS)} FROM produtos ORDER BY gtin"
            ).fetchall()]
            if version != SCHEMA_VERSION or state is None or state["state"] != "UNINITIALIZED":
                raise CatalogBootstrapError("O banco novo não corresponde à versão desta aplicação.")
            if operational_counts or logical_product_digest(operational_rows) != manifest.logical_digest:
                raise CatalogBootstrapError("O banco novo contém dados operacionais ou catálogo divergente.")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode=DELETE")
        finally:
            connection.close()
        if any(path.exists() for path in staging_sidecars):
            raise CatalogBootstrapError("O banco novo deixou arquivos pendentes e não será publicado.")
        if target.exists() or any(path.exists() for path in sidecars):
            raise CatalogBootstrapError("O banco local mudou durante a inicialização; nada foi substituído.")
        os.replace(staging, target)
        published = True
        return True
    except CatalogBootstrapError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise CatalogBootstrapError("Não foi possível preparar o banco local de produtos.") from exc
    finally:
        if not published:
            for path in (staging, *staging_sidecars):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        try:
            reservation.unlink(missing_ok=True)
        except OSError:
            pass

