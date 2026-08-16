from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from config.version import RELEASE
from db.database import Database
from .health import run_health_check
from .models import UpdateOffer, UpdatePhase, UpdatePolicy, cohort_eligible
from .state import UpdateState, UpdateStateStore


class UpdateCoordinatorError(RuntimeError):
    pass


class UpdateRestartScheduled(UpdateCoordinatorError):
    pass


class UpdateCoordinator:
    def __init__(
        self,
        *,
        policy: UpdatePolicy,
        state_store: UpdateStateStore,
        database_path: str | Path,
        backup_directory: str | Path,
        adapter,
        repository=None,
        rollback_callback: Callable[[UpdateState], None] | None = None,
        event_logger=None,
    ):
        self.policy = policy
        self.state_store = state_store
        self.database_path = Path(database_path)
        self.backup_directory = Path(backup_directory)
        self.adapter = adapter
        self.repository = repository
        self.rollback_callback = rollback_callback
        self.event_logger = event_logger

    def _log(self, event: str, **fields: object) -> None:
        if self.event_logger is None:
            return
        try:
            self.event_logger.write(event, **fields)
        except Exception:
            # O log é diagnóstico; nunca substitui a decisão segura do estado.
            pass

    def has_pending_update(self) -> bool:
        return self.state_store.load().phase in {
            UpdatePhase.DOWNLOADING, UpdatePhase.DOWNLOADED, UpdatePhase.PREPARING,
            UpdatePhase.APPLY_PENDING, UpdatePhase.HEALTH_CHECK, UpdatePhase.ROLLED_BACK,
        }

    def check_now(self, installation_id: str) -> UpdateOffer | None:
        if not self.policy.enabled:
            return None
        if self.repository is None:
            raise UpdateCoordinatorError("O repositório seguro de atualização não está configurado.")
        try:
            self._log("check_started", channel=self.policy.channel, phase="AVAILABLE")
            offer = self.repository.check_offer(f"channels/{self.policy.channel}/manifest.json")
            current = self.state_store.load()
            offer.validate(self.policy, current_sequence=current.current_sequence)
            if not cohort_eligible(installation_id, offer.rollout_seed, offer.rollout_percent):
                return None
            self.state_store.save(replace(
                current, phase=UpdatePhase.AVAILABLE, target_version=offer.version,
                target_sequence=offer.sequence, target_schema=offer.schema_target, error_code="",
            ))
            self._log("check_finished", outcome="AVAILABLE", version=offer.version, sequence=offer.sequence)
            return offer
        except UpdateCoordinatorError:
            raise
        except Exception as exc:
            raise UpdateCoordinatorError("Não foi possível verificar atualizações com segurança.") from exc

    def download(self, offer: UpdateOffer) -> Path:
        if self.repository is None:
            raise UpdateCoordinatorError("O repositório seguro de atualização não está configurado.")
        current = self.state_store.load()
        self._log("download_started", version=offer.version, sequence=offer.sequence)
        self.state_store.save(replace(current, phase=UpdatePhase.DOWNLOADING, error_code=""))
        try:
            bundle = Path(self.repository.download_bundle(offer)).resolve()
            self.state_store.save(replace(
                current, phase=UpdatePhase.DOWNLOADED, target_version=offer.version,
                target_sequence=offer.sequence, target_schema=offer.schema_target,
                bundle_directory=str(bundle), error_code="",
            ))
            self._log("download_finished", outcome="OK", version=offer.version, sequence=offer.sequence)
            return bundle
        except Exception as exc:
            self.state_store.save(replace(current, phase=UpdatePhase.FAILED, error_code="DOWNLOAD"))
            raise UpdateCoordinatorError("O download autenticado da atualização falhou.") from exc

    def prepare_apply(
        self,
        offer: UpdateOffer,
        bundle_directory: str | Path,
        *,
        safe_to_apply: Callable[[], bool],
        restart_args: list[str] | None = None,
    ) -> None:
        if not safe_to_apply():
            raise UpdateCoordinatorError("Feche o caixa antes de aplicar a atualização.")
        current = self.state_store.load()
        self.state_store.save(replace(
            current, phase=UpdatePhase.PREPARING, target_version=offer.version,
            target_sequence=offer.sequence, target_schema=offer.schema_target,
            bundle_directory=str(Path(bundle_directory).resolve()), error_code="",
        ))
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self.backup_directory / f"pre_update_{offer.sequence}_{timestamp}.sqlite3"
        try:
            Database(self.database_path).backup_to(backup)
            self._log("backup_finished", outcome="OK", sequence=offer.sequence)
            pending = replace(
                current, phase=UpdatePhase.APPLY_PENDING, target_version=offer.version,
                target_sequence=offer.sequence, target_schema=offer.schema_target,
                bundle_directory=str(Path(bundle_directory).resolve()), database_backup=str(backup.resolve()),
                attempts=current.attempts + 1, error_code="",
            )
            self.state_store.save(pending)
            self._log("apply_scheduled", version=offer.version, sequence=offer.sequence)
            self.adapter.apply_local_bundle(bundle_directory, restart_args=restart_args)
        except Exception as exc:
            latest = self.state_store.load()
            self.state_store.save(replace(latest, phase=UpdatePhase.FAILED, error_code="APPLY"))
            raise UpdateCoordinatorError("A aplicação da atualização foi interrompida com segurança.") from exc

    def resume_pending_update(self) -> bool:
        state = self.state_store.load()
        if state.phase not in {UpdatePhase.APPLY_PENDING, UpdatePhase.HEALTH_CHECK}:
            if state.phase == UpdatePhase.FAILED:
                blocking = state.error_code == "VERSION_MISMATCH" or state.error_code.startswith("HEALTH_")
                if blocking:
                    raise UpdateCoordinatorError("A atualização anterior requer revisão do administrador.")
            return False
        if RELEASE.sequence < state.current_sequence:
            raise UpdateCoordinatorError("A versão instalada não pode retroceder.")
        if RELEASE.sequence < state.target_sequence:
            if not state.bundle_directory or state.attempts >= 3:
                self.state_store.save(replace(state, phase=UpdatePhase.FAILED, error_code="APPLY_RETRY"))
                raise UpdateCoordinatorError("A atualização pendente não pôde ser aplicada.")
            retry = replace(state, attempts=state.attempts + 1)
            self.state_store.save(retry)
            try:
                self.adapter.apply_local_bundle(state.bundle_directory)
            except Exception as exc:
                raise UpdateCoordinatorError("A atualização pendente não pôde ser retomada.") from exc
            raise UpdateRestartScheduled("A atualização será aplicada após o encerramento do TrigoPDV.")
        if RELEASE.sequence != state.target_sequence or RELEASE.version != state.target_version:
            self.state_store.save(replace(state, phase=UpdatePhase.FAILED, error_code="VERSION_MISMATCH"))
            raise UpdateCoordinatorError("A versão reiniciada não corresponde ao pacote autorizado.")
        checking = replace(state, phase=UpdatePhase.HEALTH_CHECK)
        self.state_store.save(checking)
        result = run_health_check(self.database_path, expected_schema=state.target_schema)
        self._log("health_finished", outcome="OK" if result.healthy else "FAILED", code=result.code, sequence=state.target_sequence)
        if result.healthy:
            self.state_store.save(UpdateState(
                phase=UpdatePhase.IDLE, current_version=state.target_version,
                current_sequence=state.target_sequence,
            ))
            return True
        failed = replace(checking, phase=UpdatePhase.FAILED, error_code=f"HEALTH_{result.code}")
        self.state_store.save(failed)
        if self.rollback_callback is not None:
            try:
                self.rollback_callback(failed)
                self.state_store.save(replace(failed, phase=UpdatePhase.ROLLED_BACK))
            except Exception as exc:
                raise UpdateCoordinatorError("A atualização falhou e a reversão não foi concluída.") from exc
        raise UpdateCoordinatorError("A atualização não passou na verificação e o PDV foi bloqueado.")
