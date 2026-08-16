from __future__ import annotations

from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from config.settings import ConfigurationError, load_settings
from config.version import RELEASE
from updates.health import run_health_check
from updates.models import UpdateOffer, UpdatePhase, UpdatePolicy, cohort_eligible
from updates.state import UpdateState, UpdateStateError, UpdateStateStore
from updates.velopack_adapter import run_velopack_startup
from updates.event_log import UpdateEventLogger
from updates.repository import TufRepository, UpdateRepositoryError
from updates.coordinator import UpdateCoordinator, UpdateCoordinatorError, UpdateRestartScheduled
from db.database import Database
from tools.create_update_manifest import create_manifest


ROOT = Path(__file__).resolve().parent.parent


class UpdatePolicyTestCase(unittest.TestCase):
    def test_cohort_is_deterministic_and_bounded(self) -> None:
        installation = "11111111-2222-3333-4444-555555555555"
        self.assertEqual(
            cohort_eligible(installation, "signed-rollout", 37),
            cohort_eligible(installation, "signed-rollout", 37),
        )
        self.assertFalse(cohort_eligible(installation, "signed-rollout", 0))
        self.assertTrue(cohort_eligible(installation, "signed-rollout", 100))

    def test_update_settings_are_disabled_by_default_and_https_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text((ROOT / "config.ini.example").read_text(encoding="utf-8"), encoding="utf-8")
            settings = load_settings(path)
            self.assertFalse(settings.updates_enabled)
            self.assertEqual(settings.update_channel, "stable")
            self.assertEqual(settings.resource_directory, ROOT)
            content = path.read_text(encoding="utf-8").replace(
                "enabled = false\nchannel = stable\nbase_url =",
                "enabled = true\nchannel = stable\nbase_url = http://updates.example",
            )
            path.write_text(content, encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_settings(path)


class UpdateStateStoreTestCase(unittest.TestCase):
    def test_state_round_trip_and_monotonic_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = UpdateStateStore(Path(directory) / "update-state.json")
            original = UpdateState(
                phase=UpdatePhase.DOWNLOADED,
                current_version=RELEASE.version,
                current_sequence=RELEASE.sequence,
                target_version="1.2.0",
                target_sequence=3,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            store.save(original)
            self.assertEqual(store.load(), original)
            with self.assertRaises(UpdateStateError):
                store.save(UpdateState(current_version="1.0.0", current_sequence=1))
            self.assertEqual(store.load(), original)

    def test_corrupt_state_fails_closed_without_exposing_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "update-state.json"
            path.write_text('{"secret":"do-not-echo"', encoding="utf-8")
            with self.assertRaisesRegex(UpdateStateError, "estado local") as captured:
                UpdateStateStore(path).load()
            self.assertNotIn("do-not-echo", str(captured.exception))


class VelopackStartupTestCase(unittest.TestCase):
    def test_auto_apply_is_disabled_before_startup_processing(self) -> None:
        events: list[object] = []

        class FakeApp:
            def set_auto_apply_on_startup(self, enabled: bool):
                events.append(("auto", enabled))
                return self

            def run(self):
                events.append("run")

        self.assertTrue(run_velopack_startup(frozen=True, app_factory=FakeApp))
        self.assertEqual(events, [("auto", False), "run"])

    def test_main_has_no_top_level_business_import_before_velopack(self) -> None:
        import ast

        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        forbidden = {"config", "db", "desktop_controller", "services", "ui", "printing"}
        top_level = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level.append(node.module.split(".")[0])
        self.assertFalse(forbidden.intersection(top_level))


class UpdateEventLogTestCase(unittest.TestCase):
    def test_log_discards_unknown_fields_and_redacts_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "update.jsonl"
            logger = UpdateEventLogger(path, max_bytes=4096)
            logger.write(
                "update_failed", code="HEALTH_SCHEMA", phase="FAILED",
                password="never-log", version="C:/private/customer/1.2.0",
            )
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("password", record)
            self.assertEqual(record["version"], "REDACTED")
            self.assertEqual(record["code"], "HEALTH_SCHEMA")

    def test_release_manifest_contains_only_artifact_hashes_and_public_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "TrigoPDV-full.nupkg"
            artifact.write_bytes(b"package")
            manifest_path = create_manifest(
                [artifact], root / "repository", channel="internal",
                rollout_percent=10, rollout_seed="public-signed-seed", mandatory=False,
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["pack_id"], RELEASE.pack_id)
            self.assertEqual(manifest["artifacts"][0]["sha256"], hashlib.sha256(b"package").hexdigest())
            self.assertNotIn("token", json.dumps(manifest).lower())


class UpdateHealthTestCase(unittest.TestCase):
    def test_health_check_is_read_only_and_accepts_current_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pdv.sqlite3"
            database = Database(path)
            database.initialize()
            before = path.read_bytes()
            result = run_health_check(path, expected_schema=RELEASE.schema_target)
            self.assertTrue(result.healthy)
            self.assertEqual(path.read_bytes(), before)

    def test_offer_rejects_wrong_pack_schema_or_sequence(self) -> None:
        policy = UpdatePolicy(enabled=True, channel="stable", base_url="https://updates.example")
        valid = UpdateOffer(
            version="1.2.0", sequence=3, schema_target=RELEASE.schema_target,
            pack_id=RELEASE.pack_id, channel="stable", rollout_percent=100,
            rollout_seed="signed", manifest_target="bundle.json",
        )
        valid.validate(policy, current_sequence=RELEASE.sequence)
        for changes in (
            {"pack_id": "Other.App"}, {"schema_target": RELEASE.schema_target + 1},
            {"sequence": RELEASE.sequence}, {"channel": "beta"},
        ):
            values = valid.__dict__ | changes
            with self.assertRaises(ValueError):
                UpdateOffer(**values).validate(policy, current_sequence=RELEASE.sequence)


class TufRepositoryTestCase(unittest.TestCase):
    def test_signed_manifest_and_every_artifact_are_verified_by_tuf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = b"velopack package"
            manifest = json.dumps({
                "version": "1.2.0", "sequence": 3, "schema_target": RELEASE.schema_target,
                "pack_id": RELEASE.pack_id, "channel": "stable", "rollout_percent": 100,
                "rollout_seed": "signed-seed", "mandatory": False,
                "artifacts": [{
                    "target": "releases/TrigoPDV-1.2.0-full.nupkg",
                    "length": len(artifact), "sha256": hashlib.sha256(artifact).hexdigest(),
                }],
            }).encode("utf-8")
            payloads = {
                "channels/stable/manifest.json": manifest,
                "releases/TrigoPDV-1.2.0-full.nupkg": artifact,
            }
            constructor_calls = []

            class Info:
                def __init__(self, name: str):
                    self.length = len(payloads[name])
                    self.hashes = {"sha256": hashlib.sha256(payloads[name]).hexdigest()}

            class FakeUpdater:
                def __init__(self, **kwargs):
                    constructor_calls.append(kwargs)

                def get_targetinfo(self, name):
                    return Info(name) if name in payloads else None

                def download_target(self, info, filepath=None, target_base_url=None):
                    name = next(name for name, value in payloads.items() if len(value) == info.length and hashlib.sha256(value).hexdigest() == info.hashes["sha256"])
                    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
                    Path(filepath).write_bytes(payloads[name])
                    return str(filepath)

            repository = TufRepository(
                base_url="https://updates.example/", bootstrap_root=b'{"signed":"root"}',
                cache_directory=root / "cache", updater_factory=FakeUpdater,
            )
            offer = repository.check_offer("channels/stable/manifest.json")
            bundle = repository.download_bundle(offer)
            self.assertEqual(offer.sequence, 3)
            self.assertEqual((bundle / "TrigoPDV-1.2.0-full.nupkg").read_bytes(), artifact)
            self.assertTrue(constructor_calls)
            self.assertTrue(all(call["bootstrap"] == b'{"signed":"root"}' for call in constructor_calls))


class UpdateCoordinatorTestCase(unittest.TestCase):
    def test_backup_and_pending_state_precede_velopack_apply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "pdv.sqlite3"
            Database(database_path).initialize()
            bundle = root / "bundle"
            bundle.mkdir()
            events = []
            store = UpdateStateStore(root / "state.json")
            offer = UpdateOffer(
                version="1.2.0", sequence=3, schema_target=RELEASE.schema_target,
                pack_id=RELEASE.pack_id, channel="stable", rollout_percent=100,
                rollout_seed="seed", manifest_target="channels/stable/manifest.json",
            )

            class Adapter:
                def apply_local_bundle(self, directory, *, restart_args=None):
                    events.append(("apply", store.load().phase, Path(directory).is_dir()))

            coordinator = UpdateCoordinator(
                policy=UpdatePolicy(True, "stable", "https://updates.example"),
                state_store=store, database_path=database_path, backup_directory=root / "backups",
                adapter=Adapter(), repository=None,
            )
            coordinator.prepare_apply(offer, bundle, safe_to_apply=lambda: True)
            state = store.load()
            self.assertEqual(events, [("apply", UpdatePhase.APPLY_PENDING, True)])
            self.assertTrue(Path(state.database_backup).is_file())

    def test_health_failure_blocks_startup_without_rollback_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "broken.sqlite3"
            database.write_bytes(b"broken")
            store = UpdateStateStore(root / "state.json")
            store.save(UpdateState(
                phase=UpdatePhase.APPLY_PENDING, current_version="1.0.0", current_sequence=1,
                target_version=RELEASE.version, target_sequence=RELEASE.sequence,
                target_schema=RELEASE.schema_target,
            ))
            coordinator = UpdateCoordinator(
                policy=UpdatePolicy(), state_store=store, database_path=database,
                backup_directory=root / "backups", adapter=Mock(), repository=None,
            )
            with self.assertRaises(UpdateCoordinatorError):
                coordinator.resume_pending_update()
            self.assertEqual(store.load().phase, UpdatePhase.FAILED)

    def test_download_failure_never_blocks_commercial_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = UpdateStateStore(root / "state.json")
            store.save(UpdateState(phase=UpdatePhase.FAILED, error_code="DOWNLOAD"))
            coordinator = UpdateCoordinator(
                policy=UpdatePolicy(), state_store=store, database_path=root / "pdv.sqlite3",
                backup_directory=root / "backups", adapter=Mock(), repository=None,
            )
            self.assertFalse(coordinator.resume_pending_update())

    def test_pending_apply_on_old_binary_schedules_exit_instead_of_opening_pdv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            bundle.mkdir()
            store = UpdateStateStore(root / "state.json")
            store.save(UpdateState(
                phase=UpdatePhase.APPLY_PENDING,
                current_version=RELEASE.version,
                current_sequence=RELEASE.sequence,
                target_version="1.2.0",
                target_sequence=RELEASE.sequence + 1,
                target_schema=RELEASE.schema_target,
                bundle_directory=str(bundle),
                attempts=1,
            ))
            adapter = Mock()
            coordinator = UpdateCoordinator(
                policy=UpdatePolicy(), state_store=store, database_path=root / "pdv.sqlite3",
                backup_directory=root / "backups", adapter=adapter, repository=None,
            )
            with self.assertRaises(UpdateRestartScheduled):
                coordinator.resume_pending_update()
            adapter.apply_local_bundle.assert_called_once_with(str(bundle))


if __name__ == "__main__":
    unittest.main()
