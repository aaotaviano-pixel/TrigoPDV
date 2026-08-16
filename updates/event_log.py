"""Log técnico local com campos permitidos e sem dados comerciais/segredos."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re


_EVENTS = {
    "check_started", "check_finished", "download_started", "download_finished",
    "backup_finished", "apply_scheduled", "health_finished", "rollback_started",
    "rollback_finished", "update_failed",
}
_FIELDS = {"phase", "code", "version", "sequence", "channel", "outcome", "duration_ms"}
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9_.-]{0,80}$")


class UpdateEventLogger:
    def __init__(self, path: str | Path, *, max_bytes: int = 512_000, backups: int = 3):
        self.path = Path(path)
        self.max_bytes = max(4_096, int(max_bytes))
        self.backups = max(1, min(int(backups), 10))

    def write(self, event: str, **fields: object) -> None:
        if event not in _EVENTS:
            raise ValueError("Evento técnico de atualização inválido.")
        record: dict[str, object] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        for key, value in fields.items():
            if key not in _FIELDS:
                continue
            if isinstance(value, bool):
                record[key] = value
            elif isinstance(value, int):
                record[key] = value
            else:
                normalized = str(value or "")
                record[key] = normalized if _SAFE_TEXT.fullmatch(normalized) else "REDACTED"
        payload = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size + len(payload) > self.max_bytes:
            self._rotate()
        try:
            descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.write(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise RuntimeError("Não foi possível registrar o evento técnico de atualização.") from exc

    def _rotate(self) -> None:
        try:
            oldest = self.path.with_suffix(self.path.suffix + f".{self.backups}")
            oldest.unlink(missing_ok=True)
            for index in range(self.backups - 1, 0, -1):
                source = self.path.with_suffix(self.path.suffix + f".{index}")
                if source.exists():
                    os.replace(source, self.path.with_suffix(self.path.suffix + f".{index + 1}"))
            if self.path.exists():
                os.replace(self.path, self.path.with_suffix(self.path.suffix + ".1"))
        except OSError as exc:
            raise RuntimeError("Não foi possível rotacionar o log técnico de atualização.") from exc

