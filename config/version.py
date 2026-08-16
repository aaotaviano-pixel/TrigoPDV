"""Identidade imutável da versão distribuída, lida de uma única fonte."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import tomllib


class ReleaseMetadataError(RuntimeError):
    """Metadados ausentes ou incoerentes bloqueiam a distribuição."""


@dataclass(frozen=True)
class ReleaseMetadata:
    version: str
    sequence: int
    schema_target: int
    pack_id: str
    title: str
    channel: str


def _resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def load_release_metadata(path: Path | None = None) -> ReleaseMetadata:
    source = path or (_resource_root() / "release" / "version.toml")
    try:
        document = tomllib.loads(source.read_text(encoding="utf-8"))
        values = document["release"]
        metadata = ReleaseMetadata(
            version=str(values["version"]),
            sequence=int(values["sequence"]),
            schema_target=int(values["schema_target"]),
            pack_id=str(values["pack_id"]),
            title=str(values["title"]),
            channel=str(values["channel"]),
        )
    except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseMetadataError("Os metadados da versão são inválidos.") from exc
    parts = metadata.version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ReleaseMetadataError("A versão precisa usar o formato maior.menor.correção.")
    if metadata.sequence < 1 or metadata.schema_target < 1:
        raise ReleaseMetadataError("Sequência e schema da versão precisam ser positivos.")
    if not metadata.pack_id or not metadata.title or metadata.channel not in {"stable", "beta"}:
        raise ReleaseMetadataError("A identidade ou o canal da versão são inválidos.")
    return metadata


RELEASE = load_release_metadata()
__version__ = RELEASE.version

