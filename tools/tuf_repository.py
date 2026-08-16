"""Geração e verificação offline da árvore TUF publicada pelo TrigoPDV.

Este módulo recebe signers já carregados. Ele nunca descobre, persiste ou imprime
chaves privadas: a fronteira de secrets pertence à cerimônia/CI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from typing import Mapping, Sequence
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from securesystemslib.signer import Signer
from tuf.api.exceptions import DownloadHTTPError
from tuf.api.metadata import (
    MetaFile,
    Metadata,
    Root,
    Snapshot,
    TargetFile,
    Targets,
    Timestamp,
)
from tuf.ngclient.fetcher import FetcherInterface

from updates.tuf_client import WindowsSafeUpdater


ROLE_NAMES = ("targets", "snapshot", "timestamp")
MAX_METADATA_BYTES = 512_000
MAX_MANIFEST_BYTES = 512_000


class TufPublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublishedRepository:
    root: Path
    write_order: tuple[str, ...]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TufPublishError("Datas TUF precisam conter fuso UTC.")
    return value.astimezone(timezone.utc)


def _validate_expirations(
    reference: datetime,
    *,
    targets: datetime,
    snapshot: datetime,
    timestamp: datetime,
) -> None:
    reference = _utc(reference)
    values = {
        "targets": _utc(targets),
        "snapshot": _utc(snapshot),
        "timestamp": _utc(timestamp),
    }
    if any(value < reference + timedelta(hours=24) for value in values.values()):
        raise TufPublishError("A expiração TUF deve ter pelo menos 24 horas de margem.")
    if not values["timestamp"] <= values["snapshot"] <= values["targets"]:
        raise TufPublishError("A ordem de expiração dos metadados TUF é inválida.")


def create_root_metadata(
    root_signers: Sequence[Signer],
    role_signers: Mapping[str, Signer],
    *,
    version: int,
    expires: datetime,
) -> bytes:
    """Cria root pública 2-de-3 com uma chave distinta por role online."""

    if len(root_signers) != 3 or set(role_signers) != set(ROLE_NAMES):
        raise TufPublishError("A raiz exige três chaves offline e três roles online distintas.")
    if int(version) <= 0 or _utc(expires) <= datetime.now(timezone.utc) + timedelta(days=30):
        raise TufPublishError("A versão ou expiração da raiz TUF é inválida.")
    root_ids = [signer.public_key.keyid for signer in root_signers]
    role_ids = [role_signers[role].public_key.keyid for role in ROLE_NAMES]
    if len(set(root_ids + role_ids)) != 6:
        raise TufPublishError("Cada função TUF precisa de uma chave distinta.")

    root = Root(version=int(version), expires=_utc(expires), consistent_snapshot=False)
    for signer in root_signers:
        root.add_key(signer.public_key, "root")
    root.roles["root"].threshold = 2
    for role in ROLE_NAMES:
        root.add_key(role_signers[role].public_key, role)

    metadata = Metadata(root)
    metadata.sign(root_signers[0])
    metadata.sign(root_signers[1], append=True)
    metadata.verify_delegate("root", metadata)
    return metadata.to_bytes()


def _safe_target_files(directory: Path) -> list[tuple[str, Path]]:
    directory = directory.resolve()
    if not directory.is_dir():
        raise TufPublishError("A pasta de targets da publicação não existe.")
    records: list[tuple[str, Path]] = []
    for path in sorted(directory.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise TufPublishError("A publicação TUF não aceita links simbólicos.")
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix()
        parsed = PurePosixPath(relative)
        if parsed.is_absolute() or ".." in parsed.parts or "\\" in relative or ":" in relative:
            raise TufPublishError("Um target da publicação possui caminho inseguro.")
        records.append((relative, path))
    if not records:
        raise TufPublishError("A publicação TUF não contém targets.")
    return records


def _validated_root(data: bytes) -> Metadata[Root]:
    if not data or len(data) > MAX_METADATA_BYTES:
        raise TufPublishError("A raiz pública TUF é inválida.")
    try:
        metadata = Metadata.from_bytes(data)
        if not isinstance(metadata.signed, Root) or metadata.signed.is_expired():
            raise ValueError("invalid root")
        metadata.verify_delegate("root", metadata)
        if metadata.signed.consistent_snapshot:
            raise ValueError("consistent snapshot not materialized")
        return metadata
    except Exception as exc:
        raise TufPublishError("A raiz pública TUF não pôde ser validada.") from exc


def _authorized_online_signers(root: Metadata[Root], signers: Mapping[str, Signer]) -> None:
    if set(signers) != set(ROLE_NAMES):
        raise TufPublishError("As três roles online TUF são obrigatórias.")
    for role in ROLE_NAMES:
        if signers[role].public_key.keyid not in root.signed.roles[role].keyids:
            raise TufPublishError("Uma chave online não pertence à raiz pública TUF.")


def _metadata_version(repository: Path) -> int:
    try:
        metadata = Metadata.from_file(repository / "metadata" / "timestamp.json")
        return int(metadata.signed.version)
    except Exception as exc:
        raise TufPublishError("O repositório anterior não pôde ser validado.") from exc


def _write(path: Path, data: bytes, order: list[str], root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        order.append(path.relative_to(root).as_posix())
    finally:
        temporary.unlink(missing_ok=True)


def publish_repository(
    targets_directory: str | Path,
    output_directory: str | Path,
    *,
    bootstrap_root: bytes,
    role_signers: Mapping[str, Signer],
    metadata_version: int,
    targets_expires: datetime,
    snapshot_expires: datetime,
    timestamp_expires: datetime,
    reference_time: datetime | None = None,
    previous_repository: str | Path | None = None,
) -> PublishedRepository:
    """Materializa metadata/targets em staging; timestamp é escrito por último."""

    now = _utc(reference_time or datetime.now(timezone.utc))
    version = int(metadata_version)
    if version <= 0:
        raise TufPublishError("A sequência dos metadados TUF deve ser positiva.")
    if previous_repository is not None and version <= _metadata_version(Path(previous_repository)):
        raise TufPublishError("A sequência TUF precisa aumentar a cada publicação.")
    _validate_expirations(
        now,
        targets=targets_expires,
        snapshot=snapshot_expires,
        timestamp=timestamp_expires,
    )
    root = _validated_root(bytes(bootstrap_root))
    _authorized_online_signers(root, role_signers)
    files = _safe_target_files(Path(targets_directory))

    output = Path(output_directory).resolve()
    if output.exists():
        raise TufPublishError("A pasta de saída da publicação já existe.")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".{output.name}.{uuid4().hex}.staging"
    order: list[str] = []
    try:
        target_metadata = Metadata(Targets(version=version, expires=_utc(targets_expires)))
        for relative, source in files:
            destination = staging / "targets" / Path(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            target_metadata.signed.targets[relative] = TargetFile.from_file(
                relative, str(destination), ["sha256"]
            )
            order.append(destination.relative_to(staging).as_posix())
        target_metadata.sign(role_signers["targets"])
        targets_bytes = target_metadata.to_bytes()

        snapshot_metadata = Metadata(Snapshot(
            version=version,
            expires=_utc(snapshot_expires),
            meta={"targets.json": MetaFile.from_data(version, targets_bytes, ["sha256"])},
        ))
        snapshot_metadata.sign(role_signers["snapshot"])
        snapshot_bytes = snapshot_metadata.to_bytes()

        timestamp_metadata = Metadata(Timestamp(
            version=version,
            expires=_utc(timestamp_expires),
            snapshot_meta=MetaFile.from_data(version, snapshot_bytes, ["sha256"]),
        ))
        timestamp_metadata.sign(role_signers["timestamp"])
        timestamp_bytes = timestamp_metadata.to_bytes()

        _write(staging / "metadata" / "root.json", bootstrap_root, order, staging)
        _write(
            staging / "metadata" / f"{root.signed.version}.root.json",
            bootstrap_root,
            order,
            staging,
        )
        _write(staging / "metadata" / "targets.json", targets_bytes, order, staging)
        _write(staging / "metadata" / "snapshot.json", snapshot_bytes, order, staging)
        _write(staging / "metadata" / "timestamp.json", timestamp_bytes, order, staging)
        os.replace(staging, output)
        return PublishedRepository(output, tuple(order))
    except TufPublishError:
        raise
    except Exception as exc:
        raise TufPublishError("Não foi possível gerar o repositório TUF.") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


class _LocalFetcher(FetcherInterface):
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _fetch(self, url: str):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "local.invalid":
            raise DownloadHTTPError("Repositório local inválido", 400)
        relative = PurePosixPath(unquote(parsed.path).lstrip("/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise DownloadHTTPError("Caminho inválido", 400)
        path = self.root.joinpath(*relative.parts).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise DownloadHTTPError("Caminho inválido", 400) from exc
        if not path.is_file():
            raise DownloadHTTPError("Arquivo não encontrado", 404)
        # O cliente pode abortar no primeiro hash divergente sem fechar um
        # generator pendente. Ler e fechar aqui evita deixar o artefato travado
        # no Windows durante a limpeza do staging de release.
        data = path.read_bytes()
        return iter(data[index : index + 64 * 1024] for index in range(0, len(data), 64 * 1024))


def verify_repository(
    repository_root: str | Path,
    *,
    bootstrap_root: bytes,
    channel: str,
    reference_time: datetime | None = None,
) -> dict:
    """Usa o cliente TUF real e baixa manifesto+artefatos para temporários."""

    root = Path(repository_root).resolve()
    if channel not in {"internal", "pilot", "stable"}:
        raise TufPublishError("O canal do repositório é inválido.")
    _validated_root(bytes(bootstrap_root))
    if reference_time is not None:
        now = _utc(reference_time)
        try:
            for name in ("targets", "snapshot", "timestamp"):
                metadata = Metadata.from_file(root / "metadata" / f"{name}.json")
                if metadata.signed.expires <= now:
                    raise TufPublishError("Os metadados do repositório estão expirados.")
        except TufPublishError:
            raise
        except Exception as exc:
            raise TufPublishError("Os metadados do repositório são inválidos.") from exc
    try:
        with tempfile.TemporaryDirectory(dir=root.parent) as directory:
            temporary = Path(directory)
            updater = WindowsSafeUpdater(
                metadata_dir=str(temporary / "metadata"),
                metadata_base_url="https://local.invalid/metadata/",
                target_dir=str(temporary / "targets"),
                target_base_url="https://local.invalid/targets/",
                fetcher=_LocalFetcher(root),
                bootstrap=bytes(bootstrap_root),
            )
            manifest_name = f"channels/{channel}/manifest.json"
            info = updater.get_targetinfo(manifest_name)
            if info is None or info.length > MAX_MANIFEST_BYTES:
                raise ValueError("missing manifest")
            manifest_path = Path(updater.download_target(info))
            values = json.loads(manifest_path.read_text(encoding="utf-8"))
            if values.get("channel") != channel or not isinstance(values.get("artifacts"), list):
                raise ValueError("invalid manifest")
            for artifact in values["artifacts"]:
                name = str(artifact["target"])
                target_info = updater.get_targetinfo(name)
                if target_info is None:
                    raise ValueError("missing artifact")
                if int(target_info.length) != int(artifact["length"]):
                    raise ValueError("length mismatch")
                if target_info.hashes.get("sha256") != str(artifact["sha256"]).lower():
                    raise ValueError("hash mismatch")
                updater.download_target(target_info)
            return values
    except TufPublishError:
        raise
    except Exception as exc:
        raise TufPublishError("O repositório TUF não passou na verificação.") from exc
