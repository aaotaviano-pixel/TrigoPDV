"""Politica persistente de bloqueios temporarios baseada somente em UTC."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def clock_utc(clock: Clock | None = None) -> datetime:
    value = (clock or utc_now)()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("O relogio de autenticacao deve retornar data/hora UTC com fuso.")
    return value.astimezone(timezone.utc)


def format_utc(value: datetime) -> str:
    aware = value.astimezone(timezone.utc)
    return aware.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("O estado de bloqueio possui data/hora sem fuso UTC.")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class RateLimitPolicy:
    threshold: int
    window: timedelta
    block: timedelta

    def __post_init__(self) -> None:
        if self.threshold <= 0 or self.window <= timedelta(0) or self.block <= timedelta(0):
            raise ValueError("A politica de bloqueio deve usar limites positivos.")


PASSWORD_POLICY = RateLimitPolicy(5, timedelta(minutes=15), timedelta(minutes=15))
RECOVERY_POLICY = RateLimitPolicy(5, timedelta(minutes=15), timedelta(minutes=30))


@dataclass(frozen=True)
class RateLimitFields:
    counter: str
    window_started_at: str
    blocked_until: str


PASSWORD_FIELDS = RateLimitFields(
    "tentativas_login_falhas", "login_falhas_janela_inicio", "login_bloqueado_ate"
)
RECOVERY_FIELDS = RateLimitFields(
    "recuperacao_falhas", "recuperacao_janela_inicio", "recuperacao_bloqueado_ate"
)


@dataclass(frozen=True)
class RateLimitState:
    failures: int = 0
    window_started_at: datetime | None = None
    blocked_until: datetime | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any], fields: RateLimitFields) -> "RateLimitState":
        failures = int(row[fields.counter] or 0)
        if failures < 0:
            raise ValueError("O contador de autenticacao nao pode ser negativo.")
        return cls(
            failures=failures,
            window_started_at=parse_utc(row[fields.window_started_at]),
            blocked_until=parse_utc(row[fields.blocked_until]),
        )

    def normalized(self, at: datetime, policy: RateLimitPolicy) -> "RateLimitState":
        if self.blocked_until is not None:
            if at < self.blocked_until:
                return self
            return RateLimitState()
        # Contadores de versões anteriores não possuíam início de janela. Eles
        # não provam que as falhas ocorreram nos últimos minutos e, portanto,
        # não podem ser somados a uma tentativa nova.
        if self.failures == 0 or self.window_started_at is None:
            return RateLimitState()
        if at >= self.window_started_at + policy.window:
            return RateLimitState()
        return self

    def is_blocked(self, at: datetime, policy: RateLimitPolicy) -> bool:
        state = self.normalized(at, policy)
        return state.blocked_until is not None and at < state.blocked_until

    def register_failure(self, at: datetime, policy: RateLimitPolicy) -> "RateLimitState":
        state = self.normalized(at, policy)
        if state.blocked_until is not None and at < state.blocked_until:
            return state
        started_at = state.window_started_at or at
        failures = state.failures + 1
        blocked_until = at + policy.block if failures >= policy.threshold else None
        return RateLimitState(failures, started_at, blocked_until)

    @staticmethod
    def cleared() -> "RateLimitState":
        return RateLimitState()


def persist_state(
    connection: sqlite3.Connection,
    user_id: int,
    fields: RateLimitFields,
    state: RateLimitState,
    *,
    updated_at: datetime,
) -> None:
    connection.execute(
        f"UPDATE usuarios SET {fields.counter} = ?, {fields.window_started_at} = ?, "
        f"{fields.blocked_until} = ?, atualizado_em = ? WHERE id = ?",
        (
            state.failures,
            format_utc(state.window_started_at) if state.window_started_at else None,
            format_utc(state.blocked_until) if state.blocked_until else None,
            format_utc(updated_at),
            user_id,
        ),
    )
