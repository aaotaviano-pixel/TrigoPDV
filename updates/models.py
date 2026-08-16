from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from config.version import RELEASE


class UpdatePhase(str, Enum):
    IDLE = "IDLE"
    AVAILABLE = "AVAILABLE"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"
    PREPARING = "PREPARING"
    APPLY_PENDING = "APPLY_PENDING"
    HEALTH_CHECK = "HEALTH_CHECK"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"
    PAUSED = "PAUSED"


def _https_base_url(value: str) -> str:
    candidate = str(value or "").strip()
    parsed = urlsplit(candidate)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("O endereço de atualização deve usar HTTPS sem credenciais ou parâmetros.")
    return candidate.rstrip("/") + "/"


@dataclass(frozen=True)
class UpdatePolicy:
    enabled: bool = False
    channel: str = "stable"
    base_url: str = ""
    check_interval_hours: int = 6

    def __post_init__(self) -> None:
        if self.channel not in {"internal", "pilot", "stable"}:
            raise ValueError("Canal de atualização inválido.")
        if not 1 <= int(self.check_interval_hours) <= 168:
            raise ValueError("Intervalo de atualização inválido.")
        if self.enabled:
            object.__setattr__(self, "base_url", _https_base_url(self.base_url))


def cohort_eligible(installation_id: str, signed_seed: str, rollout_percent: int) -> bool:
    percent = int(rollout_percent)
    if not 0 <= percent <= 100:
        raise ValueError("Percentual de liberação inválido.")
    if percent == 0:
        return False
    if percent == 100:
        return True
    material = f"{installation_id}:{signed_seed}".encode("utf-8")
    bucket = int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % 10_000
    return bucket < percent * 100


@dataclass(frozen=True)
class TrustedArtifact:
    target: str
    length: int
    sha256: str

    def validate(self) -> None:
        path = PurePosixPath(self.target)
        if path.is_absolute() or ".." in path.parts or "\\" in self.target or ":" in self.target:
            raise ValueError("Nome de artefato inválido.")
        if self.length <= 0 or len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256.lower()):
            raise ValueError("Metadados de artefato inválidos.")


@dataclass(frozen=True)
class UpdateOffer:
    version: str
    sequence: int
    schema_target: int
    pack_id: str
    channel: str
    rollout_percent: int
    rollout_seed: str
    manifest_target: str
    mandatory: bool = False
    artifacts: tuple[TrustedArtifact, ...] = ()

    def validate(self, policy: UpdatePolicy, *, current_sequence: int) -> None:
        if self.pack_id != RELEASE.pack_id or self.schema_target != RELEASE.schema_target:
            raise ValueError("A atualização não pertence a esta instalação.")
        if self.channel != policy.channel or self.sequence <= int(current_sequence):
            raise ValueError("A sequência ou o canal da atualização são incompatíveis.")
        if not self.rollout_seed or not 0 <= int(self.rollout_percent) <= 100:
            raise ValueError("A política assinada de liberação é inválida.")
        TrustedArtifact(self.manifest_target, 1, "0" * 64).validate()
        for artifact in self.artifacts:
            artifact.validate()

