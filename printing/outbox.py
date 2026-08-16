"""Worker serial para a fila durável de comprovantes."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from queue import Queue
from typing import Any

from db.database import Database
from services.audit import AuditService
from services.errors import ConflictError, NotFoundError, ValidationError
from services.security import can_access_cash, get_active_user


class PrintOutboxWorker:
    """Imprime fora da UI e mantém o resultado no SQLite."""

    def __init__(self, database: Database, printer_provider: Callable[[], Any]) -> None:
        self.database = database
        self.printer_provider = printer_provider
        self._queue: Queue[int | None] = Queue()
        self._queued: set[int] = set()
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="trigopdv-impressao-outbox",
        )
        self._thread.start()
        self.resume_pending()

    def resume_pending(self) -> None:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT id FROM impressao_outbox WHERE status = 'PENDENTE' ORDER BY id"
            ).fetchall()
        for row in rows:
            self.enqueue(int(row["id"]))

    def enqueue(self, job_id: int) -> None:
        with self._lock:
            if job_id in self._queued:
                return
            self._queued.add(job_id)
        self._queue.put(job_id)

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                self._queue.task_done()
                return
            try:
                self._process(job_id)
            finally:
                with self._lock:
                    self._queued.discard(job_id)
                self._queue.task_done()

    def _process(self, job_id: int) -> None:
        with self.database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM impressao_outbox WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None or row["status"] != "PENDENTE":
                return
            job = dict(row)
            connection.execute(
                "UPDATE impressao_outbox SET tentativas = tentativas + 1, "
                "atualizado_em = CURRENT_TIMESTAMP WHERE id = ?",
                (job_id,),
            )
        try:
            payload = json.loads(str(job["payload"]))
            if not isinstance(payload, dict):
                raise ValueError("payload inválido")
            result = self.printer_provider().print_receipt(payload)
            printed = bool(getattr(result, "printed", False))
            message = " ".join(str(getattr(result, "message", "")).split())[:400]
        except Exception:
            printed = False
            message = "Não foi possível processar o comprovante armazenado."

        with self.database.transaction(write=True) as connection:
            if printed:
                connection.execute(
                    "UPDATE impressao_outbox SET status = 'IMPRESSO', ultimo_erro = NULL, "
                    "impresso_em = CURRENT_TIMESTAMP, atualizado_em = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND status = 'PENDENTE'",
                    (job_id,),
                )
            else:
                connection.execute(
                    "UPDATE impressao_outbox SET status = 'FALHOU', ultimo_erro = ?, "
                    "atualizado_em = CURRENT_TIMESTAMP WHERE id = ? AND status = 'PENDENTE'",
                    (message or "A impressora não confirmou o comprovante.", job_id),
                )
            AuditService.record(
                connection,
                "COMPROVANTE_IMPRESSO" if printed else "COMPROVANTE_FALHOU",
                "VENDA",
                entity_id=job["venda_id"],
                actor_id=job["solicitado_por"],
                details={
                    "impressao_outbox_id": job_id,
                    "tipo": job["tipo"],
                    "mensagem": message,
                },
            )

    def retry(self, job_id: int, *, actor_id: int) -> dict:
        try:
            safe_job_id = int(job_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Solicitação de impressão inválida.") from exc
        with self.database.transaction(write=True) as connection:
            actor = get_active_user(connection, actor_id)
            row = connection.execute(
                "SELECT o.*, c.usuario_id AS caixa_usuario_id FROM impressao_outbox o "
                "JOIN vendas v ON v.id = o.venda_id JOIN caixas c ON c.id = v.caixa_id "
                "WHERE o.id = ?",
                (safe_job_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError("Solicitação de impressão não encontrada.")
            job = dict(row)
            can_access_cash(
                connection, {"usuario_id": job["caixa_usuario_id"]}, actor["id"]
            )
            if job["status"] == "IMPRESSO":
                raise ConflictError("Este comprovante já foi impresso.")
            connection.execute(
                "UPDATE impressao_outbox SET status = 'PENDENTE', ultimo_erro = NULL, "
                "atualizado_em = CURRENT_TIMESTAMP WHERE id = ?",
                (safe_job_id,),
            )
            AuditService.record(
                connection,
                "COMPROVANTE_REENVIADO",
                "VENDA",
                entity_id=job["venda_id"],
                actor_id=actor["id"],
                details={"impressao_outbox_id": safe_job_id},
            )
            updated = dict(
                connection.execute(
                    "SELECT * FROM impressao_outbox WHERE id = ?", (safe_job_id,)
                ).fetchone()
            )
        self.enqueue(safe_job_id)
        return updated

    def shutdown(self, timeout: float = 2.0) -> None:
        if not self._thread.is_alive():
            return
        self._queue.put(None)
        self._thread.join(timeout=max(0.0, timeout))
