"""Prepara targets para assinatura TUF; não acessa nem armazena chaves."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.version import RELEASE


def create_manifest(
    artifacts: list[Path], output_root: Path, *, channel: str,
    rollout_percent: int, rollout_seed: str, mandatory: bool,
) -> Path:
    if channel not in {"internal", "pilot", "stable"} or not 0 <= rollout_percent <= 100:
        raise ValueError("Canal ou percentual inválido.")
    if not rollout_seed or len(rollout_seed) > 100:
        raise ValueError("A semente assinada de rollout é obrigatória.")
    release_dir = output_root / "targets" / "releases" / RELEASE.version
    release_dir.mkdir(parents=True, exist_ok=True)
    records = []
    names: set[str] = set()
    for source in artifacts:
        source = source.resolve()
        if not source.is_file() or source.name in names or any(character in source.name for character in ("/", "\\", ":")):
            raise ValueError("Artefato ausente, duplicado ou com nome inválido.")
        names.add(source.name)
        destination = release_dir / source.name
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
        content = destination.read_bytes()
        records.append({
            "target": f"releases/{RELEASE.version}/{source.name}",
            "length": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        })
    manifest = {
        "version": RELEASE.version,
        "sequence": RELEASE.sequence,
        "schema_target": RELEASE.schema_target,
        "pack_id": RELEASE.pack_id,
        "channel": channel,
        "rollout_percent": rollout_percent,
        "rollout_seed": rollout_seed,
        "mandatory": mandatory,
        "artifacts": records,
    }
    manifest_path = output_root / "targets" / "channels" / channel / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    temporary_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_manifest, manifest_path)
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--channel", choices=("internal", "pilot", "stable"), required=True)
    parser.add_argument("--rollout", type=int, required=True)
    parser.add_argument("--seed", required=True, help="Semente pública que fará parte dos metadados assinados")
    parser.add_argument("--mandatory", action="store_true")
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args()
    create_manifest(
        args.artifacts, args.output, channel=args.channel,
        rollout_percent=args.rollout, rollout_seed=args.seed, mandatory=args.mandatory,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

