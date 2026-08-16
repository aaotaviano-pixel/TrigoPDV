"""Monta pacote Velopack e árvore TUF estática para GitHub Pages."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit
from uuid import uuid4

import requests
from securesystemslib.signer import Signer


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import PILOT_UPDATE_URL
from config.version import RELEASE
from tools.create_update_manifest import create_manifest
from tools.tuf_ceremony import SECRET_NAMES, signer_from_private_pem
from tools.tuf_repository import publish_repository, verify_repository


class OnlineReleaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class OnlineRepositoryResult:
    repository_root: Path
    write_order: tuple[str, ...]


CHANNELS = ("internal", "pilot", "stable")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def create_continuity_checkpoint(repository_root: str | Path) -> dict:
    """Create the independent ledger record for one authenticated publication."""

    from tuf.api.metadata import Metadata, Timestamp

    root = Path(repository_root).resolve()
    try:
        timestamp = Metadata.from_file(root / "metadata" / "timestamp.json")
        if not isinstance(timestamp.signed, Timestamp):
            raise ValueError("not timestamp")
        channels: dict[str, dict[str, object]] = {}
        channel_root = root / "targets" / "channels"
        if channel_root.is_dir():
            for manifest in sorted(channel_root.glob("*/manifest.json")):
                channel = manifest.parent.name
                if channel not in CHANNELS:
                    raise ValueError("unknown channel")
                values = json.loads(manifest.read_text(encoding="utf-8"))
                if values.get("channel") != channel or int(values["sequence"]) <= 0:
                    raise ValueError("invalid manifest")
                channels[channel] = {
                    "sequence": int(values["sequence"]),
                    "manifest_sha256": _sha256(manifest),
                }
        if not channels:
            raise ValueError("missing channels")
        return {
            "format": 1,
            "metadata_version": int(timestamp.signed.version),
            "root_sha256": _sha256(root / "metadata" / "root.json"),
            "timestamp_sha256": _sha256(root / "metadata" / "timestamp.json"),
            "snapshot_sha256": _sha256(root / "metadata" / "snapshot.json"),
            "targets_sha256": _sha256(root / "metadata" / "targets.json"),
            "channels": channels,
        }
    except Exception as exc:
        raise OnlineReleaseError("O checkpoint de continuidade não pôde ser criado.") from exc


def _repository_checkpoint(checkpoint: Mapping[str, object]) -> dict:
    keys = (
        "format", "metadata_version", "root_sha256", "timestamp_sha256",
        "snapshot_sha256", "targets_sha256", "channels",
    )
    try:
        return {key: checkpoint[key] for key in keys}
    except (KeyError, TypeError) as exc:
        raise OnlineReleaseError("O checkpoint de continuidade é inválido.") from exc


def checkpoint_digest(checkpoint: Mapping[str, object]) -> str:
    try:
        canonical = json.dumps(
            dict(checkpoint), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()
    except (TypeError, ValueError) as exc:
        raise OnlineReleaseError("O checkpoint de continuidade é inválido.") from exc


def load_continuity_checkpoint(path: str | Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, dict) or int(value.get("format", 0)) != 1:
            raise ValueError("invalid checkpoint")
        return value
    except Exception as exc:
        raise OnlineReleaseError("O checkpoint de continuidade é inválido.") from exc


def write_continuity_checkpoint(
    repository_root: str | Path,
    output: str | Path,
    *,
    previous_checkpoint: Mapping[str, object] | None = None,
) -> Path:
    destination = Path(output).resolve()
    if destination.exists():
        raise OnlineReleaseError("O checkpoint de continuidade de saída já existe.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        record = create_continuity_checkpoint(repository_root)
        record["predecessor_checkpoint_sha256"] = (
            checkpoint_digest(previous_checkpoint) if previous_checkpoint is not None else None
        )
        temporary.write_text(
            json.dumps(record, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def _remote_bytes(
    base_url: str,
    *,
    relative: str,
    maximum: int,
    request_get: Callable | None = None,
) -> bytes | None:
    """Download one repository object without redirects or unbounded reads."""

    getter = request_get or requests.get
    url = str(base_url).rstrip("/") + "/" + relative
    try:
        response = getter(
            url,
            timeout=(3.05, 10.0),
            allow_redirects=False,
            stream=True,
        )
    except requests.RequestException as exc:
        raise OnlineReleaseError(
            "Não foi possível consultar a sequência TUF já publicada."
        ) from exc
    try:
        if int(response.status_code) == 404:
            return None
        if int(response.status_code) != 200:
            raise OnlineReleaseError("O repositório remoto recusou a leitura autenticada.")
        declared = response.headers.get("Content-Length")
        if declared is not None and (int(declared) < 0 or int(declared) > maximum):
            raise OnlineReleaseError("Um arquivo remoto excede o limite permitido.")
        payload = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if chunk:
                payload.extend(chunk)
            if len(payload) > maximum:
                raise OnlineReleaseError("Um arquivo remoto excede o limite permitido.")
        return bytes(payload)
    finally:
        response.close()


def _matches_metadata(data: bytes, expected) -> bool:
    hashes = getattr(expected, "hashes", {})
    return (
        int(getattr(expected, "length", -1)) == len(data)
        and str(hashes.get("sha256", "")).lower() == hashlib.sha256(data).hexdigest()
    )


def download_authenticated_repository(
    base_url: str,
    destination: str | Path,
    *,
    bootstrap_root: bytes,
    continuity_checkpoint: Mapping[str, object] | None = None,
    allow_empty_initialization: bool = False,
    request_get: Callable | None = None,
) -> Path | None:
    """Mirror the current Pages tree only after validating its full TUF chain.

    Expired online metadata remains usable only as an authenticated predecessor
    for a freshness refresh: it is never handed to the PDV client as current.
    """

    parsed = urlsplit(str(base_url))
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise OnlineReleaseError("O endereço do repositório anterior é inválido.")
    output = Path(destination).resolve()
    if output.exists():
        raise OnlineReleaseError("A pasta do repositório anterior precisa estar vazia.")
    from tuf.api.metadata import Metadata, Root, Snapshot, Targets, Timestamp

    try:
        root = Metadata.from_bytes(bytes(bootstrap_root))
        if not isinstance(root.signed, Root) or root.signed.is_expired():
            raise ValueError("invalid root")
        root.verify_delegate("root", root)
        timestamp_bytes = _remote_bytes(
            base_url, relative="metadata/timestamp.json", maximum=512_000,
            request_get=request_get,
        )
        if timestamp_bytes is None:
            if allow_empty_initialization and continuity_checkpoint is None:
                return None
            raise OnlineReleaseError(
                "A continuidade TUF está ausente; use a cerimônia explícita apenas na primeira inicialização."
            )
        if continuity_checkpoint is None:
            raise OnlineReleaseError("O checkpoint independente de continuidade é obrigatório.")
        timestamp = Metadata.from_bytes(timestamp_bytes)
        if not isinstance(timestamp.signed, Timestamp):
            raise ValueError("not timestamp")
        root.verify_delegate("timestamp", timestamp)
        snapshot_bytes = _remote_bytes(
            base_url, relative="metadata/snapshot.json", maximum=512_000,
            request_get=request_get,
        )
        if snapshot_bytes is None or not _matches_metadata(
            snapshot_bytes, timestamp.signed.snapshot_meta
        ):
            raise ValueError("snapshot mismatch")
        snapshot = Metadata.from_bytes(snapshot_bytes)
        if not isinstance(snapshot.signed, Snapshot):
            raise ValueError("not snapshot")
        root.verify_delegate("snapshot", snapshot)
        targets_meta = snapshot.signed.meta.get("targets.json")
        targets_bytes = _remote_bytes(
            base_url, relative="metadata/targets.json", maximum=512_000,
            request_get=request_get,
        )
        if targets_meta is None or targets_bytes is None or not _matches_metadata(
            targets_bytes, targets_meta
        ):
            raise ValueError("targets mismatch")
        targets = Metadata.from_bytes(targets_bytes)
        if not isinstance(targets.signed, Targets):
            raise ValueError("not targets")
        root.verify_delegate("targets", targets)

        staging = output.parent / f".{output.name}.{uuid4().hex}.download"
        staging.mkdir(parents=True, exist_ok=False)
        try:
            metadata_dir = staging / "metadata"
            metadata_dir.mkdir()
            (metadata_dir / "root.json").write_bytes(bytes(bootstrap_root))
            (metadata_dir / f"{root.signed.version}.root.json").write_bytes(bytes(bootstrap_root))
            (metadata_dir / "timestamp.json").write_bytes(timestamp_bytes)
            (metadata_dir / "snapshot.json").write_bytes(snapshot_bytes)
            (metadata_dir / "targets.json").write_bytes(targets_bytes)
            for relative, target in targets.signed.targets.items():
                path = PurePosixPath(relative)
                if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
                    raise ValueError("unsafe target")
                maximum = int(target.length)
                if maximum < 0 or maximum > 1_000_000_000:
                    raise ValueError("target too large")
                payload = _remote_bytes(
                    base_url, relative="targets/" + relative, maximum=maximum,
                    request_get=request_get,
                )
                if payload is None or not _matches_metadata(payload, target):
                    raise ValueError("target mismatch")
                local = staging / "targets" / Path(*path.parts)
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(payload)
            if create_continuity_checkpoint(staging) != _repository_checkpoint(continuity_checkpoint):
                raise OnlineReleaseError(
                    "O Pages autenticado diverge do checkpoint independente de continuidade."
                )
            os.replace(staging, output)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        return output
    except OnlineReleaseError:
        raise
    except Exception as exc:
        raise OnlineReleaseError(
            "O repositório remoto não pôde ser autenticado integralmente."
        ) from exc


def deployment_continuity_status(
    *,
    base_url: str,
    candidate_checkpoint: Mapping[str, object],
    committed_checkpoint: Mapping[str, object] | None,
    bootstrap_root: bytes,
    allow_empty_initialization: bool = False,
    request_get: Callable | None = None,
) -> str:
    """Return deploy/already-deployed after a last-moment continuity check."""

    expected_predecessor = (
        checkpoint_digest(committed_checkpoint) if committed_checkpoint is not None else None
    )
    if candidate_checkpoint.get("predecessor_checkpoint_sha256") != expected_predecessor:
        raise OnlineReleaseError("O candidato de deploy está obsoleto ou possui predecessor incorreto.")

    import tempfile

    first_error: OnlineReleaseError | None = None
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        try:
            previous = download_authenticated_repository(
                base_url,
                root / "committed",
                bootstrap_root=bootstrap_root,
                continuity_checkpoint=committed_checkpoint,
                allow_empty_initialization=(
                    allow_empty_initialization and committed_checkpoint is None
                ),
                request_get=request_get,
            )
            if previous is None:
                return "deploy"
            return "deploy"
        except OnlineReleaseError as exc:
            first_error = exc
        try:
            download_authenticated_repository(
                base_url,
                root / "candidate",
                bootstrap_root=bootstrap_root,
                continuity_checkpoint=candidate_checkpoint,
                request_get=request_get,
            )
            return "already-deployed"
        except OnlineReleaseError as exc:
            raise OnlineReleaseError(
                "O estado do Pages não corresponde ao ledger nem ao candidato deste deploy."
            ) from (first_error or exc)


def velopack_pack_command(
    *,
    pack_directory: str | Path,
    output_directory: str | Path,
    channel: str,
) -> list[str]:
    if channel not in {"internal", "pilot", "stable"}:
        raise OnlineReleaseError("Canal Velopack inválido.")
    return [
        "vpk",
        "pack",
        "--yes",
        "--skip-updates",
        "--outputDir",
        str(Path(output_directory).resolve()),
        "--channel",
        channel,
        "--packId",
        RELEASE.pack_id,
        "--packVersion",
        RELEASE.version,
        "--packDir",
        str(Path(pack_directory).resolve()),
        "--mainExe",
        "TrigoPDV.exe",
        "--packTitle",
        RELEASE.title,
        "--packAuthors",
        "Trigo de Minas",
        "--delta",
        "None",
    ]


def _validate_artifacts(artifacts: Sequence[Path], channel: str) -> list[Path]:
    resolved = [Path(path).resolve() for path in artifacts]
    package = [path for path in resolved if path.name.endswith("-full.nupkg")]
    feeds = [path for path in resolved if path.name == f"releases.{channel}.json"]
    if len(resolved) != 2 or len(package) != 1 or len(feeds) != 1 or any(not path.is_file() for path in resolved):
        raise OnlineReleaseError("A publicação aceita somente os dois artefatos Velopack autenticados.")
    return [package[0], feeds[0]]


def _default_authenticode_checker(path: Path) -> bool:
    from tools.release_gate import _authenticode_valid

    return _authenticode_valid(path)


def prepare_online_repository(
    *,
    artifacts: Sequence[str | Path],
    site_root: str | Path,
    channel: str,
    rollout_percent: int,
    rollout_seed: str,
    mandatory: bool,
    bootstrap_root: bytes,
    role_signers: Mapping[str, Signer],
    signed_binaries: Sequence[str | Path] = (),
    authenticode_checker: Callable[[Path], bool] | None = None,
    previous_repository: str | Path | None = None,
    reference_time: datetime | None = None,
) -> OnlineRepositoryResult:
    """Gera `site/updates` e a verifica com o cliente real antes de retornar."""

    if channel not in {"internal", "pilot", "stable"}:
        raise OnlineReleaseError("Canal de atualização inválido.")
    clean_artifacts = _validate_artifacts([Path(path) for path in artifacts], channel)
    if channel == "stable":
        checker = authenticode_checker or _default_authenticode_checker
        binaries = [Path(path).resolve() for path in signed_binaries]
        if len(binaries) < 2 or any(not path.is_file() or not checker(path) for path in binaries):
            raise OnlineReleaseError("Stable exige Authenticode válido no executável e no Setup.")

    site = Path(site_root).resolve()
    if site.exists():
        raise OnlineReleaseError("A pasta do site de atualização já existe.")
    now = (reference_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    workspace = site.parent / f".{site.name}.{uuid4().hex}.release"
    try:
        unsigned = workspace / "unsigned"
        previous = Path(previous_repository).resolve() if previous_repository is not None else None
        metadata_version = 1
        if previous is not None:
            previous_targets = previous / "targets"
            if not previous_targets.is_dir():
                raise OnlineReleaseError("O repositório anterior autenticado não contém targets.")
            shutil.copytree(previous_targets, unsigned / "targets")
            from tuf.api.metadata import Metadata, Timestamp

            timestamp = Metadata.from_file(previous / "metadata" / "timestamp.json")
            if not isinstance(timestamp.signed, Timestamp):
                raise OnlineReleaseError("O timestamp TUF anterior é inválido.")
            metadata_version = int(timestamp.signed.version) + 1
            previous_manifest = previous_targets / "channels" / channel / "manifest.json"
            if previous_manifest.is_file():
                try:
                    old = json.loads(previous_manifest.read_text(encoding="utf-8"))
                    old_sequence = int(old["sequence"])
                    old_rollout = int(old["rollout_percent"])
                except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise OnlineReleaseError("O manifesto anterior autenticado é inválido.") from exc
                if old_sequence > RELEASE.sequence:
                    raise OnlineReleaseError("A publicação não pode retroceder o canal selecionado.")
                if old_sequence == RELEASE.sequence:
                    if (
                        str(old.get("version")) != RELEASE.version
                        or str(old.get("pack_id")) != RELEASE.pack_id
                        or int(old.get("schema_target", -1)) != RELEASE.schema_target
                        or str(old.get("channel")) != channel
                    ):
                        raise OnlineReleaseError("A sequência existente pertence a outra release.")
                    if str(old.get("rollout_seed")) != str(rollout_seed):
                        raise OnlineReleaseError("A expansão de rollout deve preservar a coorte assinada.")
                    if int(rollout_percent) < old_rollout or (bool(old.get("mandatory")) and not mandatory):
                        raise OnlineReleaseError("A republicação não pode enfraquecer a política já assinada.")
                    expected_artifacts = [
                        {
                            "target": f"releases/{RELEASE.version}/{artifact.name}",
                            "length": artifact.stat().st_size,
                            "sha256": _sha256(artifact),
                        }
                        for artifact in clean_artifacts
                    ]
                    old_artifacts = old.get("artifacts")
                    if not isinstance(old_artifacts, list) or sorted(
                        old_artifacts, key=lambda item: str(item.get("target", ""))
                    ) != sorted(expected_artifacts, key=lambda item: item["target"]):
                        raise OnlineReleaseError(
                            "A mesma sequência não pode substituir os artefatos já publicados."
                        )
        create_manifest(
            clean_artifacts,
            unsigned,
            channel=channel,
            rollout_percent=int(rollout_percent),
            rollout_seed=str(rollout_seed),
            mandatory=bool(mandatory),
        )
        published = publish_repository(
            unsigned / "targets",
            site / "updates",
            bootstrap_root=bytes(bootstrap_root),
            role_signers=role_signers,
            metadata_version=metadata_version,
            targets_expires=now + timedelta(days=90),
            snapshot_expires=now + timedelta(days=14),
            timestamp_expires=now + timedelta(days=2),
            reference_time=now,
            previous_repository=previous,
        )
        verify_repository(
            published.root,
            bootstrap_root=bytes(bootstrap_root),
            channel=channel,
            reference_time=now,
        )
        return OnlineRepositoryResult(published.root, published.write_order)
    except OnlineReleaseError:
        raise
    except Exception as exc:
        if site.exists():
            shutil.rmtree(site, ignore_errors=True)
        raise OnlineReleaseError("A release online não passou na validação criptográfica.") from exc
    finally:
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)


def refresh_online_repository(
    *,
    previous_repository: str | Path,
    site_root: str | Path,
    bootstrap_root: bytes,
    role_signers: Mapping[str, Signer],
    reference_time: datetime | None = None,
) -> OnlineRepositoryResult:
    """Refresh TUF expiry/version while preserving every signed target byte."""

    previous = Path(previous_repository).resolve()
    site = Path(site_root).resolve()
    if site.exists() or not (previous / "targets").is_dir():
        raise OnlineReleaseError("A renovação exige um repositório anterior autenticado.")
    from tuf.api.metadata import Metadata, Timestamp

    now = (reference_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    workspace = site.parent / f".{site.name}.{uuid4().hex}.refresh"
    try:
        shutil.copytree(previous / "targets", workspace / "targets")
        timestamp = Metadata.from_file(previous / "metadata" / "timestamp.json")
        if not isinstance(timestamp.signed, Timestamp):
            raise OnlineReleaseError("O timestamp TUF anterior é inválido.")
        published = publish_repository(
            workspace / "targets",
            site / "updates",
            bootstrap_root=bytes(bootstrap_root),
            role_signers=role_signers,
            metadata_version=int(timestamp.signed.version) + 1,
            targets_expires=now + timedelta(days=90),
            snapshot_expires=now + timedelta(days=14),
            timestamp_expires=now + timedelta(days=2),
            reference_time=now,
            previous_repository=previous,
        )
        channels = sorted(
            path.parent.name
            for path in (published.root / "targets" / "channels").glob("*/manifest.json")
        )
        if not channels or any(channel not in CHANNELS for channel in channels):
            raise OnlineReleaseError("Os canais autenticados anteriores são inválidos.")
        for channel in channels:
            verify_repository(
                published.root,
                bootstrap_root=bytes(bootstrap_root),
                channel=channel,
                reference_time=now,
            )
        return OnlineRepositoryResult(published.root, published.write_order)
    except OnlineReleaseError:
        raise
    except Exception as exc:
        if site.exists():
            shutil.rmtree(site, ignore_errors=True)
        raise OnlineReleaseError("A renovação de validade TUF falhou.") from exc
    finally:
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)


def prepare_policy_repository(
    *,
    previous_repository: str | Path,
    site_root: str | Path,
    channel: str,
    rollout_percent: int,
    rollout_seed: str,
    mandatory: bool,
    bootstrap_root: bytes,
    role_signers: Mapping[str, Signer],
    reference_time: datetime | None = None,
) -> OnlineRepositoryResult:
    """Change signed rollout policy while reusing authenticated artifact bytes."""

    previous = Path(previous_repository).resolve()
    manifest_path = previous / "targets" / "channels" / channel / "manifest.json"
    try:
        values = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            str(values.get("version")) != RELEASE.version
            or int(values.get("sequence", -1)) != RELEASE.sequence
            or int(values.get("schema_target", -1)) != RELEASE.schema_target
            or str(values.get("pack_id")) != RELEASE.pack_id
            or str(values.get("channel")) != channel
            or str(values.get("rollout_seed")) != str(rollout_seed)
        ):
            raise OnlineReleaseError(
                "O modo somente política exige a identidade exata da release já publicada."
            )
        records = values["artifacts"]
        if not isinstance(records, list) or len(records) != 2:
            raise ValueError("invalid artifacts")
        artifacts: list[Path] = []
        target_root = (previous / "targets").resolve()
        for record in records:
            relative = str(record["target"])
            parsed = PurePosixPath(relative)
            if parsed.is_absolute() or ".." in parsed.parts or "\\" in relative or ":" in relative:
                raise ValueError("unsafe artifact")
            artifact = target_root.joinpath(*parsed.parts).resolve()
            artifact.relative_to(target_root)
            if (
                not artifact.is_file()
                or artifact.stat().st_size != int(record["length"])
                or _sha256(artifact) != str(record["sha256"]).lower()
            ):
                raise ValueError("artifact mismatch")
            artifacts.append(artifact)
    except OnlineReleaseError:
        raise
    except Exception as exc:
        raise OnlineReleaseError("Os artefatos autenticados da política anterior são inválidos.") from exc
    return prepare_online_repository(
        artifacts=artifacts,
        site_root=site_root,
        channel=channel,
        rollout_percent=rollout_percent,
        rollout_seed=rollout_seed,
        mandatory=mandatory,
        bootstrap_root=bootstrap_root,
        role_signers=role_signers,
        signed_binaries=artifacts,
        authenticode_checker=lambda path: True,
        previous_repository=previous,
        reference_time=reference_time,
    )


def _load_online_signers() -> dict[str, Signer]:
    signers: dict[str, Signer] = {}
    for role, secret_name in SECRET_NAMES.items():
        value = os.environ.get(secret_name, "")
        if not value:
            raise OnlineReleaseError("As chaves online TUF não estão disponíveis no ambiente protegido.")
        signers[role] = signer_from_private_pem(value.encode("utf-8"))
    return signers


def _run(command: list[str], *, cwd: Path) -> None:
    result = subprocess.run(command, cwd=cwd, check=False)
    if result.returncode:
        raise OnlineReleaseError("Uma ferramenta de build recusou a release online.")


def _find_velopack_artifacts(directory: Path, channel: str) -> list[Path]:
    packages = sorted(directory.glob("*-full.nupkg"))
    feed = directory / f"releases.{channel}.json"
    return _validate_artifacts(packages + [feed], channel)


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera a árvore TUF/Velopack do TrigoPDV.")
    parser.add_argument("--channel", choices=CHANNELS)
    parser.add_argument("--pack-directory", type=Path, default=ROOT / "dist" / "TrigoPDV")
    parser.add_argument("--release-directory", type=Path, default=ROOT / "release" / "velopack")
    parser.add_argument("--site-root", type=Path, default=ROOT / "_site")
    parser.add_argument("--rollout", type=int, default=100)
    parser.add_argument("--seed")
    parser.add_argument("--mandatory", action="store_true")
    parser.add_argument("--refresh-only", action="store_true")
    parser.add_argument("--policy-only", action="store_true")
    parser.add_argument("--initialize-empty", action="store_true")
    parser.add_argument("--continuity-checkpoint", type=Path)
    parser.add_argument(
        "--continuity-output",
        type=Path,
        default=ROOT / "_continuity-next" / "checkpoint.json",
    )
    args = parser.parse_args()
    previous_directory = ROOT / ".previous-update-repository"
    try:
        root_path = ROOT / "updates" / "trusted" / "root.json"
        if not root_path.is_file():
            raise OnlineReleaseError("A raiz TUF pública definitiva não acompanha a montagem.")
        root_bytes = root_path.read_bytes()
        checkpoint = (
            load_continuity_checkpoint(args.continuity_checkpoint)
            if args.continuity_checkpoint is not None and args.continuity_checkpoint.is_file()
            else None
        )
        previous_repository = download_authenticated_repository(
            PILOT_UPDATE_URL,
            previous_directory,
            bootstrap_root=root_bytes,
            continuity_checkpoint=checkpoint,
            allow_empty_initialization=bool(args.initialize_empty),
        )
        if args.refresh_only and args.policy_only:
            raise OnlineReleaseError("Renovação e alteração de política são modos exclusivos.")
        if args.refresh_only:
            if args.initialize_empty or previous_repository is None:
                raise OnlineReleaseError("A renovação agendada nunca inicializa um repositório vazio.")
            result = refresh_online_repository(
                previous_repository=previous_repository,
                site_root=args.site_root,
                bootstrap_root=root_bytes,
                role_signers=_load_online_signers(),
            )
            write_continuity_checkpoint(
                result.repository_root,
                args.continuity_output,
                previous_checkpoint=checkpoint,
            )
            print("Validade TUF renovada sem alterar release, rollout ou artefatos.")
            return 0
        if args.channel is None or not args.seed:
            raise OnlineReleaseError("Canal e semente são obrigatórios para publicar uma release.")
        if args.policy_only:
            if args.initialize_empty or previous_repository is None:
                raise OnlineReleaseError("Uma alteração de política exige predecessor autenticado.")
            result = prepare_policy_repository(
                previous_repository=previous_repository,
                site_root=args.site_root,
                channel=args.channel,
                rollout_percent=args.rollout,
                rollout_seed=args.seed,
                mandatory=args.mandatory,
                bootstrap_root=root_bytes,
                role_signers=_load_online_signers(),
            )
            write_continuity_checkpoint(
                result.repository_root,
                args.continuity_output,
                previous_checkpoint=checkpoint,
            )
            print(f"Política autenticada do canal {args.channel} pronta para publicar.")
            return 0
        if args.release_directory.exists():
            raise OnlineReleaseError("A pasta Velopack precisa começar vazia.")
        args.release_directory.mkdir(parents=True)
        _run(
            velopack_pack_command(
                pack_directory=args.pack_directory,
                output_directory=args.release_directory,
                channel=args.channel,
            ),
            cwd=ROOT,
        )
        artifacts = _find_velopack_artifacts(args.release_directory, args.channel)
        setup_candidates = sorted(args.release_directory.glob("*Setup*.exe"))
        result = prepare_online_repository(
            artifacts=artifacts,
            site_root=args.site_root,
            channel=args.channel,
            rollout_percent=args.rollout,
            rollout_seed=args.seed,
            mandatory=args.mandatory,
            bootstrap_root=root_bytes,
            role_signers=_load_online_signers(),
            signed_binaries=[args.pack_directory / "TrigoPDV.exe", *setup_candidates],
            previous_repository=previous_repository,
        )
        write_continuity_checkpoint(
            result.repository_root,
            args.continuity_output,
            previous_checkpoint=checkpoint,
        )
    except OnlineReleaseError as exc:
        print(f"RELEASE ONLINE BLOQUEADA: {exc}", file=sys.stderr)
        return 1
    finally:
        if previous_directory.exists():
            shutil.rmtree(previous_directory, ignore_errors=True)
    print(f"Release {RELEASE.version} autenticada e pronta para publicar no canal {args.channel}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
