from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.tuf_repository import (
    TufPublishError,
    create_root_metadata,
    publish_repository,
    verify_repository,
)


class TufRepositoryToolTestCase(unittest.TestCase):
    """Breaks caught: unsigned, stale or altered releases becoming installable."""

    def setUp(self) -> None:
        from securesystemslib.signer import CryptoSigner

        self.root_signers = [CryptoSigner.generate_ed25519() for _ in range(3)]
        self.role_signers = {
            role: CryptoSigner.generate_ed25519()
            for role in ("targets", "snapshot", "timestamp")
        }
        self.now = datetime(2026, 8, 16, 18, 0, tzinfo=timezone.utc)

    def _targets(self, root: Path, *, sequence: int = 3) -> Path:
        targets = root / "unsigned-targets"
        release = targets / "releases" / "1.1.1"
        channel = targets / "channels" / "pilot"
        release.mkdir(parents=True)
        channel.mkdir(parents=True)
        package = b"full velopack package"
        feed = b'{"Assets":[]}'
        (release / "TrigoPDV-1.1.1-full.nupkg").write_bytes(package)
        (release / "releases.pilot.json").write_bytes(feed)
        manifest = {
            "version": "1.1.1",
            "sequence": sequence,
            "schema_target": 9,
            "pack_id": "TrigoDeMinas.TrigoPDV",
            "channel": "pilot",
            "rollout_percent": 100,
            "rollout_seed": "public-seed-20260816",
            "mandatory": False,
            "artifacts": [
                {
                    "target": "releases/1.1.1/TrigoPDV-1.1.1-full.nupkg",
                    "length": len(package),
                    "sha256": hashlib.sha256(package).hexdigest(),
                },
                {
                    "target": "releases/1.1.1/releases.pilot.json",
                    "length": len(feed),
                    "sha256": hashlib.sha256(feed).hexdigest(),
                },
            ],
        }
        (channel / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True), encoding="utf-8"
        )
        return targets

    def _publish(self, root: Path, *, sequence: int = 3):
        bootstrap = create_root_metadata(
            self.root_signers,
            self.role_signers,
            version=1,
            expires=self.now + timedelta(days=365),
        )
        result = publish_repository(
            self._targets(root, sequence=sequence),
            root / "repository",
            bootstrap_root=bootstrap,
            role_signers=self.role_signers,
            metadata_version=sequence,
            targets_expires=self.now + timedelta(days=30),
            snapshot_expires=self.now + timedelta(days=7),
            timestamp_expires=self.now + timedelta(days=1),
            reference_time=self.now,
        )
        return bootstrap, result

    def test_real_tuf_client_authenticates_manifest_and_every_artifact(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            bootstrap, published = self._publish(root)

            verified = verify_repository(
                published.root,
                bootstrap_root=bootstrap,
                channel="pilot",
                reference_time=self.now,
            )

            self.assertEqual(verified["version"], "1.1.1")
            self.assertEqual(verified["sequence"], 3)
            self.assertEqual(len(verified["artifacts"]), 2)
            self.assertEqual(published.write_order[-1], "metadata/timestamp.json")

    def test_tampered_target_is_rejected_without_echoing_content(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            bootstrap, published = self._publish(root)
            target = published.root / "targets" / "releases" / "1.1.1" / "TrigoPDV-1.1.1-full.nupkg"
            target.write_bytes(b"private-content-must-not-appear")

            with self.assertRaisesRegex(TufPublishError, "repositório") as captured:
                verify_repository(
                    published.root,
                    bootstrap_root=bootstrap,
                    channel="pilot",
                    reference_time=self.now,
                )
            self.assertNotIn("private-content", str(captured.exception))

    def test_metadata_expiring_within_24_hours_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            bootstrap = create_root_metadata(
                self.root_signers,
                self.role_signers,
                version=1,
                expires=self.now + timedelta(days=365),
            )
            with self.assertRaisesRegex(TufPublishError, "expiração"):
                publish_repository(
                    self._targets(root),
                    root / "repository",
                    bootstrap_root=bootstrap,
                    role_signers=self.role_signers,
                    metadata_version=3,
                    targets_expires=self.now + timedelta(hours=23),
                    snapshot_expires=self.now + timedelta(days=7),
                    timestamp_expires=self.now + timedelta(days=1),
                    reference_time=self.now,
                )

    def test_existing_repository_rejects_same_or_lower_metadata_version(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            bootstrap, published = self._publish(root, sequence=3)
            second_targets = self._targets(root / "second", sequence=3)
            with self.assertRaisesRegex(TufPublishError, "sequência"):
                publish_repository(
                    second_targets,
                    root / "next",
                    bootstrap_root=bootstrap,
                    role_signers=self.role_signers,
                    metadata_version=3,
                    targets_expires=self.now + timedelta(days=30),
                    snapshot_expires=self.now + timedelta(days=7),
                    timestamp_expires=self.now + timedelta(days=1),
                    reference_time=self.now,
                    previous_repository=published.root,
                )

    def test_root_metadata_uses_two_of_three_offline_keys_and_distinct_online_keys(self) -> None:
        from tuf.api.metadata import Metadata, Root

        root_bytes = create_root_metadata(
            self.root_signers,
            self.role_signers,
            version=1,
            expires=self.now + timedelta(days=365),
        )
        metadata = Metadata.from_bytes(root_bytes)
        self.assertIsInstance(metadata.signed, Root)
        self.assertEqual(metadata.signed.roles["root"].threshold, 2)
        self.assertEqual(len(metadata.signed.roles["root"].keyids), 3)
        online_ids = {
            next(iter(metadata.signed.roles[role].keyids))
            for role in ("targets", "snapshot", "timestamp")
        }
        self.assertEqual(len(online_ids), 3)
        metadata.verify_delegate("root", metadata)


if __name__ == "__main__":
    unittest.main()
