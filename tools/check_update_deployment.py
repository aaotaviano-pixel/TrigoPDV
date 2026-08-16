"""Fail-closed continuity check immediately before a GitHub Pages deploy."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import PILOT_UPDATE_URL
from tools.build_online_release import (
    OnlineReleaseError,
    deployment_continuity_status,
    load_continuity_checkpoint,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Confere continuidade antes do deploy Pages.")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--committed", type=Path)
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument(
        "--root", type=Path, default=ROOT / "updates" / "trusted" / "root.json",
    )
    args = parser.parse_args()
    try:
        candidate = load_continuity_checkpoint(args.candidate)
        committed = (
            load_continuity_checkpoint(args.committed)
            if args.committed is not None and args.committed.is_file()
            else None
        )
        status = deployment_continuity_status(
            base_url=PILOT_UPDATE_URL,
            candidate_checkpoint=candidate,
            committed_checkpoint=committed,
            bootstrap_root=args.root.read_bytes(),
            allow_empty_initialization=args.allow_empty,
        )
    except (OSError, OnlineReleaseError) as exc:
        print(f"DEPLOY BLOQUEADO: {exc}", file=sys.stderr)
        return 1
    print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
