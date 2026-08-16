from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable

from config.version import RELEASE
from db.database import Database
from .health import run_health_check
from .models import TrustedArtifact, UpdateOffer, UpdatePhase, UpdatePolicy, cohort_eligible
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

    @staticmethod
    def _serialize_offer(offer: UpdateOffer) -> str:
        values = {
            "version": offer.version,
            "sequence": offer.sequence,
            "schema_target": offer.schema_target,
            "pack_id": offer.pack_id,
            "channel": offer.channel,
            "rollout_percent": offer.rollout_percent,
            "rollout_seed": offer.rollout_seed,
            "manifest_target": offer.manifest_target,
            "mandatory": offer.mandatory,
            "artifacts": [
                {"target": item.target, "length": item.length, "sha256": item.sha256}
                for item in offer.artifacts
            ],
        }
        payload = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(payload) > 65536:
            raise UpdateCoordinatorError("A oferta autenticada excede o limite permitido.")
        return payload

    @staticmethod
    def _deserialize_offer(payload: str) -> UpdateOffer:
        try:
            if not payload or len(payload) > 65536:
                raise ValueError("missing payload")
            values = json.loads(payload)
            allowed = {
                "version", "sequence", "schema_target", "pack_id", "channel",
                "rollout_percent", "rollout_seed", "manifest_target", "mandatory", "artifacts",
            }
            if not isinstance(values, dict) or set(values) != allowed:
                raise ValueError("invalid fields")
            artifacts = tuple(
                TrustedArtifact(
                    target=str(item["target"]),
                    length=int(item["length"]),
                    sha256=str(item["sha256"]).lower(),
                )
                for item in values["artifacts"]
            )
            offer = UpdateOffer(
                version=str(values["version"]),
                sequence=int(values["sequence"]),
                schema_target=int(values["schema_target"]),
                pack_id=str(values["pack_id"]),
                channel=str(values["channel"]),
                rollout_percent=int(values["rollout_percent"]),
                rollout_seed=str(values["rollout_seed"]),
                manifest_target=str(values["manifest_target"]),
                mandatory=bool(values["mandatory"]),
                artifacts=artifacts,
            )
            if not artifacts:
                raise ValueError("no artifacts")
            for artifact in artifacts:
                artifact.validate()
            return offer
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise UpdateCoordinatorError("A oferta baixada não pôde ser reconstruída com segurança.") from exc

    @staticmethod
    def _bundle_matches_offer(directory: Path, offer: UpdateOffer) -> bool:
        try:
            expected = {PurePosixPath(item.target).name: item for item in offer.artifacts}
            actual = {path.name: path for path in directory.iterdir() if path.is_file()}
            if len(expected) != len(offer.artifacts) or set(actual) != set(expected):
                return False
            for name, artifact in expected.items():
                path = actual[name]
                if path.stat().st_size != artifact.length:
                    return False
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != artifact.sha256:
                    return False
            return True
        except OSError:
            return False

    def restore_downloaded_offer(self) -> UpdateOffer:
        state = self.state_store.load()
        if state.phase != UpdatePhase.DOWNLOADED:
            raise UpdateCoordinatorError("Não existe atualização baixada pronta para instalar.")
        offer = self._deserialize_offer(state.offer_json)
        offer.validate(self.policy, current_sequence=state.current_sequence)
        bundle = Path(state.bundle_directory)
        if (
            offer.sequence != state.target_sequence
            or offer.version != state.target_version
            or offer.schema_target != state.target_schema
            or not bundle.is_dir()
            or not self._bundle_matches_offer(bundle, offer)
        ):
            self.state_store.save(replace(state, phase=UpdatePhase.FAILED, error_code="BUNDLE_INVALID"))
            raise UpdateCoordinatorError("O pacote baixado não corresponde à oferta autenticada.")
        return offer

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
            if offer.sequence == current.current_sequence:
                # The repository normally keeps publishing the newest release.
                # Seeing our exact signed identity means this installation is
                # current, not that the network check failed. A reused sequence
                # with another version is still rejected as equivocation.
                offer.validate(self.policy, current_sequence=current.current_sequence - 1)
                if offer.version != current.current_version:
                    raise UpdateCoordinatorError(
                        "A sequência publicada foi reutilizada por outra versão."
                    )
                self._log(
                    "check_finished",
                    outcome="CURRENT",
                    version=offer.version,
                    sequence=offer.sequence,
                )
                return None
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
                bundle_directory=str(bundle), offer_json=self._serialize_offer(offer), error_code="",
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
        if not self._bundle_matches_offer(Path(bundle_directory), offer):
            raise UpdateCoordinatorError("O pacote baixado não corresponde à oferta autenticada.")
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
