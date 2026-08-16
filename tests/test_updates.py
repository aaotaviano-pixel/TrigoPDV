from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from config.settings import ConfigurationError, load_settings
from config.version import RELEASE
from updates.health import run_health_check
from updates.models import TrustedArtifact, UpdateOffer, UpdatePhase, UpdatePolicy, cohort_eligible
from updates.state import UpdateState, UpdateStateError, UpdateStateStore
from updates.startup import startup_preflight
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

    def test_packaged_defaults_enable_authenticated_pilot_over_https(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text((ROOT / "config.ini.example").read_text(encoding="utf-8"), encoding="utf-8")
            settings = load_settings(path)
            self.assertTrue(settings.updates_enabled)
            self.assertEqual(settings.update_channel, "pilot")
            self.assertEqual(
                settings.update_base_url,
                "https://aaotaviano-pixel.github.io/TrigoPDV/updates",
            )
            self.assertEqual(settings.resource_directory, ROOT)
            content = path.read_text(encoding="utf-8").replace(
                "base_url = https://aaotaviano-pixel.github.io/TrigoPDV/updates",
                "base_url = http://updates.example",
            )
            path.write_text(content, encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_settings(path)

    def test_any_persisted_disabled_configuration_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            content = (ROOT / "config.ini.example").read_text(encoding="utf-8")
            legacy = content.replace("enabled = true", "enabled = false").replace(
                "channel = pilot", "channel = stable"
            ).replace(
                "base_url = https://aaotaviano-pixel.github.io/TrigoPDV/updates",
                "base_url =",
            )
            path.write_text(legacy, encoding="utf-8")
            migrated = load_settings(path)
            self.assertFalse(migrated.updates_enabled)
            self.assertEqual(migrated.update_channel, "stable")
            self.assertEqual(migrated.update_base_url, "")

            explicit = legacy.replace(
                "base_url =",
                "base_url = https://updates.example.invalid/disabled",
            )
            path.write_text(explicit, encoding="utf-8")
            disabled = load_settings(path)
            self.assertFalse(disabled.updates_enabled)
            self.assertEqual(disabled.update_channel, "stable")


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

    def test_offer_accepts_future_additive_schema_but_rejects_rollback_identity_or_sequence(self) -> None:
        policy = UpdatePolicy(enabled=True, channel="stable", base_url="https://updates.example")
        valid = UpdateOffer(
            version="1.2.1", sequence=RELEASE.sequence + 1, schema_target=RELEASE.schema_target,
            pack_id=RELEASE.pack_id, channel="stable", rollout_percent=100,
            rollout_seed="signed", manifest_target="bundle.json",
        )
        valid.validate(policy, current_sequence=RELEASE.sequence)
        UpdateOffer(**(valid.__dict__ | {"schema_target": RELEASE.schema_target + 1})).validate(
            policy, current_sequence=RELEASE.sequence
        )
        for changes in (
            {"pack_id": "Other.App"}, {"schema_target": RELEASE.schema_target - 1},
            {"sequence": RELEASE.sequence}, {"channel": "beta"},
        ):
            values = valid.__dict__ | changes
            with self.assertRaises(ValueError):
                UpdateOffer(**values).validate(policy, current_sequence=RELEASE.sequence)


class TufRepositoryTestCase(unittest.TestCase):
    def test_real_client_does_not_require_windows_symlink_privilege(self) -> None:
        from datetime import timedelta
        from securesystemslib.signer import CryptoSigner
        from tools.tuf_repository import create_root_metadata

        now = datetime.now(timezone.utc)
        root_signers = [CryptoSigner.generate_ed25519() for _ in range(3)]
        role_signers = {
            role: CryptoSigner.generate_ed25519()
            for role in ("targets", "snapshot", "timestamp")
        }
        bootstrap = create_root_metadata(
            root_signers,
            role_signers,
            version=1,
            expires=now + timedelta(days=365),
        )
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            cache = Path(directory) / "cache"
            repository = TufRepository(
                base_url="https://updates.example/",
                bootstrap_root=bootstrap,
                cache_directory=cache,
            )
            denied = OSError(1314, "symlink privilege denied")
            with patch("os.symlink", side_effect=denied):
                updater = repository._new_updater()

            self.assertIsNotNone(updater)
            root_cache = cache / "metadata" / "root.json"
            self.assertTrue(root_cache.is_file())
            self.assertFalse(root_cache.is_symlink())

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
    def test_new_binary_migrates_pending_database_before_health_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.ini"
            config_path.write_text(
                (ROOT / "config.ini.example").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            loaded = load_settings(config_path)
            database_path = root / "data" / "pdv.sqlite3"
            Database(database_path).initialize()
            with Database(database_path).transaction(write=True) as connection:
                connection.execute(
                    "UPDATE schema_meta SET valor = '8' WHERE chave = 'schema_version'"
                )
            state_path = root / "data" / "updates" / "state.json"
            UpdateStateStore(state_path).save(UpdateState(
                phase=UpdatePhase.APPLY_PENDING,
                current_version="1.1.0",
                current_sequence=2,
                target_version=RELEASE.version,
                target_sequence=RELEASE.sequence,
                target_schema=RELEASE.schema_target,
                attempts=1,
            ))
            settings = replace(
                loaded,
                database_path=database_path,
                backup_path=root / "backups",
                update_state_path=state_path,
                updates_enabled=False,
                update_base_url="",
                resource_directory=ROOT,
            )

            startup_preflight(settings)

            with Database(database_path).transaction() as connection:
                version = int(connection.execute(
                    "SELECT valor FROM schema_meta WHERE chave = 'schema_version'"
                ).fetchone()[0])
            self.assertEqual(version, RELEASE.schema_target)
            self.assertEqual(UpdateStateStore(state_path).load().phase, UpdatePhase.IDLE)

    def test_startup_recovers_interrupted_download_prepare_and_manual_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def make(name: str):
                store = UpdateStateStore(root / f"{name}.json")
                coordinator = UpdateCoordinator(
                    policy=UpdatePolicy(),
                    state_store=store,
                    database_path=root / "pdv.sqlite3",
                    backup_directory=root / "backups",
                    adapter=Mock(),
                )
                return store, coordinator

            store, coordinator = make("downloading")
            store.save(UpdateState(
                phase=UpdatePhase.DOWNLOADING,
                current_version=RELEASE.version,
                current_sequence=RELEASE.sequence,
                target_version="1.2.1",
                target_sequence=RELEASE.sequence + 1,
            ))
            self.assertFalse(coordinator.resume_pending_update())
            interrupted = store.load()
            self.assertEqual(interrupted.phase, UpdatePhase.FAILED)
            self.assertEqual(interrupted.error_code, "DOWNLOAD_INTERRUPTED")

            store, coordinator = make("preparing")
            store.save(UpdateState(
                phase=UpdatePhase.PREPARING,
                current_version=RELEASE.version,
                current_sequence=RELEASE.sequence,
                target_version="1.2.1",
                target_sequence=RELEASE.sequence + 1,
                target_schema=RELEASE.schema_target,
                bundle_directory="C:/authenticated/bundle",
                offer_json="signed-offer-placeholder",
            ))
            self.assertFalse(coordinator.resume_pending_update())
            self.assertEqual(store.load().phase, UpdatePhase.DOWNLOADED)

            store, coordinator = make("bootstrap")
            store.save(UpdateState(
                phase=UpdatePhase.FAILED,
                current_version="1.1.0",
                current_sequence=2,
                target_version=RELEASE.version,
                target_sequence=RELEASE.sequence,
                error_code="DOWNLOAD",
            ))
            self.assertFalse(coordinator.resume_pending_update())
            reconciled = store.load()
            self.assertEqual(reconciled.phase, UpdatePhase.IDLE)
            self.assertEqual(reconciled.current_version, RELEASE.version)
            self.assertEqual(reconciled.current_sequence, RELEASE.sequence)

            store, coordinator = make("future")
            store.save(UpdateState(
                current_version="9.9.9",
                current_sequence=RELEASE.sequence + 1,
            ))
            with self.assertRaisesRegex(UpdateCoordinatorError, "retroceder"):
                coordinator.resume_pending_update()

    def test_bootstrap_upgrade_downloads_backs_up_and_reaches_healthy_current_state(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            database_path = root / "pdv.sqlite3"
            Database(database_path).initialize()
            package = b"authenticated full velopack package"
            feed = b'{"Assets":[]}'
            artifacts = (
                TrustedArtifact(
                    "releases/TrigoPDV-full.nupkg",
                    len(package),
                    hashlib.sha256(package).hexdigest(),
                ),
                TrustedArtifact(
                    "releases/releases.pilot.json",
                    len(feed),
                    hashlib.sha256(feed).hexdigest(),
                ),
            )
            offer = UpdateOffer(
                version=RELEASE.version,
                sequence=RELEASE.sequence,
                schema_target=RELEASE.schema_target,
                pack_id=RELEASE.pack_id,
                channel="pilot",
                rollout_percent=100,
                rollout_seed="bootstrap-e2e",
                manifest_target="channels/pilot/manifest.json",
                artifacts=artifacts,
            )
            bundle = root / "bundle"

            class Repository:
                def download_bundle(self, received):
                    self.received = received
                    bundle.mkdir()
                    (bundle / "TrigoPDV-full.nupkg").write_bytes(package)
                    (bundle / "releases.pilot.json").write_bytes(feed)
                    return bundle

            class Adapter:
                def __init__(self):
                    self.calls = []

                def apply_local_bundle(self, directory, *, restart_args=None):
                    self.calls.append(Path(directory).resolve())

            store = UpdateStateStore(root / "state.json")
            store.save(UpdateState(current_version="1.1.0", current_sequence=2))
            adapter = Adapter()
            coordinator = UpdateCoordinator(
                policy=UpdatePolicy(True, "pilot", "https://updates.example"),
                state_store=store,
                database_path=database_path,
                backup_directory=root / "backups",
                adapter=adapter,
                repository=Repository(),
            )

            downloaded = coordinator.download(offer)
            coordinator.prepare_apply(offer, downloaded, safe_to_apply=lambda: True)
            pending = store.load()
            self.assertEqual(pending.phase, UpdatePhase.APPLY_PENDING)
            self.assertTrue(Path(pending.database_backup).is_file())
            self.assertEqual(adapter.calls, [bundle.resolve()])

            self.assertTrue(coordinator.resume_pending_update())
            final = store.load()
            self.assertEqual(final.phase, UpdatePhase.IDLE)
            self.assertEqual(final.current_version, RELEASE.version)
            self.assertEqual(final.current_sequence, RELEASE.sequence)

    def test_published_current_release_is_treated_as_up_to_date_not_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            offer = UpdateOffer(
                version=RELEASE.version,
                sequence=RELEASE.sequence,
                schema_target=RELEASE.schema_target,
                pack_id=RELEASE.pack_id,
                channel="pilot",
                rollout_percent=100,
                rollout_seed="signed-current-release",
                manifest_target="channels/pilot/manifest.json",
            )
            repository = Mock()
            repository.check_offer.return_value = offer
            coordinator = UpdateCoordinator(
                policy=UpdatePolicy(True, "pilot", "https://updates.example"),
                state_store=UpdateStateStore(root / "state.json"),
                database_path=root / "pdv.sqlite3",
                backup_directory=root / "backups",
                adapter=Mock(),
                repository=repository,
            )

            self.assertIsNone(coordinator.check_now("installation-id"))
            self.assertEqual(coordinator.state_store.load().phase, UpdatePhase.IDLE)

            repository.check_offer.return_value = UpdateOffer(
                **(offer.__dict__ | {"version": "9.9.9"})
            )
            with self.assertRaises(UpdateCoordinatorError):
                coordinator.check_now("installation-id")

    def test_downloaded_offer_survives_process_restart_and_rechecks_bundle(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            package = b"signed package"
            feed = b'{"Assets":[]}'
            artifacts = (
                TrustedArtifact("releases/app-full.nupkg", len(package), hashlib.sha256(package).hexdigest()),
                TrustedArtifact("releases/releases.pilot.json", len(feed), hashlib.sha256(feed).hexdigest()),
            )
            offer = UpdateOffer(
                version="1.2.0",
                sequence=RELEASE.sequence + 1,
                schema_target=RELEASE.schema_target,
                pack_id=RELEASE.pack_id,
                channel="pilot",
                rollout_percent=100,
                rollout_seed="signed-seed",
                manifest_target="channels/pilot/manifest.json",
                artifacts=artifacts,
            )
            bundle = root / "bundle"

            class Repository:
                def download_bundle(self, received):
                    self.assert_offer = received
                    bundle.mkdir()
                    (bundle / "app-full.nupkg").write_bytes(package)
                    (bundle / "releases.pilot.json").write_bytes(feed)
                    return bundle

            policy = UpdatePolicy(True, "pilot", "https://updates.example")
            store = UpdateStateStore(root / "state.json")
            first = UpdateCoordinator(
                policy=policy,
                state_store=store,
                database_path=root / "pdv.sqlite3",
                backup_directory=root / "backups",
                adapter=Mock(),
                repository=Repository(),
            )
            first.download(offer)

            restarted = UpdateCoordinator(
                policy=policy,
                state_store=UpdateStateStore(root / "state.json"),
                database_path=root / "pdv.sqlite3",
                backup_directory=root / "backups",
                adapter=Mock(),
                repository=None,
            )
            restored = restarted.restore_downloaded_offer()

            self.assertEqual(restored, offer)
            (bundle / "app-full.nupkg").write_bytes(b"tampered")
            with self.assertRaisesRegex(UpdateCoordinatorError, "pacote baixado"):
                restarted.restore_downloaded_offer()

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
                version="1.2.1", sequence=RELEASE.sequence + 1, schema_target=RELEASE.schema_target,
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
                target_version="1.2.1",
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
