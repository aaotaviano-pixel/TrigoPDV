"""Backup consistente via API SQLite e manutenção administrativa."""

from __future__ import annotations

import sqlite3
import threading
from queue import Queue
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from db.database import Database

from .audit import AuditService
from .errors import BackupError, ValidationError
from .security import require_admin


class BackupService:
    def __init__(self, database: Database, backup_path: str | Path, *, audit: Optional[AuditService] = None):
        self.database = database
        self.backup_path = Path(backup_path)
        self.audit = audit or AuditService(database)

    @staticmethod
    def _destination_folder(value: str | Path) -> Path:
        folder = Path(value).expanduser()
        if folder.exists() and not folder.is_dir():
            raise BackupError("O destino de backup deve ser uma pasta.")
        return folder

    @staticmethod
    def _backup_name() -> str:
        return f"backup-trigo-de-minas-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.sqlite3"

    def backup_database(self, destination: str | Path | None = None) -> Path:
        """Cria uma cópia consistente inclusive quando WAL está em uso."""

        folder = self._destination_folder(destination or self.backup_path)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BackupError(f"Não foi possível criar a pasta de backup: {exc}") from exc
        target_path = folder / self._backup_name()
        source: Optional[sqlite3.Connection] = None
        target: Optional[sqlite3.Connection] = None
        try:
            source = self.database._connect()  # A mesma configuração de timeout/foreign keys do app.
            target = sqlite3.connect(str(target_path), timeout=10)
            source.backup(target)
            check = target.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise BackupError(f"A cópia de segurança falhou na verificação de integridade: {check}")
        except BackupError:
            if target is not None:
                target.close()
                target = None
            if target_path.exists():
                target_path.unlink(missing_ok=True)
            raise
        except (sqlite3.Error, OSError) as exc:
            if target is not None:
                target.close()
                target = None
            if target_path.exists():
                target_path.unlink(missing_ok=True)
            raise BackupError(f"Não foi possível criar o backup do banco: {exc}") from exc
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()
        return target_path

    def create_backup(self, *, actor_id: int, destination: str | Path | None = None) -> Path:
        with self.database.transaction(write=True) as connection:
            require_admin(connection, actor_id)
            AuditService.record(connection, "BACKUP_MANUAL_SOLICITADO", "BACKUP", actor_id=actor_id)
        path = self.backup_database(destination)
        self.audit.log("BACKUP_MANUAL_CONCLUIDO", "BACKUP", actor_id=actor_id, details={"arquivo": str(path)})
        return path

    def integrity_check(self, *, actor_id: int) -> str:
        with self.database.transaction() as connection:
            require_admin(connection, actor_id)
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            return str(result)

    def _maintenance(self, command: str, *, actor_id: int) -> dict:
        if command not in {"VACUUM", "REINDEX"}:
            raise ValidationError("Comando de manutenção inválido.")
        # Cópia prévia garante um ponto de retorno caso o SQLite/disco falhe.
        with self.database.transaction() as connection:
            require_admin(connection, actor_id)
        pre_backup = self.backup_database()
        connection: Optional[sqlite3.Connection] = None
        try:
            # VACUUM não pode rodar dentro de BEGIN; _connect usa autocommit.
            connection = self.database._connect()
            require_admin(connection, actor_id)
            connection.execute(command)
            check = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise BackupError(f"A manutenção terminou com integridade inválida: {check}")
            AuditService.record(
                connection,
                f"MANUTENCAO_{command}",
                "BANCO_DADOS",
                actor_id=actor_id,
                details={"backup_previo": str(pre_backup), "integridade": check},
            )
        except BackupError:
            raise
        except sqlite3.Error as exc:
            raise BackupError(f"Não foi possível executar {command}: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()
        return {"operacao": command, "backup_previo": str(pre_backup), "integridade": "ok"}

    def vacuum(self, *, actor_id: int) -> dict:
        return self._maintenance("VACUUM", actor_id=actor_id)

    def reindex(self, *, actor_id: int) -> dict:
        return self._maintenance("REINDEX", actor_id=actor_id)


class CashBackupWorker:
    """Processa backups de fechamento sem bloquear a thread Tk."""

    def __init__(
        self,
        database: Database,
        backup_service: Any,
        *,
        audit: Optional[AuditService] = None,
    ) -> None:
        self.database = database
        self.backup_service = backup_service
        self.audit = audit or AuditService(database)
        self._queue: Queue[int | None] = Queue()
        self._queued: set[int] = set()
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="trigopdv-backup-caixa",
        )
        self._thread.start()
        self.resume_pending()

    def resume_pending(self) -> None:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT caixa_id FROM backups_caixa WHERE status = 'PENDENTE' ORDER BY id"
            ).fetchall()
        for row in rows:
            self.enqueue(int(row["caixa_id"]))

    def enqueue(self, cash_id: int) -> None:
        with self._lock:
            if cash_id in self._queued:
                return
            self._queued.add(cash_id)
        self._queue.put(cash_id)

    def _run(self) -> None:
        while True:
            cash_id = self._queue.get()
            if cash_id is None:
                self._queue.task_done()
                return
            try:
                self._process(cash_id)
            finally:
                with self._lock:
                    self._queued.discard(cash_id)
                self._queue.task_done()

    def _process(self, cash_id: int) -> None:
        actor_id: int | None = None
        with self.database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM backups_caixa WHERE caixa_id = ?", (cash_id,)
            ).fetchone()
            if row is None or row["status"] != "PENDENTE":
                return
            actor_id = row["solicitado_por"]
            connection.execute(
                "UPDATE backups_caixa SET tentativas = tentativas + 1, "
                "atualizado_em = CURRENT_TIMESTAMP WHERE caixa_id = ?",
                (cash_id,),
            )
        try:
            path = self.backup_service.backup_database()
        except Exception as exc:
            message = " ".join(str(exc).split())[:400] or "Falha não detalhada."
            with self.database.transaction(write=True) as connection:
                connection.execute(
                    "UPDATE backups_caixa SET status = 'FALHOU', ultimo_erro = ?, "
                    "atualizado_em = CURRENT_TIMESTAMP WHERE caixa_id = ? AND status = 'PENDENTE'",
                    (message, cash_id),
                )
                AuditService.record(
                    connection,
                    "BACKUP_CAIXA_FALHOU",
                    "CAIXA",
                    entity_id=cash_id,
                    actor_id=actor_id,
                    details={"mensagem": message},
                )
            return
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE backups_caixa SET status = 'CONCLUIDO', arquivo = ?, ultimo_erro = NULL, "
                "concluido_em = CURRENT_TIMESTAMP, atualizado_em = CURRENT_TIMESTAMP "
                "WHERE caixa_id = ? AND status = 'PENDENTE'",
                (str(path), cash_id),
            )
            AuditService.record(
                connection,
                "BACKUP_CAIXA_CONCLUIDO",
                "CAIXA",
                entity_id=cash_id,
                actor_id=actor_id,
                details={"arquivo": str(path)},
            )

    def shutdown(self, timeout: float = 2.0) -> None:
        if not self._thread.is_alive():
            return
        self._queue.put(None)
        self._thread.join(timeout=max(0.0, timeout))
