"""Conexões, transações e inicialização segura do SQLite."""

from __future__ import annotations

import sqlite3
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Generator, Iterable, Optional
from uuid import uuid4

from .schema import SCHEMA_VERSION

if TYPE_CHECKING:
    from .migrations import MigrationResult


class DatabaseError(RuntimeError):
    """Erro de persistência com mensagem segura para exibição na interface."""


class Database:
    """Pequena unidade de trabalho para um banco SQLite local.

    Cada operação recebe sua conexão e sempre fecha o arquivo. Escritas usam
    ``BEGIN IMMEDIATE`` para evitar que duas confirmações de venda baixem o
    mesmo estoque simultaneamente.
    """

    def __init__(self, path: str | Path):
        raw_path = str(path)
        self.path = Path(raw_path) if raw_path != ":memory:" else Path(raw_path)
        self._memory = raw_path == ":memory:"
        self._lifecycle_lock = threading.Lock()
        self._closed = False
        # SQLite cria um banco novo para cada conexão com ':memory:'. Para
        # testes/injeção de dependência, usa URI compartilhada e uma conexão
        # âncora que mantém o banco vivo durante a instância Database. O nome
        # precisa ser independente do endereço reutilizável do objeto Python:
        # uma conexão ainda viva não pode contaminar uma instância posterior.
        self._memory_uri = f"file:trigo_pdv_{uuid4().hex}?mode=memory&cache=shared" if self._memory else None
        self._memory_anchor: Optional[sqlite3.Connection] = None
        if self._memory:
            self._memory_anchor = self._connect_raw()

    def _connect_raw(self) -> sqlite3.Connection:
        with self._lifecycle_lock:
            if self._closed:
                raise DatabaseError("O banco de dados já foi fechado.")
            return sqlite3.connect(
                self._memory_uri if self._memory else str(self.path),
                uri=self._memory,
                timeout=10,
                isolation_level=None,
                check_same_thread=False,
            )

    def close(self) -> None:
        """Libera a conexão âncora e impede novas operações nesta instância."""

        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            anchor = self._memory_anchor
            self._memory_anchor = None
        if anchor is not None:
            try:
                anchor.close()
            except sqlite3.Error as exc:
                raise DatabaseError(f"Não foi possível fechar o banco de dados: {exc}") from exc

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            # Destrutores nunca devem propagar falhas durante GC/interpreter exit.
            pass

    def _connect_inspection(self) -> sqlite3.Connection:
        """Abre uma conexão de inspeção sem pragmas persistentes no arquivo."""

        if not self._memory:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise DatabaseError(f"Não foi possível criar a pasta do banco: {exc}") from exc
        try:
            connection = self._connect_raw()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Não foi possível abrir o banco de dados: {exc}") from exc
        connection.row_factory = sqlite3.Row
        return connection

    def _connect(self) -> sqlite3.Connection:
        if not self._memory:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise DatabaseError(f"Não foi possível criar a pasta do banco: {exc}") from exc
        try:
            connection = self._connect_raw()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Não foi possível abrir o banco de dados: {exc}") from exc
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            if not self._memory:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
        except sqlite3.Error as exc:
            connection.close()
            raise DatabaseError(f"Não foi possível preparar o banco de dados: {exc}") from exc
        return connection

    @contextmanager
    def transaction(self, *, write: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """Abre uma transação e garante rollback em qualquer exceção."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise DatabaseError(f"Erro ao acessar o banco de dados: {exc}") from exc
        except Exception:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def backup_to(self, destination: str | Path) -> Path:
        """Cria uma cópia verificada e substitui o destino somente ao concluir."""

        target_path = Path(destination)
        temporary_path = target_path.with_name(f".{target_path.name}.{uuid4().hex}.tmp")
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DatabaseError(f"Não foi possível preparar o destino do backup: {exc}") from exc
        source: Optional[sqlite3.Connection] = None
        target: Optional[sqlite3.Connection] = None
        try:
            source = self._connect_inspection()
            target = sqlite3.connect(str(temporary_path), timeout=10)
            source.backup(target)
            check = target.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise DatabaseError(f"A cópia de segurança falhou na verificação de integridade: {check}")
            target.close()
            target = None
            os.replace(temporary_path, target_path)
        except DatabaseError:
            if target is not None:
                target.close()
                target = None
            if temporary_path.exists():
                temporary_path.unlink(missing_ok=True)
            raise
        except (sqlite3.Error, OSError) as exc:
            if target is not None:
                target.close()
                target = None
            if temporary_path.exists():
                temporary_path.unlink(missing_ok=True)
            raise DatabaseError(f"Não foi possível criar o backup do banco: {exc}") from exc
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()
        return target_path

    def initialize(self, *, backup_dir: str | Path | None = None) -> MigrationResult:
        """Inicializa ou migra o schema, com backup antes de DDL existente."""

        from .migrations import MigrationManager, default_migrations

        folder = Path(backup_dir) if backup_dir is not None else None
        return MigrationManager(self, SCHEMA_VERSION, default_migrations()).migrate(backup_dir=folder)

    def fetch_one(self, sql: str, parameters: Iterable[object] = ()) -> Optional[dict]:
        with self.transaction() as connection:
            row = connection.execute(sql, tuple(parameters)).fetchone()
            return dict(row) if row is not None else None

    def fetch_all(self, sql: str, parameters: Iterable[object] = ()) -> list[dict]:
        with self.transaction() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
            return [dict(row) for row in rows]
