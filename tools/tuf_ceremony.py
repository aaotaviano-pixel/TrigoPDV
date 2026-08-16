"""Cerimônia local de chaves TUF.

Somente a raiz pública pode ser escrita dentro do projeto. Material privado é
PKCS8 criptografado em uma pasta externa e secrets online são enviados ao
GitHub por stdin, nunca exibidos.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Mapping
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from securesystemslib.signer import CryptoSigner


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ROLE_NAMES = ("targets", "snapshot", "timestamp")
CREDENTIAL_TARGET = "TrigoPDV/TUF/RootPassphrase"
SECRET_NAMES = {
    "targets": "TRIGOPDV_TUF_TARGETS_KEY",
    "snapshot": "TRIGOPDV_TUF_SNAPSHOT_KEY",
    "timestamp": "TRIGOPDV_TUF_TIMESTAMP_KEY",
}


class CeremonyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CeremonyResult:
    public_root_path: Path
    root_private_paths: tuple[Path, ...]
    online_private_paths: Mapping[str, Path]
    online_private_pems: Mapping[str, bytes]
    online_keyids: Mapping[str, str]


def signer_from_private_pem(data: bytes, *, password: bytes | None = None) -> CryptoSigner:
    try:
        private_key = serialization.load_pem_private_key(bytes(data), password=password)
        return CryptoSigner(private_key)
    except Exception as exc:
        raise CeremonyError("Uma chave privada TUF não pôde ser carregada.") from exc


def _encrypted_private_pem(signer: CryptoSigner, passphrase: bytes) -> bytes:
    private_key = serialization.load_pem_private_key(signer.private_bytes, password=None)
    return private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(passphrase),
    )


def _atomic_private_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def create_ceremony(
    *,
    key_directory: str | Path,
    public_root_path: str | Path,
    project_root: str | Path,
    passphrase: bytes,
    reference_time: datetime | None = None,
) -> CeremonyResult:
    """Cria uma cerimônia nova; nunca continua nem sobrescreve material."""

    project = Path(project_root).resolve()
    key_dir = Path(key_directory).resolve()
    public_root = Path(public_root_path).resolve()
    if _inside(key_dir, project):
        raise CeremonyError("A pasta de chaves privadas precisa ficar fora do projeto.")
    if len(passphrase) < 24:
        raise CeremonyError("A frase de proteção das chaves é insuficiente.")
    if public_root.exists() or (key_dir.exists() and any(key_dir.iterdir())):
        raise CeremonyError("A cerimônia TUF já existe e não será sobrescrita.")

    from tools.tuf_repository import create_root_metadata

    now = (reference_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    root_signers = [CryptoSigner.generate_ed25519() for _ in range(3)]
    role_signers = {
        role: CryptoSigner.generate_ed25519()
        for role in ROLE_NAMES
    }
    root_bytes = create_root_metadata(
        root_signers,
        role_signers,
        version=1,
        expires=now + timedelta(days=730),
    )

    key_dir.mkdir(parents=True, exist_ok=False)
    root_paths: list[Path] = []
    online_paths: dict[str, Path] = {}
    try:
        for index, signer in enumerate(root_signers, start=1):
            path = key_dir / f"root-{index}.private.pem"
            _atomic_private_write(path, _encrypted_private_pem(signer, passphrase))
            root_paths.append(path)
        for role, signer in role_signers.items():
            path = key_dir / f"{role}.private.pem"
            _atomic_private_write(path, _encrypted_private_pem(signer, passphrase))
            online_paths[role] = path

        public_root.parent.mkdir(parents=True, exist_ok=True)
        temporary_root = public_root.with_name(f".{public_root.name}.{uuid4().hex}.tmp")
        try:
            temporary_root.write_bytes(root_bytes)
            os.replace(temporary_root, public_root)
        finally:
            temporary_root.unlink(missing_ok=True)
    except Exception:
        for path in key_dir.iterdir() if key_dir.exists() else ():
            path.unlink(missing_ok=True)
        if key_dir.exists():
            key_dir.rmdir()
        public_root.unlink(missing_ok=True)
        raise

    return CeremonyResult(
        public_root_path=public_root,
        root_private_paths=tuple(root_paths),
        online_private_paths=online_paths,
        online_private_pems={role: role_signers[role].private_bytes for role in ROLE_NAMES},
        online_keyids={role: role_signers[role].public_key.keyid for role in ROLE_NAMES},
    )


def verify_ceremony_custody(result: CeremonyResult, *, passphrase: bytes) -> None:
    """Prova que as seis chaves privadas ainda existem, abrem e casam com root."""

    try:
        from tuf.api.metadata import Metadata, Root

        metadata = Metadata.from_file(result.public_root_path)
        if not isinstance(metadata.signed, Root):
            raise ValueError("not root")
        metadata.verify_delegate("root", metadata)
        root_ids = {
            signer_from_private_pem(path.read_bytes(), password=passphrase).public_key.keyid
            for path in result.root_private_paths
        }
        if root_ids != set(metadata.signed.roles["root"].keyids):
            raise ValueError("root keys differ")
        if set(result.online_private_paths) != set(ROLE_NAMES):
            raise ValueError("online roles missing")
        for role in ROLE_NAMES:
            signer = signer_from_private_pem(
                result.online_private_paths[role].read_bytes(),
                password=passphrase,
            )
            if signer.public_key.keyid not in metadata.signed.roles[role].keyids:
                raise ValueError("online key differs")
    except Exception as exc:
        raise CeremonyError("A custódia das chaves TUF não pôde ser comprovada.") from exc


def _credential_passphrase() -> bytes:
    configured = os.environ.get("TRIGOPDV_TUF_ROOT_PASSPHRASE", "")
    if configured:
        value = configured.encode("utf-8")
        if len(value) < 24:
            raise CeremonyError("A frase de proteção configurada é insuficiente.")
        return value
    try:
        import win32cred

        try:
            record = win32cred.CredRead(CREDENTIAL_TARGET, win32cred.CRED_TYPE_GENERIC)
            blob = record["CredentialBlob"]
            value = blob.encode("utf-8") if isinstance(blob, str) else bytes(blob)
            if len(value) >= 24:
                return value
        except Exception:
            pass
        value_text = secrets.token_urlsafe(48)
        value = value_text.encode("ascii")
        win32cred.CredWrite({
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": CREDENTIAL_TARGET,
            "UserName": os.environ.get("USERNAME", "TrigoPDVRelease"),
            "CredentialBlob": value_text,
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
        }, 0)
        return value
    except Exception as exc:
        raise CeremonyError(
            "Não foi possível proteger a frase das chaves no Gerenciador de Credenciais do Windows."
        ) from exc


def _upload_github_secrets(repository: str, values: Mapping[str, bytes]) -> None:
    if "/" not in repository or any(character.isspace() for character in repository):
        raise CeremonyError("O repositório GitHub informado é inválido.")
    for role in ROLE_NAMES:
        result = subprocess.run(
            [
                "gh", "secret", "set", SECRET_NAMES[role],
                "--repo", repository,
            ],
            input=values[role],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode:
            raise CeremonyError("O GitHub não aceitou um secret TUF online.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Cria a raiz TUF do TrigoPDV sem expor chaves.")
    parser.add_argument("--key-directory", type=Path, required=True)
    parser.add_argument(
        "--public-root",
        type=Path,
        default=ROOT / "updates" / "trusted" / "root.json",
    )
    parser.add_argument("--github-repository", default="")
    args = parser.parse_args()
    try:
        passphrase = _credential_passphrase()
        result = create_ceremony(
            key_directory=args.key_directory,
            public_root_path=args.public_root,
            project_root=ROOT,
            passphrase=passphrase,
        )
        verify_ceremony_custody(result, passphrase=passphrase)
        if args.github_repository:
            _upload_github_secrets(args.github_repository, result.online_private_pems)
        verify_ceremony_custody(result, passphrase=passphrase)
    except CeremonyError as exc:
        print(f"CERIMÔNIA BLOQUEADA: {exc}", file=sys.stderr)
        return 1
    print("Raiz TUF pública criada; material privado permaneceu fora do projeto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
