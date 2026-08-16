from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from desktop_controller import DesktopController
from updates.models import UpdatePhase
from updates.monitor import UpdateMonitor
from updates.state import UpdateState, UpdateStateStore


class UpdateMonitorTestCase(unittest.TestCase):
    def test_due_check_runs_without_blocking_start_and_persists_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = UpdateStateStore(Path(directory) / "state.json")
            started = threading.Event()
            release = threading.Event()

            def slow_check() -> None:
                started.set()
                release.wait(2)

            monitor = UpdateMonitor(
                state_store=store,
                check_and_download=slow_check,
                interval_hours=6,
            )
            before = time.monotonic()
            self.assertTrue(monitor.start())
            self.assertLess(time.monotonic() - before, 0.25)
            self.assertTrue(started.wait(1))
            release.set()
            monitor.stop(timeout=2)

            checked_at = datetime.fromisoformat(store.load().last_check_at)
            self.assertIsNotNone(checked_at.tzinfo)

    def test_recent_check_is_not_repeated_until_interval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
            store = UpdateStateStore(Path(directory) / "state.json")
            store.save(UpdateState(last_check_at=(now - timedelta(hours=5)).isoformat()))
            calls: list[str] = []
            monitor = UpdateMonitor(
                state_store=store,
                check_and_download=lambda: calls.append("check"),
                interval_hours=6,
                clock=lambda: now,
            )

            self.assertFalse(monitor.run_due_check())
            self.assertEqual(calls, [])

    def test_failure_is_offline_safe_and_does_not_destroy_downloaded_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
            store = UpdateStateStore(Path(directory) / "state.json")
            store.save(UpdateState(
                phase=UpdatePhase.DOWNLOADED,
                target_version="1.2.1",
                target_sequence=4,
                bundle_directory="C:/signed/bundle",
            ))

            def offline() -> None:
                raise OSError("private network details must not escape")

            monitor = UpdateMonitor(
                state_store=store,
                check_and_download=offline,
                interval_hours=6,
                clock=lambda: now,
            )

            self.assertFalse(monitor.run_due_check())
            state = store.load()
            self.assertEqual(state.phase, UpdatePhase.DOWNLOADED)
            self.assertEqual(state.target_sequence, 4)
            self.assertEqual(state.last_check_at, now.isoformat())
            self.assertNotIn("private", state.error_code)

    def test_controller_background_download_needs_no_logged_in_user(self) -> None:
        controller = object.__new__(DesktopController)
        controller.settings = SimpleNamespace(updates_enabled=True)
        controller.service = SimpleNamespace(
            installation_status=lambda: SimpleNamespace(
                installation_id="11111111-2222-3333-4444-555555555555"
            )
        )
        controller._pending_update_offer = None
        controller._update_lock = threading.RLock()
        offer = Mock(version="1.2.0")
        coordinator = Mock()
        coordinator.state_store.load.return_value = UpdateState()
        coordinator.check_now.return_value = offer
        coordinator.download.return_value = Path("C:/signed/bundle")

        with patch.object(controller, "_update_coordinator", return_value=coordinator):
            result = controller.background_check_for_update()

        self.assertTrue(result["available"])
        coordinator.check_now.assert_called_once()
        coordinator.download.assert_called_once_with(offer)
        self.assertIs(controller._pending_update_offer, offer)


if __name__ == "__main__":
    unittest.main()
