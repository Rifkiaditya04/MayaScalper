"""Core state models for Tournament Scalping Predator V1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum, auto
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping


EPSILON = 1e-8


def utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _require_aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _require_non_negative(value: float, field_name: str) -> None:
    if value < 0.0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_finite(value: float, field_name: str) -> None:
    if not isfinite(value):
        raise ValueError(f"{field_name} must be finite")


class Regime(Enum):
    TREND = auto()
    BREAKOUT = auto()
    CHOP = auto()
    NEWS_DEAD = auto()


class Module(Enum):
    PULLBACK_CONTINUATION = auto()
    BREAKOUT_MOMENTUM = auto()
    NONE = auto()


class AggressionState(Enum):
    DEFENSIVE = auto()
    NORMAL = auto()
    AGGRESSIVE = auto()


class Direction(Enum):
    LONG = auto()
    SHORT = auto()
    FLAT = auto()


class TradePhase(Enum):
    IDLE = auto()
    ENTERED = auto()
    PYRAMIDED = auto()
    EXITING = auto()
    COOLDOWN = auto()


class ConfidenceTier(str, Enum):
    WEAK = "WEAK"
    NORMAL = "NORMAL"
    GOOD = "GOOD"
    ELITE = "ELITE"


class GovernorState(str, Enum):
    SURVIVE = "SURVIVE"
    NORMAL = "NORMAL"
    HUNT = "HUNT"
    PROTECT = "PROTECT"
    SPRINT = "SPRINT"


class ExecutionStatus(str, Enum):
    FILLED = "FILLED"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILLED_UNVERIFIED = "FILLED_UNVERIFIED"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"
    SPREAD_VETOED = "SPREAD_VETOED"
    STALE_SIGNAL = "STALE_SIGNAL"
    DUPLICATE = "DUPLICATE"
    INVALID_PARAMS = "INVALID_PARAMS"
    MT5_ERROR = "MT5_ERROR"


class RetcodeClass(str, Enum):
    SUCCESS = "SUCCESS"
    RETRYABLE = "RETRYABLE"
    NON_RETRYABLE = "NON_RETRYABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CandleData:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _require_aware_utc(self.timestamp, "timestamp"))
        if not self.timeframe.strip():
            raise ValueError("timeframe must be non-empty")
        for field_name in ("open", "high", "low", "close", "volume"):
            _require_finite(getattr(self, field_name), field_name)
        _require_non_negative(self.volume, "volume")


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    symbol: str
    timestamp: datetime
    m1: CandleData
    m5: CandleData
    m15: CandleData
    h1: CandleData
    atr_m1: float
    atr_m1_base: float
    atr_m5: float
    atr_m5_base: float
    atr_m15_base: float
    atr_h1_base: float
    atr_m1_prev_window: float
    atr_m5_prev_window: float
    adx_m5: float
    adx_m15: float
    adx_h1: float
    h1_slope: float
    m15_slope: float
    spread_current: float
    spread_baseline: float
    tick_vol_m1: float
    tick_vol_m1_base: float
    swing_high_m5: float
    swing_low_m5: float
    m1_closes_recent: tuple[float, ...]
    is_news_window: bool
    session: str
    bid: float
    ask: float
    source_server_time: datetime | None = None
    is_closed_bar: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _require_aware_utc(self.timestamp, "timestamp"))
        if self.source_server_time is not None:
            object.__setattr__(
                self,
                "source_server_time",
                _require_aware_utc(self.source_server_time, "source_server_time"),
            )
        if not self.symbol.strip():
            raise ValueError("symbol must be non-empty")
        if not self.session.strip():
            raise ValueError("session must be non-empty")
        if not self.is_closed_bar:
            raise ValueError("MarketSnapshot must represent a fully closed bar")
        for field_name in (
            "atr_m1",
            "atr_m1_base",
            "atr_m5",
            "atr_m5_base",
            "atr_m15_base",
            "atr_h1_base",
            "atr_m1_prev_window",
            "atr_m5_prev_window",
            "adx_m5",
            "adx_m15",
            "adx_h1",
            "h1_slope",
            "m15_slope",
            "spread_current",
            "spread_baseline",
            "tick_vol_m1",
            "tick_vol_m1_base",
            "swing_high_m5",
            "swing_low_m5",
            "bid",
            "ask",
        ):
            _require_finite(getattr(self, field_name), field_name)
        for field_name in (
            "atr_m1",
            "atr_m1_base",
            "atr_m5",
            "atr_m5_base",
            "atr_m15_base",
            "atr_h1_base",
            "atr_m1_prev_window",
            "atr_m5_prev_window",
            "adx_m5",
            "adx_m15",
            "adx_h1",
            "spread_current",
            "spread_baseline",
            "tick_vol_m1",
            "tick_vol_m1_base",
        ):
            _require_non_negative(getattr(self, field_name), field_name)
        if self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid")
        closes = tuple(float(value) for value in self.m1_closes_recent)
        if not closes:
            raise ValueError("m1_closes_recent must not be empty")
        for idx, value in enumerate(closes):
            _require_finite(value, f"m1_closes_recent[{idx}]")
        object.__setattr__(self, "m1_closes_recent", closes)


@dataclass(frozen=True, slots=True)
class SignalScore:
    module: Module
    direction: Direction
    score: float
    confidence_tier: ConfidenceTier
    body_score: float
    wick_score: float
    atr_expansion: float
    session_bonus: float
    spread_penalty: float
    htf_alignment: float
    momentum_score: float
    volume_score: float
    entry_hint: float
    invalidation_anchor: float
    setup_id: str
    signal_timestamp: datetime
    setup_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "signal_timestamp",
            _require_aware_utc(self.signal_timestamp, "signal_timestamp"),
        )
        if not self.setup_id.strip():
            raise ValueError("setup_id must be non-empty")
        for field_name in (
            "score",
            "body_score",
            "wick_score",
            "atr_expansion",
            "session_bonus",
            "spread_penalty",
            "htf_alignment",
            "momentum_score",
            "volume_score",
            "entry_hint",
            "invalidation_anchor",
        ):
            _require_finite(float(getattr(self, field_name)), field_name)
        metadata = MappingProxyType(dict(self.setup_metadata))
        object.__setattr__(self, "setup_metadata", metadata)


@dataclass(slots=True)
class LayerState:
    ticket: int
    direction: Direction
    entry_price: float
    sl_price: float
    tp_price: float | None
    lot_size: float
    r_risk: float
    initial_r_distance: float
    open_time: datetime
    layer_index: int
    module: Module
    setup_id: str
    partial_taken: bool = False
    bars_in_trade: int = 0
    tp_attach_attempts: int = 0

    def __post_init__(self) -> None:
        self.open_time = _require_aware_utc(self.open_time, "open_time")
        if self.ticket <= 0:
            raise ValueError("ticket must be positive")
        if self.layer_index < 0:
            raise ValueError("layer_index must be non-negative")
        if self.bars_in_trade < 0:
            raise ValueError("bars_in_trade must be non-negative")
        if self.tp_attach_attempts < 0:
            raise ValueError("tp_attach_attempts must be non-negative")
        if not self.setup_id.strip():
            raise ValueError("setup_id must be non-empty")
        for field_name in ("entry_price", "sl_price", "lot_size", "r_risk", "initial_r_distance"):
            _require_finite(float(getattr(self, field_name)), field_name)
        if self.tp_price is not None:
            _require_finite(self.tp_price, "tp_price")
        if self.lot_size <= 0.0:
            raise ValueError("lot_size must be positive")
        if self.r_risk < 0.0:
            raise ValueError("r_risk must be non-negative")
        if self.initial_r_distance < 0.0:
            raise ValueError("initial_r_distance must be non-negative")

    def unrealized_r(self, current_price: float) -> float:
        """Always anchor unrealized R to the original stop distance."""
        _require_finite(current_price, "current_price")
        if self.initial_r_distance < EPSILON:
            return 0.0
        if self.direction == Direction.LONG:
            return (current_price - self.entry_price) / self.initial_r_distance
        if self.direction == Direction.SHORT:
            return (self.entry_price - current_price) / self.initial_r_distance
        return 0.0


@dataclass(slots=True)
class PositionState:
    phase: TradePhase = TradePhase.IDLE
    direction: Direction = Direction.FLAT
    module: Module = Module.NONE
    layers: list[LayerState] = field(default_factory=list)
    last_exit_time: datetime | None = None
    last_exit_direction: Direction | None = None
    bars_since_exit: int = 0
    reentry_count: int = 0
    _unrealized_pnl_r: float = 0.0

    def __post_init__(self) -> None:
        if self.last_exit_time is not None:
            self.last_exit_time = _require_aware_utc(self.last_exit_time, "last_exit_time")
        if self.bars_since_exit < 0:
            raise ValueError("bars_since_exit must be non-negative")
        if self.reentry_count < 0:
            raise ValueError("reentry_count must be non-negative")
        _require_finite(self._unrealized_pnl_r, "_unrealized_pnl_r")

    @property
    def layer_count(self) -> int:
        return len(self.layers)

    @property
    def total_r_risk(self) -> float:
        return sum(layer.r_risk for layer in self.layers)

    @property
    def unrealized_pnl_r(self) -> float:
        return self._unrealized_pnl_r

    def update_unrealized(self, pnl_r: float) -> None:
        _require_finite(pnl_r, "pnl_r")
        self._unrealized_pnl_r = pnl_r

    def add_layer(self, layer: LayerState, signal: SignalScore) -> None:
        self.layers.append(layer)
        self.direction = layer.direction
        self.module = signal.module
        self.phase = TradePhase.PYRAMIDED if len(self.layers) > 1 else TradePhase.ENTERED

    def transition_to_cooldown(
        self,
        exit_direction: Direction,
        *,
        exited_at: datetime | None = None,
    ) -> None:
        self.phase = TradePhase.COOLDOWN
        self.last_exit_time = _require_aware_utc(exited_at or utcnow(), "exited_at")
        self.last_exit_direction = exit_direction
        self.bars_since_exit = 0
        self.layers.clear()
        self._unrealized_pnl_r = 0.0
        self.direction = Direction.FLAT
        self.module = Module.NONE

    def transition_to_idle(self) -> None:
        self.phase = TradePhase.IDLE
        self.direction = Direction.FLAT
        self.module = Module.NONE
        self.bars_since_exit = 0

    def remove_layer(self, ticket: int) -> LayerState | None:
        for index, layer in enumerate(self.layers):
            if layer.ticket == ticket:
                removed = self.layers.pop(index)
                if not self.layers:
                    self.direction = Direction.FLAT
                    self.module = Module.NONE
                return removed
        return None


@dataclass(frozen=True, slots=True)
class RiskParams:
    r_weak: float = 0.20
    r_normal: float = 0.50
    r_good: float = 0.90
    r_elite: float = 1.25
    r_max_single: float = 1.50
    mult_defensive: float = 0.60
    mult_normal: float = 1.00
    mult_aggressive: float = 1.20
    equity_floor_ratio: float = 0.70
    dd_pct_defensive: float = 2.0
    dd_pct_kill: float = 4.0
    losses_defensive: int = 3
    losses_kill: int = 5
    wins_for_aggressive: int = 5
    daily_r_for_aggressive: float = 2.5
    dd_max_for_aggressive: float = 0.5
    pyramid_max_layers: int = 2
    pyramid_min_profit_r: float = 1.0
    pyramid_aggregate_cap: float = 2.5
    reentry_max_attempts: int = 1
    reentry_min_bars: int = 3
    spread_min_base_ticks: float = 30.0

    def __post_init__(self) -> None:
        for field_name in (
            "r_weak",
            "r_normal",
            "r_good",
            "r_elite",
            "r_max_single",
            "mult_defensive",
            "mult_normal",
            "mult_aggressive",
            "equity_floor_ratio",
            "dd_pct_defensive",
            "dd_pct_kill",
            "daily_r_for_aggressive",
            "dd_max_for_aggressive",
            "pyramid_min_profit_r",
            "pyramid_aggregate_cap",
            "spread_min_base_ticks",
        ):
            _require_finite(float(getattr(self, field_name)), field_name)
        if not (0.0 < self.r_weak < self.r_normal < self.r_good < self.r_elite <= self.r_max_single):
            raise ValueError("risk tiers must be strictly monotonic and capped by r_max_single")
        if not (0.0 < self.mult_defensive < self.mult_normal <= self.mult_aggressive):
            raise ValueError("aggression multipliers must satisfy defensive < normal <= aggressive")
        if not (0.0 < self.equity_floor_ratio <= 1.0):
            raise ValueError("equity_floor_ratio must be in (0, 1]")
        if not (0.0 < self.dd_pct_defensive < self.dd_pct_kill):
            raise ValueError("drawdown thresholds must satisfy defensive < kill")
        if not (0 < self.losses_defensive < self.losses_kill):
            raise ValueError("loss thresholds must satisfy defensive < kill")
        if self.wins_for_aggressive <= 0:
            raise ValueError("wins_for_aggressive must be positive")
        if self.dd_max_for_aggressive < 0.0:
            raise ValueError("dd_max_for_aggressive must be non-negative")
        if self.pyramid_max_layers < 1:
            raise ValueError("pyramid_max_layers must be at least 1")
        if self.reentry_max_attempts < 0:
            raise ValueError("reentry_max_attempts must be non-negative")
        if self.reentry_min_bars < 0:
            raise ValueError("reentry_min_bars must be non-negative")


@dataclass(frozen=True, slots=True)
class RegimeResult:
    regime: Regime
    confidence: float
    direction_bias: Direction
    conflict_note: str
    raw_scores: Mapping[str, float] = field(default_factory=dict)
    diagnostics: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_finite(self.confidence, "confidence")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        raw_scores = {str(key): float(value) for key, value in self.raw_scores.items()}
        for key, value in raw_scores.items():
            _require_finite(value, f"raw_scores[{key}]")
        object.__setattr__(self, "raw_scores", MappingProxyType(raw_scores))
        diagnostics = {str(key): str(value) for key, value in self.diagnostics.items()}
        object.__setattr__(self, "diagnostics", MappingProxyType(diagnostics))


@dataclass(frozen=True, slots=True)
class GovernorDirective:
    governor_state: GovernorState
    aggression_bias: float
    threshold_modifier: float
    session_risk_budget_r: float
    allow_aggressive_features: bool
    session_pause: bool
    governor_note: str

    def __post_init__(self) -> None:
        for field_name in ("aggression_bias", "threshold_modifier", "session_risk_budget_r"):
            _require_finite(float(getattr(self, field_name)), field_name)
        if not -1.0 <= self.aggression_bias <= 1.0:
            raise ValueError("aggression_bias must be between -1.0 and 1.0")
        if not -10.0 <= self.threshold_modifier <= 12.0:
            raise ValueError("threshold_modifier must be between -10.0 and 12.0")
        if self.session_risk_budget_r < 0.0:
            raise ValueError("session_risk_budget_r must be non-negative")


@dataclass(frozen=True, slots=True)
class CompetitionContext:
    total_days: int
    start_equity: float
    starting_date: date
    total_pnl_r: float
    daily_pnl_r: float
    session_pnl_r: float
    session_loss_count: int
    session_risk_committed_r: float
    current_session: str
    governor_state: GovernorState
    days_elapsed: int
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.total_days <= 0:
            raise ValueError("total_days must be positive")
        if self.days_elapsed < 0:
            raise ValueError("days_elapsed must be non-negative")
        if self.session_loss_count < 0:
            raise ValueError("session_loss_count must be non-negative")
        if not self.current_session.strip():
            raise ValueError("current_session must be non-empty")
        object.__setattr__(self, "updated_at", _require_aware_utc(self.updated_at, "updated_at"))
        for field_name in (
            "start_equity",
            "total_pnl_r",
            "daily_pnl_r",
            "session_pnl_r",
            "session_risk_committed_r",
        ):
            _require_finite(float(getattr(self, field_name)), field_name)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    status: ExecutionStatus
    ticket: int | None
    fill_price: float | None
    fill_lot: float | None
    sl_confirmed: bool
    tp_confirmed: bool
    retcode: int | None
    retcode_class: RetcodeClass
    slippage_ticks: float | None
    latency_ms: float | None
    setup_id: str
    attempt_count: int
    timestamp: datetime
    note: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _require_aware_utc(self.timestamp, "timestamp"))
        if not self.setup_id.strip():
            raise ValueError("setup_id must be non-empty")
        if self.attempt_count < 0:
            raise ValueError("attempt_count must be non-negative")
        for field_name in ("fill_price", "fill_lot", "slippage_ticks", "latency_ms"):
            value = getattr(self, field_name)
            if value is not None:
                _require_finite(float(value), field_name)
        if self.ticket is not None and self.ticket <= 0:
            raise ValueError("ticket must be positive when present")
        if self.retcode is not None and self.retcode < 0:
            raise ValueError("retcode must be non-negative when present")


@dataclass(slots=True)
class RuntimeState:
    symbol: str
    magic: int
    starting_equity: float
    start_time: datetime
    snap: MarketSnapshot | None = None
    regime: RegimeResult = field(
        default_factory=lambda: RegimeResult(
            regime=Regime.CHOP,
            confidence=0.0,
            direction_bias=Direction.FLAT,
            conflict_note="",
            raw_scores={},
        )
    )
    aggression: AggressionState = AggressionState.NORMAL
    position: PositionState = field(default_factory=PositionState)
    equity_current: float = 0.0
    equity_peak: float = 0.0
    daily_start_equity: float = 0.0
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    daily_pnl_r: float = 0.0
    total_trades_today: int = 0
    last_signal: SignalScore | None = None
    last_signal_age_bars: int = 0
    spread_elevated_bars: int = 0
    kill_switch_active: bool = False
    kill_reason: str = ""
    manual_override: bool = False
    competition_ctx: CompetitionContext | None = None
    risk_params: RiskParams = field(default_factory=RiskParams)
    last_broker_day: date | None = None
    consecutive_bar_errors: int = 0

    def __post_init__(self) -> None:
        self.start_time = _require_aware_utc(self.start_time, "start_time")
        if not self.symbol.strip():
            raise ValueError("symbol must be non-empty")
        if self.magic <= 0:
            raise ValueError("magic must be positive")
        for field_name in (
            "starting_equity",
            "equity_current",
            "equity_peak",
            "daily_start_equity",
            "daily_pnl_r",
        ):
            _require_finite(float(getattr(self, field_name)), field_name)
        for field_name in (
            "consecutive_wins",
            "consecutive_losses",
            "total_trades_today",
            "last_signal_age_bars",
            "spread_elevated_bars",
            "consecutive_bar_errors",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be non-negative")


__all__ = [
    "AggressionState",
    "CandleData",
    "CompetitionContext",
    "ConfidenceTier",
    "Direction",
    "EPSILON",
    "ExecutionResult",
    "ExecutionStatus",
    "GovernorDirective",
    "GovernorState",
    "LayerState",
    "MarketSnapshot",
    "Module",
    "PositionState",
    "Regime",
    "RegimeResult",
    "RetcodeClass",
    "RiskParams",
    "RuntimeState",
    "SignalScore",
    "TradePhase",
    "utcnow",
]
