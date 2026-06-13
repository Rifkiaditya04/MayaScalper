"""State object untuk regime, freshness, posisi live, dan persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
import json
from typing import Any


@dataclass(slots=True)
class DirectionLockState:
    direction: str
    htf_bias: str
    ref_high: float
    ref_low: float
    timestamp: datetime
    buffer: float
    monotonic_started_at: float | None = None


@dataclass(slots=True)
class FreshnessState:
    direction: str
    armed: bool = False
    reset_detected: bool = False
    reset_bar_time: datetime | None = None


@dataclass(slots=True)
class PositionRuntimeState:
    ticket: int
    side: str
    opened_at: datetime
    entry_price: float
    tp_price: float
    effective_tp_distance: float
    entry_m5_anchor: datetime | None = None
    max_favorable_distance: float = 0.0
    be_applied: bool = False
    expected_close: bool = False
    close_reason: str | None = None
    protection_verified: bool = True
    recovery_mode: str | None = None
    recovery_attempts: int = 0
    recovery_next_retry_at: datetime | None = None
    recovery_next_retry_monotonic: float | None = None
    recovery_last_error: str | None = None
    recovery_escalated: bool = False


@dataclass(slots=True)
class StrategyNearMissSample:
    timestamp: datetime
    symbol: str
    side_candidate: str
    buy_score: int
    sell_score: int
    blockers: tuple[str, ...]
    primary_blocker: str
    htf_bias: str
    candidate_location: str
    tp_feasible: bool
    entry_price_reference: float
    mfe_15m: float | None = None
    mae_15m: float | None = None
    close_15m: float | None = None
    mfe_30m: float | None = None
    mae_30m: float | None = None
    close_30m: float | None = None
    mfe_60m: float | None = None
    mae_60m: float | None = None
    close_60m: float | None = None


@dataclass(slots=True)
class ProgressExitCounterfactualCase:
    ticket: int
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    realized_pnl: float
    exit_timestamp: datetime
    progress: float
    required_progress: float
    closed_bars_since_entry: int
    original_tp_price: float
    effective_tp_distance: float
    direction_lock_active_after_exit: bool
    direction_lock_side: str | None
    direction_lock_reason: str | None
    mfe_15m: float | None = None
    mae_15m: float | None = None
    close_15m: float | None = None
    mfe_30m: float | None = None
    mae_30m: float | None = None
    close_30m: float | None = None
    mfe_60m: float | None = None
    mae_60m: float | None = None
    close_60m: float | None = None
    original_tp_hit_within_60m: bool | None = None
    original_tp_hit_at: datetime | None = None
    completed: bool = False


@dataclass(slots=True)
class StrategyTelemetry:
    block_counts: dict[str, int] = field(default_factory=dict)
    primary_block_counts: dict[str, int] = field(default_factory=dict)
    executed_trades: int = 0
    blocked_decisions: int = 0
    near_miss_recorded: int = 0
    near_miss_completed_15m: int = 0
    near_miss_completed_30m: int = 0
    near_miss_completed_60m: int = 0
    near_miss_samples: list[StrategyNearMissSample] = field(default_factory=list)
    progress_exit_counterfactual_started: int = 0
    progress_exit_counterfactual_completed: int = 0
    progress_exit_counterfactual_tp_hits_60m: int = 0
    progress_exit_cases: list[ProgressExitCounterfactualCase] = field(default_factory=list)


@dataclass(slots=True)
class BotState:
    direction_locks: dict[str, DirectionLockState] = field(default_factory=dict)
    freshness: dict[str, FreshnessState] = field(default_factory=dict)
    positions: dict[int, PositionRuntimeState] = field(default_factory=dict)
    manual_close_pause_until: datetime | None = None
    manual_close_pause_deadline_monotonic: float | None = None
    last_processed_m5_bar_time: datetime | None = None
    session_started_at: datetime | None = None
    startup_reconciled_at: datetime | None = None
    last_execution_signal: str | None = None
    last_execution_m5_bar_time: datetime | None = None
    last_execution_at: datetime | None = None
    last_execution_monotonic: float | None = None
    current_trading_day: date | None = None
    daily_baseline_equity: float | None = None
    session_peak_equity: float | None = None
    trading_disabled: bool = False
    trading_disabled_reason: str | None = None
    manual_ack_required: bool = False
    manual_ack_reason: str | None = None
    manual_ack_timestamp: datetime | None = None
    entry_pause_until: datetime | None = None
    entry_pause_deadline_monotonic: float | None = None
    entry_pause_reason: str | None = None
    consecutive_losses: int = 0
    soft_drawdown_tripped_today: bool = False
    session_started_monotonic: float | None = None
    strategy_telemetry: StrategyTelemetry = field(default_factory=StrategyTelemetry)


@dataclass(slots=True)
class PersistedDirectionLock:
    direction: str
    htf_bias: str
    ref_high: float
    ref_low: float
    timestamp: datetime
    buffer: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "htf_bias": self.htf_bias,
            "ref_high": self.ref_high,
            "ref_low": self.ref_low,
            "timestamp": self.timestamp.isoformat(),
            "buffer": self.buffer,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PersistedDirectionLock:
        return cls(
            direction=str(raw["direction"]),
            htf_bias=str(raw.get("htf_bias", "HOLD")),
            ref_high=float(raw["ref_high"]),
            ref_low=float(raw["ref_low"]),
            timestamp=datetime.fromisoformat(str(raw["timestamp"])),
            buffer=float(raw["buffer"]),
        )


@dataclass(slots=True)
class BotPersistentStateSnapshot:
    version: int
    symbol: str
    magic_number: int
    trading_day_key: str | None = None
    updated_at: datetime | None = None
    trading_disabled: bool = False
    trading_disabled_reason: str | None = None
    manual_ack_required: bool = False
    manual_ack_reason: str | None = None
    manual_ack_timestamp: datetime | None = None
    daily_baseline_equity: float | None = None
    session_peak_equity: float | None = None
    last_entry_timestamp: datetime | None = None
    manual_close_pause_until: datetime | None = None
    soft_drawdown_tripped_today: bool = False
    entry_pause_until: datetime | None = None
    entry_pause_reason: str | None = None
    consecutive_losses: int = 0
    direction_locks: dict[str, PersistedDirectionLock] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "symbol": self.symbol,
            "magic_number": self.magic_number,
            "trading_day_key": self.trading_day_key,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "trading_disabled": self.trading_disabled,
            "trading_disabled_reason": self.trading_disabled_reason,
            "manual_ack_required": self.manual_ack_required,
            "manual_ack_reason": self.manual_ack_reason,
            "manual_ack_timestamp": self.manual_ack_timestamp.isoformat() if self.manual_ack_timestamp else None,
            "daily_baseline_equity": self.daily_baseline_equity,
            "session_peak_equity": self.session_peak_equity,
            "last_entry_timestamp": self.last_entry_timestamp.isoformat() if self.last_entry_timestamp else None,
            "manual_close_pause_until": self.manual_close_pause_until.isoformat() if self.manual_close_pause_until else None,
            "soft_drawdown_tripped_today": self.soft_drawdown_tripped_today,
            "entry_pause_until": self.entry_pause_until.isoformat() if self.entry_pause_until else None,
            "entry_pause_reason": self.entry_pause_reason,
            "consecutive_losses": self.consecutive_losses,
            "direction_locks": {
                key: lock.to_dict() for key, lock in self.direction_locks.items()
            },
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BotPersistentStateSnapshot:
        direction_locks_raw = raw.get("direction_locks", {}) or {}
        direction_locks = {
            str(key): PersistedDirectionLock.from_dict(value)
            for key, value in direction_locks_raw.items()
            if isinstance(value, dict)
        }
        return cls(
            version=int(raw.get("version", 1)),
            symbol=str(raw.get("symbol", "")),
            magic_number=int(raw.get("magic_number", 0)),
            trading_day_key=str(raw.get("trading_day_key")) if raw.get("trading_day_key") is not None else None,
            updated_at=datetime.fromisoformat(str(raw["updated_at"])) if raw.get("updated_at") else None,
            trading_disabled=bool(raw.get("trading_disabled", False)),
            trading_disabled_reason=str(raw.get("trading_disabled_reason")) if raw.get("trading_disabled_reason") is not None else None,
            manual_ack_required=bool(raw.get("manual_ack_required", False)),
            manual_ack_reason=str(raw.get("manual_ack_reason")) if raw.get("manual_ack_reason") is not None else None,
            manual_ack_timestamp=datetime.fromisoformat(str(raw["manual_ack_timestamp"])) if raw.get("manual_ack_timestamp") else None,
            daily_baseline_equity=float(raw["daily_baseline_equity"]) if raw.get("daily_baseline_equity") is not None else None,
            session_peak_equity=float(raw["session_peak_equity"]) if raw.get("session_peak_equity") is not None else None,
            last_entry_timestamp=datetime.fromisoformat(str(raw["last_entry_timestamp"])) if raw.get("last_entry_timestamp") else None,
            manual_close_pause_until=datetime.fromisoformat(str(raw["manual_close_pause_until"])) if raw.get("manual_close_pause_until") else None,
            soft_drawdown_tripped_today=bool(raw.get("soft_drawdown_tripped_today", False)),
            entry_pause_until=datetime.fromisoformat(str(raw["entry_pause_until"])) if raw.get("entry_pause_until") else None,
            entry_pause_reason=str(raw.get("entry_pause_reason")) if raw.get("entry_pause_reason") is not None else None,
            consecutive_losses=int(raw.get("consecutive_losses", 0) or 0),
            direction_locks=direction_locks,
        )


def load_persistent_state(path: Path) -> BotPersistentStateSnapshot | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Persistent state root must be an object")
    return BotPersistentStateSnapshot.from_dict(raw)


def save_persistent_state(path: Path, snapshot: BotPersistentStateSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(snapshot.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)
