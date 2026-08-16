"""Background update checks that never block the checkout interface."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, RLock, Thread, current_thread
from typing import Callable

from .state import UpdateStateStore


class UpdateMonitor:
    """Run due update checks on one daemon thread.

    Network and repository failures are intentionally contained here: the PDV
    is offline-first and a failed check must not block local sales.  The
    detailed, redacted diagnostic is written by the coordinator event logger.
    """

    def __init__(
        self,
        *,
        state_store: UpdateStateStore,
        check_and_download: Callable[[], object],
        interval_hours: int,
        clock: Callable[[], datetime] | None = None,
        operation_lock=None,
    ) -> None:
        self.state_store = state_store
        self.check_and_download = check_and_download
        self.interval = timedelta(hours=max(1, int(interval_hours)))
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._operation_lock = operation_lock or RLock()
        self._stop = Event()
        self._lifecycle_lock = Lock()
        self._thread: Thread | None = None

    @staticmethod
    def _parse_utc(value: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return None
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    def run_due_check(self) -> bool:
        """Run one due check and return whether it completed successfully."""

        with self._operation_lock:
            return self._run_due_check_locked()

    def _run_due_check_locked(self) -> bool:
        """Keep the check and its timestamp in the coordinator state critical section."""

        now = self.clock().astimezone(timezone.utc)
        try:
            before = self.state_store.load()
        except Exception:
            return False
        last_check = self._parse_utc(before.last_check_at)
        if last_check is not None and now - last_check < self.interval:
            return False
        succeeded = True
        try:
            self.check_and_download()
        except Exception:
            succeeded = False
        try:
            latest = self.state_store.load()
            self.state_store.save(replace(latest, last_check_at=now.isoformat()))
        except Exception:
            return False
        return succeeded

    def _run(self) -> None:
        while not self._stop.is_set():
            self.run_due_check()
            if self._stop.wait(self.interval.total_seconds()):
                break

    def start(self) -> bool:
        """Start once and return immediately; repeated starts are ignored."""

        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = Thread(
                target=self._run,
                name="trigopdv-update-monitor",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
