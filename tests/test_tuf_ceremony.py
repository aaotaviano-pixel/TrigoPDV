from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from tuf.api.metadata import Metadata, Root

from tools.tuf_ceremony import (
    CREDENTIAL_TARGET,
    CeremonyError,
    _credential_passphrase,
    create_ceremony,
    signer_from_private_pem,
    verify_ceremony_custody,
)


ROOT = Path(__file__).resolve().parent.parent


class TufCeremonyTestCase(unittest.TestCase):
    """Breaks caught: root keys leaking into Git or a single key owning trust."""

    def test_creates_two_of_three_root_outside_project_and_distinct_online_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory) / "offline-root"
            public_root = Path(directory) / "public" / "root.json"

            result = create_ceremony(
                key_directory=external,
                public_root_path=public_root,
                project_root=ROOT,
                passphrase=b"test-passphrase-with-32-bytes-minimum",
                reference_time=datetime(2026, 8, 16, tzinfo=timezone.utc),
            )

            metadata = Metadata.from_file(public_root)
            self.assertIsInstance(metadata.signed, Root)
            self.assertEqual(metadata.signed.roles["root"].threshold, 2)
            self.assertEqual(len(metadata.signed.roles["root"].keyids), 3)
            self.assertEqual(set(result.online_private_pems), {"targets", "snapshot", "timestamp"})
            self.assertEqual(len(set(result.online_keyids.values())), 3)
            self.assertEqual(len(result.root_private_paths), 3)
            for path in result.root_private_paths:
                payload = path.read_bytes()
                self.assertIn(b"BEGIN ENCRYPTED PRIVATE KEY", payload)
                self.assertNotIn(b"BEGIN PRIVATE KEY", payload)
            for role, private_pem in result.online_private_pems.items():
                signer = signer_from_private_pem(private_pem)
                self.assertEqual(signer.public_key.keyid, result.online_keyids[role])
            self.assertFalse(any(ROOT.rglob("*.root-private.pem")))

    def test_refuses_key_directory_inside_project(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            with self.assertRaisesRegex(CeremonyError, "fora do projeto"):
                create_ceremony(
                    key_directory=Path(directory) / "keys",
                    public_root_path=Path(directory) / "root.json",
                    project_root=ROOT,
                    passphrase=b"test-passphrase-with-32-bytes-minimum",
                    reference_time=datetime(2026, 8, 16, tzinfo=timezone.utc),
                )

    def test_never_overwrites_existing_root_key_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory) / "offline-root"
            public_root = Path(directory) / "public" / "root.json"
            arguments = dict(
                key_directory=external,
                public_root_path=public_root,
                project_root=ROOT,
                passphrase=b"test-passphrase-with-32-bytes-minimum",
                reference_time=datetime(2026, 8, 16, tzinfo=timezone.utc),
            )
            create_ceremony(**arguments)
            before = {path.name: path.read_bytes() for path in external.iterdir()}

            with self.assertRaisesRegex(CeremonyError, "já existe"):
                create_ceremony(**arguments)

            self.assertEqual(before, {path.name: path.read_bytes() for path in external.iterdir()})

    def test_windows_credential_blob_uses_unicode_and_is_returned_as_bytes(self) -> None:
        captured = {}
        class MissingCredentialError(RuntimeError):
            winerror = 1168

        fake = types.SimpleNamespace(
            CRED_TYPE_GENERIC=1,
            CRED_PERSIST_LOCAL_MACHINE=2,
            CredRead=lambda *args: (_ for _ in ()).throw(MissingCredentialError("missing")),
            CredWrite=lambda record, flags: captured.update(record=record, flags=flags),
        )
        with patch.dict("sys.modules", {"win32cred": fake}), patch(
            "tools.tuf_ceremony.secrets.token_urlsafe",
            return_value="portable-generated-passphrase-value",
        ):
            value = _credential_passphrase(create_if_missing=True)

        self.assertEqual(value, b"portable-generated-passphrase-value")
        self.assertEqual(captured["record"]["TargetName"], CREDENTIAL_TARGET)
        self.assertIsInstance(captured["record"]["CredentialBlob"], str)

    def test_credential_read_failure_never_overwrites_existing_custody(self) -> None:
        for failure in (RuntimeError("credential service unavailable"),):
            writes = []
            fake = types.SimpleNamespace(
                CRED_TYPE_GENERIC=1,
                CRED_PERSIST_LOCAL_MACHINE=2,
                CredRead=lambda *args, failure=failure: (_ for _ in ()).throw(failure),
                CredWrite=lambda *args: writes.append(args),
            )
            with self.subTest(failure=type(failure).__name__), patch.dict(
                "sys.modules", {"win32cred": fake}
            ):
                with self.assertRaisesRegex(CeremonyError, "Gerenciador de Credenciais"):
                    _credential_passphrase(create_if_missing=True)
            self.assertEqual(writes, [])

    def test_invalid_existing_credential_is_rejected_without_overwrite(self) -> None:
        writes = []
        fake = types.SimpleNamespace(
            CRED_TYPE_GENERIC=1,
            CRED_PERSIST_LOCAL_MACHINE=2,
            CredRead=lambda *args: {"CredentialBlob": "short"},
            CredWrite=lambda *args: writes.append(args),
        )
        with patch.dict("sys.modules", {"win32cred": fake}):
            with self.assertRaisesRegex(CeremonyError, "inválida"):
                _credential_passphrase(create_if_missing=True)
        self.assertEqual(writes, [])

    def test_missing_credential_is_not_created_during_read_only_verification(self) -> None:
        class MissingCredentialError(RuntimeError):
            winerror = 1168

        writes = []
        fake = types.SimpleNamespace(
            CRED_TYPE_GENERIC=1,
            CRED_PERSIST_LOCAL_MACHINE=2,
            CredRead=lambda *args: (_ for _ in ()).throw(MissingCredentialError("missing")),
            CredWrite=lambda *args: writes.append(args),
        )
        with patch.dict("sys.modules", {"win32cred": fake}):
            with self.assertRaisesRegex(CeremonyError, "não foi encontrada"):
                _credential_passphrase()
        self.assertEqual(writes, [])

    def test_direct_cli_resolves_project_imports_without_writing_inside_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ)
            env["TRIGOPDV_TUF_ROOT_PASSPHRASE"] = "cli-test-passphrase-value-with-32-bytes"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "tuf_ceremony.py"),
                    "--key-directory",
                    str(root / "keys"),
                    "--public-root",
                    str(root / "public" / "root.json"),
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "public" / "root.json").is_file())

    def test_custody_verification_rejects_a_missing_offline_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            passphrase = b"test-passphrase-with-32-bytes-minimum"
            result = create_ceremony(
                key_directory=root / "keys",
                public_root_path=root / "public" / "root.json",
                project_root=ROOT,
                passphrase=passphrase,
                reference_time=datetime(2026, 8, 16, tzinfo=timezone.utc),
            )
            result.root_private_paths[1].unlink()

            with self.assertRaisesRegex(CeremonyError, "custódia"):
                verify_ceremony_custody(result, passphrase=passphrase)


if __name__ == "__main__":
    unittest.main()
