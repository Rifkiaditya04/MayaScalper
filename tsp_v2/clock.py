"""Authoritative broker-clock utilities for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .enums import ClockHealth
from .models import ClockState


LOCAL_BROKER_SKEW_WARNING_SECONDS = 60.0
LOCAL_BROKER_SKEW_SOFT_FAIL_SECONDS = 180.0
LOCAL_BROKER_SKEW_HARD_FAIL_SECONDS = 300.0
BACKWARD_JUMP_WARNING_SECONDS = 5.0
BACKWARD_JUMP_HARD_FAIL_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class ClockSample:
    broker_time_utc: datetime
    local_time_utc: datetime
    skew_seconds: float


def normalize_broker_time(
    *,
    broker_time: datetime,
    offset_hours: int = 0,
) -> datetime:
    """Normalize broker time into authoritative UTC."""
    if broker_time.tzinfo is None:
        normalized = broker_time.replace(tzinfo=timezone.utc)
    else:
        normalized = broker_time.astimezone(timezone.utc)
    if offset_hours:
        normalized = normalized - timedelta(hours=offset_hours)
    return normalized


def evaluate_clock_state(
    *,
    broker_time: datetime,
    local_time_utc: datetime,
    previous_broker_time_utc: datetime | None = None,
    broker_offset_hours: int = 0,
) -> ClockState:
    broker_time_utc = normalize_broker_time(
        broker_time=broker_time,
        offset_hours=broker_offset_hours,
    )
    local_utc = _ensure_utc(local_time_utc, field_name="local_time_utc")
    skew_seconds = abs((broker_time_utc - local_utc).total_seconds())

    backward_jump_seconds = 0.0
    if previous_broker_time_utc is not None:
        previous_utc = _ensure_utc(
            previous_broker_time_utc,
            field_name="previous_broker_time_utc",
        )
        delta_seconds = (broker_time_utc - previous_utc).total_seconds()
        if delta_seconds < 0.0:
            backward_jump_seconds = abs(delta_seconds)

    health = ClockHealth.OK
    diagnostic_flags: list[str] = []

    if skew_seconds > LOCAL_BROKER_SKEW_HARD_FAIL_SECONDS:
        health = ClockHealth.HARD_FAIL
        diagnostic_flags.append("local_broker_skew_hard_fail")
    elif skew_seconds > LOCAL_BROKER_SKEW_SOFT_FAIL_SECONDS:
        health = ClockHealth.SOFT_FAIL
        diagnostic_flags.append("local_broker_skew_soft_fail")
    elif skew_seconds > LOCAL_BROKER_SKEW_WARNING_SECONDS:
        health = ClockHealth.WARNING
        diagnostic_flags.append("local_broker_skew_warning")

    if backward_jump_seconds > BACKWARD_JUMP_HARD_FAIL_SECONDS:
        health = ClockHealth.HARD_FAIL
        diagnostic_flags.append("broker_backward_jump_hard_fail")
    elif backward_jump_seconds > BACKWARD_JUMP_WARNING_SECONDS:
        if health is ClockHealth.OK:
            health = ClockHealth.WARNING
        diagnostic_flags.append("broker_backward_jump_warning")

    return ClockState(
        broker_time_utc=broker_time_utc,
        local_time_utc=local_utc,
        skew_seconds=skew_seconds,
        health=health,
        backward_jump_seconds=backward_jump_seconds,
        diagnostic_flags=tuple(diagnostic_flags),
    )


def is_execution_blocked(clock_state: ClockState) -> bool:
    return clock_state.health in {ClockHealth.SOFT_FAIL, ClockHealth.HARD_FAIL}


def _ensure_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC datetime")
    return value.astimezone(timezone.utc)
