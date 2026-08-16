"""Gate local: fonte pode ser validada; produção exige confiança externa real."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.version import RELEASE
from db.schema import SCHEMA_VERSION
from services.catalog_bootstrap import CatalogManifest


class GateFailure(RuntimeError):
    pass


def _normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    canonical = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _source_gate() -> None:
    if RELEASE.schema_target != SCHEMA_VERSION:
        raise GateFailure("Versão e schema não coincidem.")
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "render_release_metadata.py"), "--check"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise GateFailure("Metadados gerados estão desatualizados.")
    lock_lines: list[str] = []
    continued = ""
    for raw_line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            continued += line[:-1].strip() + " "
            continue
        lock_lines.append((continued + line).strip())
        continued = ""
    if continued:
        raise GateFailure("O lock de dependências termina com uma continuação incompleta.")
    if not lock_lines or any("==" not in line or "--hash=sha256:" not in line for line in lock_lines):
        raise GateFailure("O lock de dependências não possui versões e hashes completos.")
    lock_digest = _normalized_text_sha256(ROOT / "requirements.lock")
    recorded_lock_digest = (ROOT / "release" / "sbom.lock.sha256").read_text(encoding="utf-8").strip()
    if lock_digest != recorded_lock_digest or not (ROOT / "release" / "sbom.cdx.json").is_file():
        raise GateFailure("O SBOM precisa ser regenerado após alterar o lock de dependências.")
    data = ROOT / "TrigoPDV_Instalacao_PenDrive" / "dados-iniciais"
    if (data / "trigo_de_minas.sqlite3").exists():
        raise GateFailure("O pacote ainda contém um banco operacional.")
    manifest = CatalogManifest.load(data / "catalogo-produtos.manifest.json")
    catalog = data / "catalogo-produtos.sqlite3"
    if not catalog.is_file():
        raise GateFailure("O catálogo somente de produtos está ausente.")
    if hashlib.sha256(catalog.read_bytes()).hexdigest() != manifest.file_sha256:
        raise GateFailure("O catálogo somente de produtos diverge do manifesto.")


def _authenticode_valid(path: Path) -> bool:
    if not path.is_file():
        return False
    command = (
        "$s=Get-AuthenticodeSignature -LiteralPath $args[0]; "
        "[Console]::Out.Write($s.Status.ToString())"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command, str(path)],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "Valid"


def _production_gate() -> None:
    base_url = os.environ.get("TRIGOPDV_UPDATE_BASE_URL", "").strip()
    parsed = urlsplit(base_url)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise GateFailure("Configure um repositório HTTPS de atualização sem credenciais na URL.")
    root_path = ROOT / "updates" / "trusted" / "root.json"
    if not root_path.is_file():
        raise GateFailure("A raiz TUF assinada e embutida ainda não foi fornecida.")
    try:
        from tuf.api.metadata import Metadata, Root

        metadata = Metadata.from_bytes(root_path.read_bytes())
        if not isinstance(metadata.signed, Root) or metadata.signed.is_expired():
            raise GateFailure("A raiz TUF é inválida ou está expirada.")
    except GateFailure:
        raise
    except Exception as exc:
        raise GateFailure("A raiz TUF não pôde ser validada.") from exc
    executable = ROOT / "dist" / "TrigoPDV" / "TrigoPDV.exe"
    setup = ROOT / "installer" / "Output" / "TrigoPDV-Setup.exe"
    if not _authenticode_valid(executable) or not _authenticode_valid(setup):
        raise GateFailure("Executável e instalador precisam de assinatura Authenticode válida.")
    tuf_metadata = ROOT / "release" / "tuf-repository" / "metadata"
    if not all((tuf_metadata / name).is_file() for name in ("root.json", "timestamp.json", "snapshot.json", "targets.json")):
        raise GateFailure("Os metadados TUF assinados da publicação estão incompletos.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--production", action="store_true")
    args = parser.parse_args()
    try:
        _source_gate()
        if args.production:
            _production_gate()
    except (GateFailure, OSError, ValueError) as exc:
        print(f"GATE BLOQUEADO: {exc}", file=sys.stderr)
        return 1
    print("GATE DE FONTE APROVADO" if not args.production else "GATE DE PRODUÇÃO APROVADO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
