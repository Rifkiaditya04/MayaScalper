"""Structured telemetry for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import json
from enum import Enum
from typing import Any, Mapping, Protocol

from .enums import GovernorState, PaceClassification, TelemetryCategory, TelemetrySeverity


class TelemetryValidationError(ValueError):
    """Raised when telemetry payloads violate the production contract."""


class AlertRoute(str, Enum):
    NONE = "NONE"
    OPERATOR_ATTENTION = "OPERATOR_ATTENTION"
    IMMEDIATE_ESCALATION = "IMMEDIATE_ESCALATION"


_EXECUTION_EVENT_KINDS = {
    "intent_created",
    "intent_rejected",
    "submitted",
    "filled",
    "rejected",
    "expired",
}


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    timestamp_utc: datetime
    category: TelemetryCategory
    severity: TelemetrySeverity
    event_id: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "timestamp_utc": _ensure_utc(self.timestamp_utc).isoformat(),
            "category": self.category.value,
            "severity": self.severity.value,
            "event_id": self.event_id,
            "message": self.message,
            "metadata": _json_ready(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True, slots=True)
class TelemetryIndexRecord:
    topic: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RuntimeMetricsSnapshot:
    captured_at_utc: datetime
    equity: float
    balance: float
    drawdown: float
    active_positions: int
    signal_count: int
    execution_count: int
    win_rate: float
    governor_state: GovernorState
    pace_state: PaceClassification


@dataclass(frozen=True, slots=True)
class DailyRuntimeSummary:
    date: date
    runtime_hours: float
    pnl: float
    drawdown: float
    trades: int
    wins: int
    losses: int
    win_rate: float
    governor_transitions: int
    execution_failures: int
    recovery_events: int
    active_positions: int
    signal_count: int
    execution_count: int
    governor_state: str
    pace_state: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "runtime_hours": round(self.runtime_hours, 6),
            "pnl": round(self.pnl, 6),
            "drawdown": round(self.drawdown, 6),
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 6),
            "governor_transitions": self.governor_transitions,
            "execution_failures": self.execution_failures,
            "recovery_events": self.recovery_events,
            "active_positions": self.active_positions,
            "signal_count": self.signal_count,
            "execution_count": self.execution_count,
            "governor_state": self.governor_state,
            "pace_state": self.pace_state,
        }


class TelemetrySink(Protocol):
    def write(self, topic: str, payload: Mapping[str, Any]) -> None: ...


@dataclass(slots=True)
class TelemetryCollector:
    session_started_at_utc: datetime
    starting_balance: float | None = None
    _events: list[TelemetryEvent] = field(default_factory=list)
    _latest_metrics: RuntimeMetricsSnapshot | None = None
    _governor_transition_count: int = 0
    _execution_failure_count: int = 0
    _recovery_event_count: int = 0
    _wins: int = 0
    _losses: int = 0

    def emit_event(
        self,
        *,
        category: TelemetryCategory,
        severity: TelemetrySeverity,
        event_id: str,
        message: str,
        metadata: Mapping[str, Any] | None = None,
        timestamp_utc: datetime | None = None,
    ) -> TelemetryEvent:
        event = TelemetryEvent(
            timestamp_utc=_ensure_utc(timestamp_utc or _now_utc()),
            category=category,
            severity=severity,
            event_id=_require_non_empty(event_id, "event_id"),
            message=_require_non_empty(message, "message"),
            metadata=_validate_metadata(metadata),
        )
        self._events.append(event)
        return event

    def record_governor_transition(
        self,
        *,
        from_state: GovernorState,
        to_state: GovernorState,
        reason: str,
        timestamp_utc: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> TelemetryEvent:
        self._governor_transition_count += 1
        merged = _merge_metadata(
            metadata,
            {
                "from_state": from_state.value,
                "to_state": to_state.value,
                "reason": reason,
            },
        )
        return self.emit_event(
            category=TelemetryCategory.GOVERNOR,
            severity=_governor_transition_severity(from_state, to_state, reason),
            event_id=f"governor.transition.{from_state.value.lower()}_to_{to_state.value.lower()}",
            message=f"Governor transition {from_state.value} -> {to_state.value}",
            metadata=merged,
            timestamp_utc=timestamp_utc,
        )

    def record_execution_event(
        self,
        *,
        kind: str,
        timestamp_utc: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> TelemetryEvent:
        normalized_kind = _require_non_empty(kind, "kind").lower()
        if normalized_kind not in _EXECUTION_EVENT_KINDS:
            raise TelemetryValidationError(
                f"Unsupported execution telemetry kind: {kind}"
            )
        if normalized_kind in {"intent_rejected", "rejected", "expired"}:
            self._execution_failure_count += 1
        merged = _merge_metadata(metadata, {"kind": normalized_kind})
        if _is_trade_win(merged):
            self._wins += 1
        elif _is_trade_loss(merged):
            self._losses += 1
        return self.emit_event(
            category=TelemetryCategory.EXECUTION,
            severity=_execution_severity(normalized_kind, merged),
            event_id=f"execution.{normalized_kind}",
            message=f"Execution event {normalized_kind}",
            metadata=merged,
            timestamp_utc=timestamp_utc,
        )

    def record_recovery_event(
        self,
        *,
        stage: str,
        outcome: str,
        timestamp_utc: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> TelemetryEvent:
        self._recovery_event_count += 1
        merged = _merge_metadata(metadata, {"stage": stage, "outcome": outcome})
        return self.emit_event(
            category=TelemetryCategory.RECOVERY,
            severity=_recovery_severity(outcome),
            event_id=f"recovery.{_slug(stage)}.{_slug(outcome)}",
            message=f"Recovery {stage}: {outcome}",
            metadata=merged,
            timestamp_utc=timestamp_utc,
        )

    def record_runtime_metrics(self, snapshot: RuntimeMetricsSnapshot) -> RuntimeMetricsSnapshot:
        if self.starting_balance is None:
            self.starting_balance = snapshot.balance
        self._latest_metrics = snapshot
        return snapshot

    def build_daily_summary(self, *, summary_date: date | None = None) -> DailyRuntimeSummary:
        if self._latest_metrics is None:
            raise TelemetryValidationError("Runtime metrics are required before building a daily summary")
        summary_date = summary_date or self._latest_metrics.captured_at_utc.date()
        runtime_hours = max(
            0.0,
            (
                _ensure_utc(self._latest_metrics.captured_at_utc)
                - _ensure_utc(self.session_started_at_utc)
            ).total_seconds()
            / 3600.0,
        )
        pnl = self._latest_metrics.balance - (self.starting_balance if self.starting_balance is not None else self._latest_metrics.balance)
        return DailyRuntimeSummary(
            date=summary_date,
            runtime_hours=runtime_hours,
            pnl=pnl,
            drawdown=self._latest_metrics.drawdown,
            trades=self._latest_metrics.execution_count,
            wins=self._wins,
            losses=self._losses,
            win_rate=self._latest_metrics.win_rate,
            governor_transitions=self._governor_transition_count,
            execution_failures=self._execution_failure_count,
            recovery_events=self._recovery_event_count,
            active_positions=self._latest_metrics.active_positions,
            signal_count=self._latest_metrics.signal_count,
            execution_count=self._latest_metrics.execution_count,
            governor_state=self._latest_metrics.governor_state.value,
            pace_state=self._latest_metrics.pace_state.value,
        )

    def route_alert(self, event: TelemetryEvent) -> AlertRoute:
        if event.severity is TelemetrySeverity.CRITICAL:
            return AlertRoute.IMMEDIATE_ESCALATION
        if event.severity in {TelemetrySeverity.WARNING, TelemetrySeverity.ERROR}:
            return AlertRoute.OPERATOR_ATTENTION
        return AlertRoute.NONE

    def export_index_records(self, *, include_summary: bool = True) -> tuple[TelemetryIndexRecord, ...]:
        records = [
            TelemetryIndexRecord(topic=f"telemetry.event.{event.category.value.lower()}", payload=event.to_payload())
            for event in self._events
        ]
        if include_summary and self._latest_metrics is not None:
            records.append(
                TelemetryIndexRecord(
                    topic="telemetry.summary.daily",
                    payload=self.build_daily_summary().to_payload(),
                )
            )
        return tuple(records)

    def clear(self) -> None:
        self._events.clear()

    @property
    def latest_metrics(self) -> RuntimeMetricsSnapshot | None:
        return self._latest_metrics


def emit_event(event_type: str, payload: Mapping[str, Any]) -> TelemetryEvent:
    """Compatibility helper for structured telemetry emission."""

    if not isinstance(payload, Mapping):
        raise TelemetryValidationError("Telemetry payload must be a mapping")
    category = _coerce_category(payload.get("category") or _parse_event_type(event_type)[0])
    severity = _coerce_severity(payload.get("severity") or _parse_event_type(event_type)[1])
    timestamp_raw = payload.get("timestamp_utc")
    timestamp_utc = _parse_datetime(timestamp_raw) if timestamp_raw is not None else _now_utc()
    event = TelemetryEvent(
        timestamp_utc=timestamp_utc,
        category=category,
        severity=severity,
        event_id=_require_non_empty(str(payload.get("event_id", event_type)), "event_id"),
        message=_require_non_empty(str(payload.get("message", event_type)), "message"),
        metadata=_validate_metadata(payload.get("metadata")),
    )
    return event


def serialize_event(event: TelemetryEvent) -> str:
    return event.to_json()


def build_runtime_metrics_snapshot(
    *,
    captured_at_utc: datetime,
    equity: float,
    balance: float,
    drawdown: float,
    active_positions: int,
    signal_count: int,
    execution_count: int,
    win_rate: float,
    governor_state: GovernorState,
    pace_state: PaceClassification,
) -> RuntimeMetricsSnapshot:
    return RuntimeMetricsSnapshot(
        captured_at_utc=_ensure_utc(captured_at_utc),
        equity=float(equity),
        balance=float(balance),
        drawdown=float(drawdown),
        active_positions=int(active_positions),
        signal_count=int(signal_count),
        execution_count=int(execution_count),
        win_rate=float(win_rate),
        governor_state=governor_state,
        pace_state=pace_state,
    )


def _governor_transition_severity(from_state: GovernorState, to_state: GovernorState, reason: str) -> TelemetrySeverity:
    del from_state
    if to_state is GovernorState.KILL_REVIEW or "hard_shutdown" in reason.lower():
        return TelemetrySeverity.CRITICAL
    if to_state in {GovernorState.SURVIVE, GovernorState.PROTECT}:
        return TelemetrySeverity.WARNING
    return TelemetrySeverity.INFO


def _execution_severity(kind: str, metadata: Mapping[str, Any]) -> TelemetrySeverity:
    if kind in {"intent_rejected", "rejected", "expired"}:
        return TelemetrySeverity.WARNING
    if metadata.get("severity") == "critical":
        return TelemetrySeverity.CRITICAL
    return TelemetrySeverity.INFO


def _recovery_severity(outcome: str) -> TelemetrySeverity:
    normalized = outcome.strip().lower()
    if normalized in {"blocked", "failure", "failed", "mismatch"}:
        return TelemetrySeverity.ERROR
    return TelemetrySeverity.INFO


def _is_trade_win(metadata: Mapping[str, Any]) -> bool:
    outcome = str(metadata.get("trade_result") or metadata.get("result") or metadata.get("outcome") or "").strip().upper()
    return outcome == "WIN"


def _is_trade_loss(metadata: Mapping[str, Any]) -> bool:
    outcome = str(metadata.get("trade_result") or metadata.get("result") or metadata.get("outcome") or "").strip().upper()
    return outcome == "LOSS"


def _merge_metadata(
    metadata: Mapping[str, Any] | None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if metadata is not None:
        merged.update(_validate_metadata(metadata))
    if extra is not None:
        merged.update(_validate_metadata(extra))
    return merged


def _validate_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    if not isinstance(metadata, Mapping):
        raise TelemetryValidationError("Telemetry metadata must be a mapping")
    return {str(key): _json_ready(value) for key, value in metadata.items()}


def _coerce_category(raw: Any) -> TelemetryCategory:
    if raw is None:
        return TelemetryCategory.TELEMETRY
    if isinstance(raw, TelemetryCategory):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return TelemetryCategory(raw.strip())
        except ValueError as exc:
            raise TelemetryValidationError("Telemetry category must be a known canonical value") from exc
    raise TelemetryValidationError("Telemetry category must be a known canonical value")


def _coerce_severity(raw: Any) -> TelemetrySeverity:
    if raw is None:
        return TelemetrySeverity.INFO
    if isinstance(raw, TelemetrySeverity):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return TelemetrySeverity(raw.strip())
        except ValueError as exc:
            raise TelemetryValidationError("Telemetry severity must be a known canonical value") from exc
    raise TelemetryValidationError("Telemetry severity must be a known canonical value")


def _parse_event_type(event_type: str) -> tuple[TelemetryCategory | None, TelemetrySeverity | None]:
    raw = _require_non_empty(event_type, "event_type")
    parts = [part.strip() for part in raw.split(":", 1)]
    category = _try_parse_category(parts[0]) if parts else None
    severity = _try_parse_severity(parts[1]) if len(parts) > 1 else None
    return category, severity


def _try_parse_category(raw: str) -> TelemetryCategory | None:
    try:
        return TelemetryCategory(raw)
    except ValueError:
        return None


def _try_parse_severity(raw: str) -> TelemetrySeverity | None:
    try:
        return TelemetrySeverity(raw)
    except ValueError:
        return None


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return _ensure_utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_ready(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, set):
        return [_json_ready(item) for item in sorted(value, key=lambda item: str(item))]
    return value


def _parse_datetime(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return _ensure_utc(raw)
    if not isinstance(raw, str) or not raw.strip():
        raise TelemetryValidationError("timestamp_utc must be a valid ISO datetime string")
    try:
        return _ensure_utc(datetime.fromisoformat(raw))
    except ValueError as exc:
        raise TelemetryValidationError("timestamp_utc must be a valid ISO datetime string") from exc


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise TelemetryValidationError("timestamp must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _require_non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TelemetryValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _slug(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in value.strip()).strip("_") or "unknown"
