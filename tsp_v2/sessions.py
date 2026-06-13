"""Deterministic session classification for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .enums import SessionName


@dataclass(frozen=True, slots=True)
class SessionWindow:
    name: SessionName
    start_hour_utc: int
    end_hour_utc_exclusive: int


SESSION_WINDOWS: tuple[SessionWindow, ...] = (
    SessionWindow(SessionName.ASIA, 0, 7),
    SessionWindow(SessionName.LONDON, 7, 12),
    SessionWindow(SessionName.LONDON_NY, 12, 16),
    SessionWindow(SessionName.EARLY_NY, 16, 19),
    SessionWindow(SessionName.LATE_NY, 19, 22),
)


def classify_session(now_utc: datetime) -> SessionName:
    broker_utc = _ensure_utc(now_utc)
    if broker_utc.weekday() >= 5:
        return SessionName.DEAD

    hour = broker_utc.hour
    for window in SESSION_WINDOWS:
        if window.start_hour_utc <= hour < window.end_hour_utc_exclusive:
            return window.name
    return SessionName.DEAD


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware UTC datetime")
    return value.astimezone(timezone.utc)
