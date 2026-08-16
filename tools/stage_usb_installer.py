"""Build a deterministic, self-verifying USB installer directory."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
from uuid import uuid4


class UsbStageError(RuntimeError):
    pass


@dataclass(frozen=True)
class UsbStageResult:
    root: Path
    manifest: Path


MANIFEST_NAME = "MANIFESTO-SHA256.txt"
SETUP_NAME = "TrigoPDV-Setup.exe"
ALLOWED_SQLITE = "dados-iniciais/catalogo-produtos.sqlite3"


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def _safe_sources(source: Path) -> list[tuple[str, Path]]:
    records: list[tuple[str, Path]] = []
    for path in source.rglob("*"):
        if path.is_symlink():
            raise UsbStageError("O pacote USB não aceita links simbólicos.")
        if not path.is_file():
            continue
        relative = path.relative_to(source).as_posix()
        lowered = relative.casefold()
        if lowered in {MANIFEST_NAME.casefold(), SETUP_NAME.casefold()}:
            continue
        if (
            lowered.endswith(("-wal", "-shm"))
            or PurePosixPath(relative).name.casefold() == "config.ini"
            or (lowered.endswith((".sqlite", ".sqlite3", ".db")) and lowered != ALLOWED_SQLITE)
        ):
            raise UsbStageError("O pacote USB contém dados operacionais e foi bloqueado.")
        records.append((relative, path))
    return sorted(records, key=lambda item: item[0].casefold())


def verify_usb_package(root: str | Path) -> bool:
    package = Path(root).resolve()
    manifest = package / MANIFEST_NAME
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
        expected: dict[str, str] = {}
        for line in lines:
            digest, separator, relative = line.partition(" *")
            path = PurePosixPath(relative)
            if (
                separator != " *"
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in relative
                or ":" in relative
                or relative in expected
            ):
                return False
            expected[relative] = digest
        actual = {
            path.relative_to(package).as_posix(): path
            for path in package.rglob("*")
            if path.is_file() and path.name != MANIFEST_NAME
        }
        return (
            set(actual) == set(expected)
            and SETUP_NAME in actual
            and all(_digest(actual[name]) == digest for name, digest in expected.items())
        )
    except (OSError, ValueError):
        return False


def stage_usb_package(
    source_directory: str | Path,
    setup_path: str | Path,
    output_directory: str | Path,
) -> UsbStageResult:
    source = Path(source_directory).resolve()
    setup = Path(setup_path).resolve()
    output = Path(output_directory).resolve()
    if not source.is_dir() or not setup.is_file() or output.exists():
        raise UsbStageError("A origem, o Setup ou o destino do pacote USB é inválido.")
    files = _safe_sources(source)
    staging = output.parent / f".{output.name}.{uuid4().hex}.staging"
    try:
        staging.mkdir(parents=True, exist_ok=False)
        for relative, source_path in files:
            destination = staging / Path(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
        shutil.copyfile(setup, staging / SETUP_NAME)
        staged_files = sorted(
            (path for path in staging.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(staging).as_posix().casefold(),
        )
        lines = [
            f"{_digest(path)} *{path.relative_to(staging).as_posix()}"
            for path in staged_files
        ]
        manifest = staging / MANIFEST_NAME
        with manifest.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if not verify_usb_package(staging):
            raise UsbStageError("O manifesto final do pacote USB não confere.")
        os.replace(staging, output)
        return UsbStageResult(output, output / MANIFEST_NAME)
    except UsbStageError:
        raise
    except OSError as exc:
        raise UsbStageError("Não foi possível montar o pacote USB.") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Monta o pacote final para pen drive.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = stage_usb_package(args.source, args.setup, args.output)
    except UsbStageError as exc:
        print(f"PACOTE USB BLOQUEADO: {exc}")
        return 1
    print(f"Pacote USB verificado: {result.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
