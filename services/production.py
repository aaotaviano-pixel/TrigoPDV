"""Preparacao unica e recuperavel do banco antes da abertura comercial."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from db.database import Database

from .audit import AuditService
from .backup import BackupService
from .errors import ConflictError, ValidationError
from .security import require_admin


PRODUCTION_CONFIRMATION = "INICIAR PRODUCAO"
PRODUCTION_PREPARED_KEY = "production_prepared_at"

_OPERATIONAL_TABLES = (
    "impressao_outbox",
    "backups_caixa",
    "cancelamentos_venda",
    "itens_venda",
    "movimentacoes_caixa",
    "vendas",
    "caixas",
    "logs_auditoria",
)


class ProductionPreparationService:
    """Remove testes uma unica vez, sempre depois de um backup verificado."""

    def __init__(self, database: Database, backup_service: BackupService) -> None:
        self.database = database
        self.backup_service = backup_service

    @staticmethod
    def _normalize_confirmation(value: Any) -> str:
        return " ".join(str(value or "").strip().upper().split())

    @staticmethod
    def _assert_ready(connection: Any, actor_id: int) -> None:
        require_admin(connection, actor_id)
        prepared = connection.execute(
            "SELECT valor FROM schema_meta WHERE chave = ?",
            (PRODUCTION_PREPARED_KEY,),
        ).fetchone()
        if prepared is not None:
            raise ConflictError(
                "A preparação inicial já foi concluída. Para proteger as vendas reais, "
                "esta limpeza não pode ser repetida."
            )
        if connection.execute(
            "SELECT 1 FROM caixas WHERE status = 'ABERTO' LIMIT 1"
        ).fetchone():
            raise ConflictError("Feche o caixa antes de preparar o PDV para produção.")
        if connection.execute(
            "SELECT 1 FROM backups_caixa WHERE status = 'PENDENTE' LIMIT 1"
        ).fetchone():
            raise ConflictError("Aguarde a conclusão do backup de fechamento antes de continuar.")
        if connection.execute(
            "SELECT 1 FROM impressao_outbox WHERE status = 'PENDENTE' LIMIT 1"
        ).fetchone():
            raise ConflictError("Aguarde a impressão pendente antes de continuar.")

    def status(self, *, actor_id: int) -> dict[str, Any]:
        with self.database.transaction() as connection:
            require_admin(connection, actor_id)
            row = connection.execute(
                "SELECT valor FROM schema_meta WHERE chave = ?",
                (PRODUCTION_PREPARED_KEY,),
            ).fetchone()
            return {
                "prepared": row is not None,
                "prepared_at": str(row["valor"]) if row is not None else "",
            }

    def prepare(self, *, actor_id: int, confirmation: str) -> dict[str, Any]:
        if self._normalize_confirmation(confirmation) != PRODUCTION_CONFIRMATION:
            raise ValidationError(f"Digite {PRODUCTION_CONFIRMATION} para confirmar.")

        # Valida primeiro para não criar uma cópia desnecessária de um estado bloqueado.
        with self.database.transaction() as connection:
            self._assert_ready(connection, actor_id)

        backup_path = self.backup_service.backup_database()
        prepared_at = datetime.now().astimezone().isoformat(timespec="seconds")

        with self.database.transaction(write=True) as connection:
            # Revalida sob lock de escrita para evitar corrida com abertura de caixa.
            self._assert_ready(connection, actor_id)
            counts = {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in _OPERATIONAL_TABLES
            }
            for table in _OPERATIONAL_TABLES:
                connection.execute(f"DELETE FROM {table}")

            connection.execute(
                "UPDATE produtos SET estoque = 0, estoque_controlado = 0, "
                "atualizado_em = CURRENT_TIMESTAMP"
            )
            sequence_tables = _OPERATIONAL_TABLES
            placeholders = ", ".join("?" for _ in sequence_tables)
            connection.execute(
                f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                sequence_tables,
            )
            connection.execute(
                "INSERT INTO schema_meta(chave, valor) VALUES (?, ?)",
                (PRODUCTION_PREPARED_KEY, prepared_at),
            )
            AuditService.record(
                connection,
                "PREPARACAO_PRODUCAO_CONCLUIDA",
                "BANCO_DADOS",
                actor_id=actor_id,
                details={
                    "backup_previo": str(Path(backup_path)),
                    "registros_removidos": counts,
                    "estoque_requer_inventario": True,
                },
            )
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise ConflictError(
                    "A verificação final do banco falhou. Nenhum dado foi removido."
                )

        return {
            "prepared_at": prepared_at,
            "backup_path": str(backup_path),
            "removed": counts,
            "integrity": "ok",
            "inventory_required": True,
        }
