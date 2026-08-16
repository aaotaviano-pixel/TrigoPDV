from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Callable
from uuid import uuid4

from config.version import RELEASE

from .models import TrustedArtifact, UpdateOffer, UpdatePolicy


class UpdateRepositoryError(RuntimeError):
    pass


class TufRepository:
    """Cliente TUF; nenhum byte chega ao Velopack sem metadata assinada."""

    def __init__(
        self,
        *,
        base_url: str,
        bootstrap_root: bytes,
        cache_directory: str | Path,
        updater_factory: Callable | None = None,
    ):
        policy = UpdatePolicy(enabled=True, channel="stable", base_url=base_url)
        if not bootstrap_root or len(bootstrap_root) > 512_000:
            raise UpdateRepositoryError("A raiz de confiança da atualização não está disponível.")
        self.base_url = policy.base_url
        self.bootstrap_root = bytes(bootstrap_root)
        self.cache_directory = Path(cache_directory)
        self.metadata_directory = self.cache_directory / "metadata"
        self.target_directory = self.cache_directory / "targets"
        self.bundle_directory = self.cache_directory / "bundles"
        self._updater_factory = updater_factory

    def _new_updater(self):
        self.metadata_directory.mkdir(parents=True, exist_ok=True)
        self.target_directory.mkdir(parents=True, exist_ok=True)
        kwargs = {
            "metadata_dir": str(self.metadata_directory),
            "metadata_base_url": self.base_url + "metadata/",
            "target_dir": str(self.target_directory),
            "target_base_url": self.base_url + "targets/",
            "bootstrap": self.bootstrap_root,
        }
        try:
            if self._updater_factory is not None:
                return self._updater_factory(**kwargs)
            from tuf.ngclient.config import UpdaterConfig
            from .tuf_client import WindowsSafeUpdater

            kwargs["config"] = UpdaterConfig(
                app_user_agent=f"TrigoPDV-Updater/{RELEASE.version}"
            )
            return WindowsSafeUpdater(**kwargs)
        except Exception as exc:
            raise UpdateRepositoryError("Não foi possível iniciar a validação criptográfica da atualização.") from exc

    @staticmethod
    def _target_info(updater, target: str):
        try:
            info = updater.get_targetinfo(target)
        except Exception as exc:
            raise UpdateRepositoryError("Não foi possível validar os metadados da atualização.") from exc
        if info is None:
            raise UpdateRepositoryError("O arquivo assinado da atualização não foi encontrado.")
        return info

    @staticmethod
    def _verify_info(info, artifact: TrustedArtifact) -> None:
        sha256 = str(getattr(info, "hashes", {}).get("sha256", "")).lower()
        if int(getattr(info, "length", -1)) != artifact.length or sha256 != artifact.sha256.lower():
            raise UpdateRepositoryError("Os metadados assinados do pacote são incoerentes.")

    @staticmethod
    def _download(updater, info, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            updater.download_target(info, filepath=str(destination))
        except Exception as exc:
            raise UpdateRepositoryError("Não foi possível baixar e autenticar a atualização.") from exc

    def check_offer(self, manifest_target: str) -> UpdateOffer:
        marker = TrustedArtifact(manifest_target, 1, "0" * 64)
        marker.validate()
        updater = self._new_updater()
        info = self._target_info(updater, manifest_target)
        length = int(getattr(info, "length", -1))
        hashes = getattr(info, "hashes", {})
        digest = str(hashes.get("sha256", "")).lower()
        if not 1 <= length <= 512_000 or len(digest) != 64:
            raise UpdateRepositoryError("O manifesto assinado da atualização é inválido.")
        destination = self.target_directory / f"manifest-{uuid4().hex}.json"
        try:
            self._download(updater, info, destination)
            if destination.stat().st_size != length or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise UpdateRepositoryError("O manifesto baixado falhou na verificação.")
            values = json.loads(destination.read_text(encoding="utf-8"))
            artifacts = tuple(
                TrustedArtifact(
                    target=str(item["target"]), length=int(item["length"]),
                    sha256=str(item["sha256"]).lower(),
                )
                for item in values["artifacts"]
            )
            offer = UpdateOffer(
                version=str(values["version"]), sequence=int(values["sequence"]),
                schema_target=int(values["schema_target"]), pack_id=str(values["pack_id"]),
                channel=str(values["channel"]), rollout_percent=int(values["rollout_percent"]),
                rollout_seed=str(values["rollout_seed"]), manifest_target=manifest_target,
                mandatory=bool(values.get("mandatory", False)), artifacts=artifacts,
            )
            for artifact in artifacts:
                artifact.validate()
            if not artifacts:
                raise ValueError("empty artifacts")
            return offer
        except UpdateRepositoryError:
            raise
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise UpdateRepositoryError("O manifesto assinado da atualização é inválido.") from exc
        finally:
            destination.unlink(missing_ok=True)

    def download_bundle(self, offer: UpdateOffer) -> Path:
        destination = self.bundle_directory / f"release-{offer.sequence}"
        if destination.is_dir():
            if self._bundle_matches(destination, offer.artifacts):
                return destination
            raise UpdateRepositoryError("O cache local da atualização está incoerente.")
        updater = self._new_updater()
        staging = self.bundle_directory / f".release-{offer.sequence}-{uuid4().hex}.staging"
        names: set[str] = set()
        try:
            staging.mkdir(parents=True, exist_ok=False)
            for artifact in offer.artifacts:
                artifact.validate()
                name = PurePosixPath(artifact.target).name
                if not name or name in names:
                    raise UpdateRepositoryError("O pacote assinado contém nomes duplicados.")
                names.add(name)
                info = self._target_info(updater, artifact.target)
                self._verify_info(info, artifact)
                local = staging / name
                self._download(updater, info, local)
                if local.stat().st_size != artifact.length or hashlib.sha256(local.read_bytes()).hexdigest() != artifact.sha256:
                    raise UpdateRepositoryError("Um arquivo do pacote falhou na verificação final.")
            self.bundle_directory.mkdir(parents=True, exist_ok=True)
            os.replace(staging, destination)
            return destination
        except UpdateRepositoryError:
            raise
        except OSError as exc:
            raise UpdateRepositoryError("Não foi possível publicar o pacote autenticado no cache local.") from exc
        finally:
            if staging.exists():
                for child in staging.iterdir():
                    child.unlink(missing_ok=True)
                staging.rmdir()

    @staticmethod
    def _bundle_matches(directory: Path, artifacts: tuple[TrustedArtifact, ...]) -> bool:
        expected = {PurePosixPath(item.target).name: item for item in artifacts}
        actual = {path.name: path for path in directory.iterdir() if path.is_file()}
        if set(expected) != set(actual):
            return False
        return all(
            actual[name].stat().st_size == item.length
            and hashlib.sha256(actual[name].read_bytes()).hexdigest() == item.sha256
            for name, item in expected.items()
        )

