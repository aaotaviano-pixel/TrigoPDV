"""Small deterministic state machine for keyboard/HID barcode readers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class ScanTicket:
    sequence: int
    code: str
    generation: int


class ScanQueue:
    """Serialize scans and invalidate results that belong to an old sale/view."""

    def __init__(self, *, max_pending: int = 100) -> None:
        self._lock = Lock()
        self._pending: deque[ScanTicket] = deque()
        self._active: ScanTicket | None = None
        self._sequence = 0
        self._generation = 0
        self._max_pending = max(1, int(max_pending))

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def has_pending(self) -> bool:
        with self._lock:
            return self._active is not None or bool(self._pending)

    def enqueue(self, code: str) -> ScanTicket:
        normalized = str(code or "").strip()
        if not normalized:
            raise ValueError("Informe o código lido.")
        with self._lock:
            if len(self._pending) >= self._max_pending:
                raise OverflowError("Muitos códigos aguardando na fila.")
            self._sequence += 1
            ticket = ScanTicket(self._sequence, normalized, self._generation)
            self._pending.append(ticket)
            return ticket

    def take_next(self) -> ScanTicket | None:
        with self._lock:
            if self._active is not None or not self._pending:
                return None
            self._active = self._pending.popleft()
            return self._active

    def finish(self, ticket: ScanTicket) -> bool:
        with self._lock:
            if self._active != ticket:
                return False
            self._active = None
            return ticket.generation == self._generation

    def advance_generation(self) -> int:
        with self._lock:
            self._generation += 1
            self._pending.clear()
            self._active = None
            return self._generation

