from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import tempfile
import unittest

from securesystemslib.signer import CryptoSigner

from config.version import RELEASE
from tools.build_online_release import (
    OnlineReleaseError,
    prepare_online_repository,
    velopack_pack_command,
)
from tools.tuf_repository import create_root_metadata, verify_repository


class OnlineReleasePipelineTestCase(unittest.TestCase):
    """Breaks caught: wrong package, data leak or unsigned stable deployment."""

    def setUp(self) -> None:
        self.root_signers = [CryptoSigner.generate_ed25519() for _ in range(3)]
        self.role_signers = {
            role: CryptoSigner.generate_ed25519()
            for role in ("targets", "snapshot", "timestamp")
        }
        self.now = datetime.now(timezone.utc)
        self.bootstrap = create_root_metadata(
            self.root_signers,
            self.role_signers,
            version=1,
            expires=self.now.replace(year=self.now.year + 1),
        )

    def _artifacts(self, root: Path, channel: str = "pilot") -> list[Path]:
        package = root / f"TrigoPDV-{RELEASE.version}-full.nupkg"
        feed = root / f"releases.{channel}.json"
        package.write_bytes(b"velopack-full-package")
        feed.write_text('{"Assets":[]}', encoding="utf-8")
        return [package, feed]

    def test_pilot_repository_contains_only_tuf_metadata_and_velopack_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            artifacts = self._artifacts(root)

            result = prepare_online_repository(
                artifacts=artifacts,
                site_root=root / "site",
                channel="pilot",
                rollout_percent=100,
                rollout_seed="pilot-seed-20260816",
                mandatory=False,
                bootstrap_root=self.bootstrap,
                role_signers=self.role_signers,
                reference_time=self.now,
            )

            names = {
                path.relative_to(result.repository_root).as_posix()
                for path in result.repository_root.rglob("*")
                if path.is_file()
            }
            self.assertIn("metadata/timestamp.json", names)
            self.assertTrue(any(name.endswith("-full.nupkg") for name in names))
            self.assertTrue(any(name.endswith("releases.pilot.json") for name in names))
            self.assertFalse(any("config.ini" in name or "/data/" in name for name in names))
            self.assertEqual(result.write_order[-1], "metadata/timestamp.json")
            verified = verify_repository(
                result.repository_root,
                bootstrap_root=self.bootstrap,
                channel="pilot",
                reference_time=self.now,
            )
            self.assertEqual(verified["sequence"], RELEASE.sequence)

    def test_stable_refuses_any_binary_without_valid_authenticode(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            artifacts = self._artifacts(root, "stable")
            executable = root / "TrigoPDV.exe"
            setup = root / "TrigoPDV-Setup.exe"
            executable.write_bytes(b"unsigned exe")
            setup.write_bytes(b"unsigned setup")

            with self.assertRaisesRegex(OnlineReleaseError, "Authenticode"):
                prepare_online_repository(
                    artifacts=artifacts,
                    site_root=root / "site",
                    channel="stable",
                    rollout_percent=100,
                    rollout_seed="stable-seed-20260816",
                    mandatory=False,
                    bootstrap_root=self.bootstrap,
                    role_signers=self.role_signers,
                    signed_binaries=[executable, setup],
                    authenticode_checker=lambda path: False,
                    reference_time=self.now,
                )

    def test_rejects_non_velopack_artifact_before_copying_it(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            artifacts = self._artifacts(root)
            local_config = root / "config.ini"
            local_config.write_text("secret=value", encoding="utf-8")
            artifacts.append(local_config)

            with self.assertRaisesRegex(OnlineReleaseError, "artefatos Velopack"):
                prepare_online_repository(
                    artifacts=artifacts,
                    site_root=root / "site",
                    channel="pilot",
                    rollout_percent=100,
                    rollout_seed="pilot-seed-20260816",
                    mandatory=False,
                    bootstrap_root=self.bootstrap,
                    role_signers=self.role_signers,
                    reference_time=self.now,
                )
            self.assertFalse((root / "site").exists())

    def test_velopack_command_is_full_only_and_uses_immutable_identity(self) -> None:
        command = velopack_pack_command(
            pack_directory=Path("C:/build/TrigoPDV"),
            output_directory=Path("C:/build/Releases"),
            channel="pilot",
        )
        self.assertIsInstance(command, list)
        self.assertEqual(command[0:2], ["vpk", "pack"])
        self.assertIn("--yes", command)
        self.assertIn("--skip-updates", command)
        self.assertEqual(command[command.index("--packId") + 1], "TrigoDeMinas.TrigoPDV")
        self.assertEqual(command[command.index("--packVersion") + 1], RELEASE.version)
        self.assertEqual(command[command.index("--channel") + 1], "pilot")
        self.assertEqual(command[command.index("--delta") + 1].casefold(), "none")
        self.assertNotIn("shell=True", command)

    def test_github_workflows_pin_actions_and_pages_deploy_waits_for_verification(self) -> None:
        root = Path(__file__).resolve().parents[1]
        publication = (root / ".github" / "workflows" / "publish-update.yml").read_text(
            encoding="utf-8"
        )
        codeql = (root / ".github" / "workflows" / "codeql.yml").read_text(
            encoding="utf-8"
        )
        for workflow in (publication, codeql):
            references = re.findall(r"uses:\s*[^@\s]+@([^\s#]+)", workflow)
            self.assertTrue(references)
            self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in references))
        self.assertIn("needs: build", publication)
        self.assertIn("python tools/release_gate.py", publication)
        self.assertIn("python -m unittest discover -s tests -q", publication)
        self.assertIn("TRIGOPDV_TUF_TARGETS_KEY", publication)
        self.assertNotIn("TRIGOPDV_TUF_ROOT_PASSPHRASE", publication)


if __name__ == "__main__":
    unittest.main()
