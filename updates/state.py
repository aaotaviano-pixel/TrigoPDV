from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from config.version import RELEASE
from .models import UpdatePhase


class UpdateStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateState:
    phase: UpdatePhase = UpdatePhase.IDLE
    current_version: str = RELEASE.version
    current_sequence: int = RELEASE.sequence
    target_version: str = ""
    target_sequence: int = 0
    target_schema: int = 0
    bundle_directory: str = ""
    database_backup: str = ""
    offer_json: str = ""
    last_check_at: str = ""
    attempts: int = 0
    error_code: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, values: dict) -> "UpdateState":
        allowed = {field.name for field in __import__("dataclasses").fields(cls)}
        if set(values) - allowed:
            raise ValueError("unknown fields")
        values = dict(values)
        values["phase"] = UpdatePhase(values.get("phase", UpdatePhase.IDLE))
        for name, limit in {
            "current_version": 64,
            "target_version": 64,
            "bundle_directory": 4096,
            "database_backup": 4096,
            "offer_json": 65536,
            "last_check_at": 64,
            "error_code": 128,
            "updated_at": 64,
        }.items():
            value = values.get(name, "")
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError(f"invalid {name}")
        return cls(**values)

    def as_dict(self) -> dict:
        values = asdict(self)
        values["phase"] = self.phase.value
        return values


class UpdateStateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> UpdateState:
        if not self.path.exists():
            return UpdateState()
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(values, dict):
                raise ValueError("not object")
            return UpdateState.from_dict(values)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise UpdateStateError("Não foi possível validar o estado local de atualização.") from exc

    def save(self, state: UpdateState) -> None:
        current = self.load() if self.path.exists() else None
        if current is not None and int(state.current_sequence) < int(current.current_sequence):
            raise UpdateStateError("A sequência local de atualização não pode retroceder.")
        normalized = state
        if not state.updated_at:
            normalized = UpdateState(**(state.__dict__ | {"updated_at": datetime.now(timezone.utc).isoformat()}))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(normalized.as_dict(), handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise UpdateStateError("Não foi possível salvar o estado local de atualização.") from exc
        finally:
            temporary.unlink(missing_ok=True)
