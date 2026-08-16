"""Monta pacote Velopack e árvore TUF estática para GitHub Pages."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable, Mapping, Sequence
from uuid import uuid4

from securesystemslib.signer import Signer


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
            metadata_version=RELEASE.sequence,
            targets_expires=now + timedelta(days=90),
            snapshot_expires=now + timedelta(days=14),
            timestamp_expires=now + timedelta(days=2),
            reference_time=now,
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
    parser.add_argument("--channel", choices=("internal", "pilot", "stable"), required=True)
    parser.add_argument("--pack-directory", type=Path, default=ROOT / "dist" / "TrigoPDV")
    parser.add_argument("--release-directory", type=Path, default=ROOT / "release" / "velopack")
    parser.add_argument("--site-root", type=Path, default=ROOT / "_site")
    parser.add_argument("--rollout", type=int, default=100)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--mandatory", action="store_true")
    args = parser.parse_args()
    try:
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
        root_path = ROOT / "updates" / "trusted" / "root.json"
        if not root_path.is_file():
            raise OnlineReleaseError("A raiz TUF pública definitiva não acompanha a montagem.")
        setup_candidates = sorted(args.release_directory.glob("*Setup*.exe"))
        prepare_online_repository(
            artifacts=artifacts,
            site_root=args.site_root,
            channel=args.channel,
            rollout_percent=args.rollout,
            rollout_seed=args.seed,
            mandatory=args.mandatory,
            bootstrap_root=root_path.read_bytes(),
            role_signers=_load_online_signers(),
            signed_binaries=[args.pack_directory / "TrigoPDV.exe", *setup_candidates],
        )
    except OnlineReleaseError as exc:
        print(f"RELEASE ONLINE BLOQUEADA: {exc}", file=sys.stderr)
        return 1
    print(f"Release {RELEASE.version} autenticada e pronta para publicar no canal {args.channel}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
