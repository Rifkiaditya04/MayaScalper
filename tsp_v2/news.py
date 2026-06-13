"""News provider composition and governance for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from .config import AppConfig, NewsConfig
from .config_schema import ConfigValidationError
from .enums import NewsProviderMode, NewsProviderState, RuntimeMode
from .models import NewsSnapshot


WARNING_THRESHOLD_MINUTES = 15
SOFT_FAIL_THRESHOLD_MINUTES = 30
HARD_FAIL_THRESHOLD_MINUTES = 60


@dataclass(frozen=True, slots=True)
class NewsEvent:
    event_id: str
    title: str
    symbol: str
    impact: str
    starts_at_utc: datetime
    ends_at_utc: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "symbol": self.symbol,
            "impact": self.impact,
            "starts_at_utc": self.starts_at_utc.isoformat(),
            "ends_at_utc": self.ends_at_utc.isoformat() if self.ends_at_utc else None,
        }


@dataclass(frozen=True, slots=True)
class ProviderSnapshot:
    provider_mode: NewsProviderMode
    provider_state: NewsProviderState
    snapshot_generated_at_utc: datetime | None
    relevant_events: tuple[NewsEvent, ...]
    lockout_active: bool
    next_relevant_event_utc: datetime | None


class StaticFileNewsProvider:
    def __init__(self, *, source_path: Path, news_lockout_minutes: int) -> None:
        self._source_path = source_path
        self._news_lockout_minutes = news_lockout_minutes

    def snapshot(self, *, at_utc: datetime, symbol: str) -> ProviderSnapshot:
        return _load_snapshot_from_path(
            mode=NewsProviderMode.STATIC_FILE,
            source_path=self._source_path,
            at_utc=at_utc,
            symbol=symbol,
            news_lockout_minutes=self._news_lockout_minutes,
        )


class CalendarSnapshotNewsProvider:
    def __init__(self, *, source_path: Path, news_lockout_minutes: int) -> None:
        self._source_path = source_path
        self._news_lockout_minutes = news_lockout_minutes

    def snapshot(self, *, at_utc: datetime, symbol: str) -> ProviderSnapshot:
        return _load_snapshot_from_path(
            mode=NewsProviderMode.CALENDAR_SNAPSHOT,
            source_path=self._source_path,
            at_utc=at_utc,
            symbol=symbol,
            news_lockout_minutes=self._news_lockout_minutes,
        )


class DisabledDiagnosticNewsProvider:
    def snapshot(self, *, at_utc: datetime, symbol: str) -> ProviderSnapshot:
        del at_utc, symbol
        return ProviderSnapshot(
            provider_mode=NewsProviderMode.DISABLED_DIAGNOSTIC_ONLY,
            provider_state=NewsProviderState.DISABLED,
            snapshot_generated_at_utc=None,
            relevant_events=(),
            lockout_active=False,
            next_relevant_event_utc=None,
        )


def build_news_snapshot(
    *,
    cycle_time_utc: datetime,
    config: AppConfig,
    symbol: str,
) -> NewsSnapshot:
    cycle_utc = _ensure_utc(cycle_time_utc, field_name="cycle_time_utc")
    provider = build_news_provider(config=config.news, news_lockout_minutes=config.regime.news_lockout_minutes)
    provider_snapshot = provider.snapshot(at_utc=cycle_utc, symbol=symbol)
    _enforce_news_governance(
        provider_snapshot=provider_snapshot,
        mode=config.bot.mode,
        symbol=symbol,
    )
    return NewsSnapshot(
        provider_mode=provider_snapshot.provider_mode,
        provider_state=provider_snapshot.provider_state,
        snapshot_generated_at_utc=provider_snapshot.snapshot_generated_at_utc,
        lockout_active=provider_snapshot.lockout_active,
        next_relevant_event_utc=provider_snapshot.next_relevant_event_utc,
        relevant_events=tuple(event.to_dict() for event in provider_snapshot.relevant_events),
    )


def build_news_provider(*, config: NewsConfig, news_lockout_minutes: int):
    if config.provider_mode is NewsProviderMode.STATIC_FILE:
        if config.source_path is None:
            raise ConfigValidationError("news.source_path is required for STATIC_FILE mode")
        return StaticFileNewsProvider(
            source_path=config.source_path,
            news_lockout_minutes=news_lockout_minutes,
        )
    if config.provider_mode is NewsProviderMode.CALENDAR_SNAPSHOT:
        if config.source_path is None:
            raise ConfigValidationError("news.source_path is required for CALENDAR_SNAPSHOT mode")
        return CalendarSnapshotNewsProvider(
            source_path=config.source_path,
            news_lockout_minutes=news_lockout_minutes,
        )
    if config.provider_mode is NewsProviderMode.DISABLED_DIAGNOSTIC_ONLY:
        return DisabledDiagnosticNewsProvider()
    raise ConfigValidationError(f"Unsupported news provider mode: {config.provider_mode}")


def evaluate_news_provider_state(
    *,
    snapshot_generated_at_utc: datetime | None,
    at_utc: datetime,
) -> NewsProviderState:
    if snapshot_generated_at_utc is None:
        return NewsProviderState.UNAVAILABLE
    age_seconds = max(0.0, (at_utc - snapshot_generated_at_utc).total_seconds())
    if age_seconds > HARD_FAIL_THRESHOLD_MINUTES * 60:
        return NewsProviderState.UNAVAILABLE
    if age_seconds > SOFT_FAIL_THRESHOLD_MINUTES * 60:
        return NewsProviderState.STALE
    if age_seconds > WARNING_THRESHOLD_MINUTES * 60:
        return NewsProviderState.STALE
    return NewsProviderState.READY


def _load_snapshot_from_path(
    *,
    mode: NewsProviderMode,
    source_path: Path,
    at_utc: datetime,
    symbol: str,
    news_lockout_minutes: int,
) -> ProviderSnapshot:
    if not source_path.exists():
        return ProviderSnapshot(
            provider_mode=mode,
            provider_state=NewsProviderState.UNAVAILABLE,
            snapshot_generated_at_utc=None,
            relevant_events=(),
            lockout_active=False,
            next_relevant_event_utc=None,
        )

    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigValidationError(f"Invalid news JSON at {source_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ConfigValidationError(f"News snapshot at {source_path} must be a JSON object")

    generated_raw = payload.get("generated_at_utc")
    if generated_raw is None:
        raise ConfigValidationError(f"News snapshot at {source_path} is missing generated_at_utc")
    snapshot_generated_at_utc = _parse_utc_datetime(generated_raw, field_name="generated_at_utc")
    provider_state = evaluate_news_provider_state(
        snapshot_generated_at_utc=snapshot_generated_at_utc,
        at_utc=at_utc,
    )

    events_raw = payload.get("events")
    if not isinstance(events_raw, list):
        raise ConfigValidationError(f"News snapshot at {source_path} must contain an events list")

    relevant_events = tuple(
        event
        for event in (_parse_news_event(raw_item) for raw_item in events_raw)
        if _is_symbol_relevant(event_symbol=event.symbol, target_symbol=symbol)
    )
    future_events = tuple(
        event for event in relevant_events if event.starts_at_utc >= at_utc
    )
    next_relevant_event_utc = min(
        (event.starts_at_utc for event in future_events),
        default=None,
    )
    lockout_active = _compute_lockout_active(
        at_utc=at_utc,
        relevant_events=relevant_events,
        next_relevant_event_utc=next_relevant_event_utc,
        news_lockout_minutes=news_lockout_minutes,
    )

    return ProviderSnapshot(
        provider_mode=mode,
        provider_state=provider_state,
        snapshot_generated_at_utc=snapshot_generated_at_utc,
        relevant_events=relevant_events,
        lockout_active=lockout_active,
        next_relevant_event_utc=next_relevant_event_utc,
    )


def _compute_lockout_active(
    *,
    at_utc: datetime,
    relevant_events: tuple[NewsEvent, ...],
    next_relevant_event_utc: datetime | None,
    news_lockout_minutes: int,
) -> bool:
    lockout_horizon = timedelta(minutes=news_lockout_minutes)
    for event in relevant_events:
        window_start = event.starts_at_utc - lockout_horizon
        window_end = (event.ends_at_utc or event.starts_at_utc) + lockout_horizon
        if window_start <= at_utc <= window_end:
            return True
    if next_relevant_event_utc is None:
        return False
    return next_relevant_event_utc - at_utc <= lockout_horizon


def _parse_news_event(raw_event: Any) -> NewsEvent:
    if not isinstance(raw_event, dict):
        raise ConfigValidationError("Each news event must be a mapping")
    required_keys = {"event_id", "title", "symbol", "impact", "starts_at_utc"}
    missing = sorted(required_keys - set(raw_event))
    if missing:
        raise ConfigValidationError(f"News event missing keys: {', '.join(missing)}")
    ends_at = raw_event.get("ends_at_utc")
    return NewsEvent(
        event_id=_require_non_empty_string(raw_event["event_id"], field_name="event_id"),
        title=_require_non_empty_string(raw_event["title"], field_name="title"),
        symbol=_require_non_empty_string(raw_event["symbol"], field_name="symbol").upper(),
        impact=_require_non_empty_string(raw_event["impact"], field_name="impact").upper(),
        starts_at_utc=_parse_utc_datetime(raw_event["starts_at_utc"], field_name="starts_at_utc"),
        ends_at_utc=(
            _parse_utc_datetime(ends_at, field_name="ends_at_utc")
            if ends_at is not None
            else None
        ),
    )


def _is_symbol_relevant(*, event_symbol: str, target_symbol: str) -> bool:
    target_upper = target_symbol.upper()
    event_upper = event_symbol.upper()
    if event_upper in {"ALL", "*"}:
        return True
    if event_upper == target_upper:
        return True
    # XAUUSD should also react to USD events.
    if "USD" in target_upper and event_upper == "USD":
        return True
    # FX pair relevance if event symbol matches one leg.
    if len(target_upper) >= 6 and event_upper in {target_upper[:3], target_upper[3:6]}:
        return True
    return False


def _enforce_news_governance(
    *,
    provider_snapshot: ProviderSnapshot,
    mode: RuntimeMode,
    symbol: str,
) -> None:
    del symbol
    if mode is RuntimeMode.DIAGNOSTIC:
        return
    if provider_snapshot.provider_mode is NewsProviderMode.DISABLED_DIAGNOSTIC_ONLY:
        raise ConfigValidationError(
            "DISABLED_DIAGNOSTIC_ONLY news mode is forbidden outside DIAGNOSTIC runtime"
        )
    if mode in {RuntimeMode.FORWARD_TEST, RuntimeMode.CONTEST} and provider_snapshot.provider_state is not NewsProviderState.READY:
        raise ConfigValidationError(
            f"News provider is not ready for {mode.value}: {provider_snapshot.provider_state.value}"
        )


def _parse_utc_datetime(raw_value: Any, *, field_name: str) -> datetime:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ConfigValidationError(f"{field_name} must be a non-empty ISO-8601 string")
    value = raw_value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ConfigValidationError(f"{field_name} must be a valid ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ConfigValidationError(f"{field_name} must be timezone-aware UTC datetime")
    return parsed.astimezone(timezone.utc)


def _ensure_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ConfigValidationError(f"{field_name} must be timezone-aware UTC datetime")
    return value.astimezone(timezone.utc)


def _require_non_empty_string(raw_value: Any, *, field_name: str) -> str:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ConfigValidationError(f"{field_name} must be a non-empty string")
    return raw_value.strip()
