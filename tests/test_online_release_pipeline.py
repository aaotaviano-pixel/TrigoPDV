from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import tempfile
import types
import unittest

from securesystemslib.signer import CryptoSigner

from config.version import RELEASE
from tools.build_online_release import (
    download_authenticated_repository,
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
        root.mkdir(parents=True, exist_ok=True)
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

    def test_republication_increments_tuf_metadata_and_preserves_other_channels(self) -> None:
        from tuf.api.metadata import Metadata, Timestamp

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            first = prepare_online_repository(
                artifacts=self._artifacts(root / "first-artifacts", "pilot"),
                site_root=root / "first-site",
                channel="pilot",
                rollout_percent=10,
                rollout_seed="stable-pilot-seed",
                mandatory=False,
                bootstrap_root=self.bootstrap,
                role_signers=self.role_signers,
                reference_time=self.now,
            )
            second = prepare_online_repository(
                artifacts=self._artifacts(root / "second-artifacts", "internal"),
                site_root=root / "second-site",
                channel="internal",
                rollout_percent=100,
                rollout_seed="stable-internal-seed",
                mandatory=False,
                bootstrap_root=self.bootstrap,
                role_signers=self.role_signers,
                previous_repository=first.repository_root,
                reference_time=self.now,
            )

            first_timestamp = Metadata.from_file(first.repository_root / "metadata" / "timestamp.json")
            second_timestamp = Metadata.from_file(second.repository_root / "metadata" / "timestamp.json")
            self.assertIsInstance(first_timestamp.signed, Timestamp)
            self.assertIsInstance(second_timestamp.signed, Timestamp)
            self.assertEqual(second_timestamp.signed.version, first_timestamp.signed.version + 1)
            self.assertTrue((second.repository_root / "targets/channels/pilot/manifest.json").is_file())
            self.assertTrue((second.repository_root / "targets/channels/internal/manifest.json").is_file())
            verify_repository(
                second.repository_root,
                bootstrap_root=self.bootstrap,
                channel="pilot",
                reference_time=self.now,
            )
            verify_repository(
                second.repository_root,
                bootstrap_root=self.bootstrap,
                channel="internal",
                reference_time=self.now,
            )

    def test_rollout_expansion_is_accepted_by_a_persistent_tuf_client(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            first_artifacts = root / "first-artifacts"
            first_artifacts.mkdir()
            first = prepare_online_repository(
                artifacts=self._artifacts(first_artifacts, "pilot"),
                site_root=root / "first-site",
                channel="pilot",
                rollout_percent=10,
                rollout_seed="same-cohort-seed",
                mandatory=False,
                bootstrap_root=self.bootstrap,
                role_signers=self.role_signers,
                reference_time=self.now,
            )
            client_cache = root / "persistent-client"
            first_manifest = verify_repository(
                first.repository_root,
                bootstrap_root=self.bootstrap,
                channel="pilot",
                reference_time=self.now,
                client_directory=client_cache,
            )
            second_artifacts = root / "second-artifacts"
            second_artifacts.mkdir()
            second = prepare_online_repository(
                artifacts=self._artifacts(second_artifacts, "pilot"),
                site_root=root / "second-site",
                channel="pilot",
                rollout_percent=100,
                rollout_seed="same-cohort-seed",
                mandatory=False,
                bootstrap_root=self.bootstrap,
                role_signers=self.role_signers,
                previous_repository=first.repository_root,
                reference_time=self.now,
            )
            second_manifest = verify_repository(
                second.repository_root,
                bootstrap_root=self.bootstrap,
                channel="pilot",
                reference_time=self.now,
                client_directory=client_cache,
            )
            self.assertEqual(first_manifest["rollout_percent"], 10)
            self.assertEqual(second_manifest["rollout_percent"], 100)
            self.assertEqual(second_manifest["rollout_seed"], "same-cohort-seed")

    def test_remote_repository_is_fully_authenticated_before_republication(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            published = prepare_online_repository(
                artifacts=self._artifacts(root / "artifacts", "pilot"),
                site_root=root / "source-site",
                channel="pilot",
                rollout_percent=10,
                rollout_seed="remote-seed",
                mandatory=False,
                bootstrap_root=self.bootstrap,
                role_signers=self.role_signers,
                reference_time=self.now,
            ).repository_root

            class Response:
                def __init__(self, payload: bytes, status: int = 200):
                    self.status_code = status
                    self.headers = {"Content-Length": str(len(payload))}
                    self._payload = payload

                def iter_content(self, chunk_size):
                    yield self._payload

                def close(self):
                    pass

            def getter(url, **kwargs):
                marker = "/updates/"
                relative = url.split(marker, 1)[1]
                return Response((published / relative).read_bytes())

            downloaded = download_authenticated_repository(
                "https://updates.example/updates",
                root / "downloaded",
                bootstrap_root=self.bootstrap,
                request_get=getter,
            )
            self.assertIsNotNone(downloaded)
            self.assertTrue((downloaded / "targets/channels/pilot/manifest.json").is_file())

            target = published / "targets/channels/pilot/manifest.json"
            original = target.read_bytes()
            target.write_bytes(original + b"tampered")
            with self.assertRaisesRegex(OnlineReleaseError, "autenticado|limite"):
                download_authenticated_repository(
                    "https://updates.example/updates",
                    root / "tampered-download",
                    bootstrap_root=self.bootstrap,
                    request_get=getter,
                )

    def test_expired_timestamp_can_be_refreshed_for_a_persistent_client(self) -> None:
        from datetime import timedelta

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            first = prepare_online_repository(
                artifacts=self._artifacts(root / "first-artifacts", "pilot"),
                site_root=root / "first-site",
                channel="pilot",
                rollout_percent=100,
                rollout_seed="refresh-seed",
                mandatory=False,
                bootstrap_root=self.bootstrap,
                role_signers=self.role_signers,
                reference_time=self.now,
            )
            cache = root / "client"
            verify_repository(
                first.repository_root,
                bootstrap_root=self.bootstrap,
                channel="pilot",
                reference_time=self.now,
                client_directory=cache,
            )
            refreshed_at = self.now + timedelta(days=3)
            refreshed = prepare_online_repository(
                artifacts=self._artifacts(root / "second-artifacts", "pilot"),
                site_root=root / "second-site",
                channel="pilot",
                rollout_percent=100,
                rollout_seed="refresh-seed",
                mandatory=False,
                bootstrap_root=self.bootstrap,
                role_signers=self.role_signers,
                previous_repository=first.repository_root,
                reference_time=refreshed_at,
            )
            verified = verify_repository(
                refreshed.repository_root,
                bootstrap_root=self.bootstrap,
                channel="pilot",
                reference_time=refreshed_at,
                client_directory=cache,
            )
            self.assertEqual(verified["rollout_seed"], "refresh-seed")

    def test_selected_channel_cannot_be_replaced_by_an_older_application_release(self) -> None:
        import json

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            first = prepare_online_repository(
                artifacts=self._artifacts(root / "first-artifacts", "pilot"),
                site_root=root / "first-site",
                channel="pilot",
                rollout_percent=100,
                rollout_seed="future-seed",
                mandatory=False,
                bootstrap_root=self.bootstrap,
                role_signers=self.role_signers,
                reference_time=self.now,
            )
            manifest = first.repository_root / "targets/channels/pilot/manifest.json"
            values = json.loads(manifest.read_text(encoding="utf-8"))
            values["sequence"] = RELEASE.sequence + 1
            values["version"] = "9.9.9"
            manifest.write_text(json.dumps(values), encoding="utf-8")

            with self.assertRaisesRegex(OnlineReleaseError, "retroceder"):
                prepare_online_repository(
                    artifacts=self._artifacts(root / "second-artifacts", "pilot"),
                    site_root=root / "second-site",
                    channel="pilot",
                    rollout_percent=100,
                    rollout_seed="older-seed",
                    mandatory=False,
                    bootstrap_root=self.bootstrap,
                    role_signers=self.role_signers,
                    previous_repository=first.repository_root,
                    reference_time=self.now,
                )

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

    def test_authenticode_gate_requires_a_trusted_timestamp_and_code_signing_policy(self) -> None:
        from tools.release_gate import _authenticode_valid

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            binary = Path(directory) / "signed.exe"
            binary.write_bytes(b"placeholder")
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                return types.SimpleNamespace(returncode=0, stdout="Valid", stderr="")

            self.assertFalse(_authenticode_valid(binary, runner=runner))
            command = " ".join(calls[0])
            self.assertIn("TimeStamperCertificate", command)
            self.assertIn("1.3.6.1.5.5.7.3.3", command)
            self.assertIn("1.3.6.1.5.5.7.3.8", command)

            def valid_runner(command, **kwargs):
                return types.SimpleNamespace(returncode=0, stdout="ValidTimestamped", stderr="")

            self.assertTrue(_authenticode_valid(binary, runner=valid_runner))

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
        self.assertEqual(command[command.index("--packId") + 1], "TrigoDeMinas.TrigoPDV.V2")
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
        self.assertIn("cron:", publication)
        self.assertRegex(publication, r"build:\s+if: github\.ref == 'refs/heads/main'")
        self.assertRegex(publication, r"build:[\s\S]+?environment:\s+name: github-pages")
        self.assertIn("github.ref }}' -ne 'refs/heads/main'", publication)
        self.assertIn("tools/stage_usb_installer.py", publication)
        self.assertIn("trigopdv-usb-package", publication)


if __name__ == "__main__":
    unittest.main()
