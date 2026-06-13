"""Typed model scaffolds for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import (
    ClockHealth,
    Direction,
    ExecutionRegistryState,
    GovernorState,
    HealthState,
    NewsProviderMode,
    NewsProviderState,
    PaceClassification,
    ProfileName,
    RegimeName,
    RuntimeMode,
    RiskAction,
    SessionName,
    SignalFamily,
)


@dataclass(frozen=True, slots=True)
class ContractSnapshot:
    symbol: str
    point: float
    tick_size: float
    tick_value: float
    min_lot: float
    max_lot: float
    lot_step: float
    stop_level_points: int
    freeze_level_points: int


@dataclass(frozen=True, slots=True)
class NewsSnapshot:
    provider_mode: NewsProviderMode
    provider_state: NewsProviderState
    snapshot_generated_at_utc: datetime | None
    lockout_active: bool
    next_relevant_event_utc: datetime | None
    relevant_events: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class ClockState:
    broker_time_utc: datetime
    local_time_utc: datetime
    skew_seconds: float
    health: ClockHealth
    backward_jump_seconds: float = 0.0
    diagnostic_flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    cycle_time_utc: datetime
    symbol: str
    tick_bid: float
    tick_ask: float
    spread_points: float
    spread_ratio: float
    spread_health: HealthState
    session: SessionName
    news: NewsSnapshot
    contract: ContractSnapshot
    feed_health: HealthState
    latency_health: HealthState
    bars_h1: tuple[dict[str, Any], ...]
    bars_m15: tuple[dict[str, Any], ...]
    bars_m5: tuple[dict[str, Any], ...]
    bars_m1: tuple[dict[str, Any], ...]
    indicator_bundle: dict[str, Any] = field(default_factory=dict)
    payload_health: HealthState = HealthState.GREEN
    payload_diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RegimeDecision:
    regime: RegimeName
    confidence: float
    direction_bias: Direction
    raw_scores: dict[str, float]
    diagnostics: dict[str, str]


@dataclass(frozen=True, slots=True)
class SignalDecision:
    setup_id: str
    signal_family: SignalFamily
    symbol: str
    direction: Direction
    score: float
    threshold: float
    expires_at_utc: datetime
    rationale: str
    lineage: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SignalEvaluation:
    accepted: bool
    decision: SignalDecision | None
    reject_reason: str
    signal_key: str
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    action: RiskAction
    risk_multiplier: float
    sized_volume: float
    invalidation_price: float
    hard_block_reason: str
    governor_adjusted_state: GovernorState
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    symbol: str
    direction: Direction
    setup_id: str
    correlation_group: str
    risk_pct: float
    signal_score: float = 0.0
    open_time_utc: datetime | None = None
    pyramid_count: int = 0


@dataclass(frozen=True, slots=True)
class RiskContext:
    account_equity: float = 100_000.0
    daily_start_equity: float = 100_000.0
    current_drawdown_pct: float = 0.0
    current_daily_loss_pct: float = 0.0
    current_unrealized_r: float = 0.0
    loss_streak: int = 0
    open_positions: tuple[PositionSnapshot, ...] = ()
    execution_health: HealthState = HealthState.GREEN
    spread_health: HealthState = HealthState.GREEN
    latency_health: HealthState = HealthState.GREEN
    broker_stable: bool = True
    recovery_uncertainty: bool = False
    execution_anomaly_cluster: bool = False
    current_portfolio_risk_pct: float = 0.0
    current_symbol_risk_pct: float = 0.0


@dataclass(frozen=True, slots=True)
class GovernorContext:
    contest_elapsed_pct: float
    equity: float
    peak_equity: float
    drawdown_pct: float
    daily_loss_pct: float
    realized_pnl_r: float
    signal_density: float
    execution_health: HealthState
    feed_health: HealthState
    opportunity_starvation_minutes: float
    recovery_momentum: float
    profile: ProfileName | None = None
    ranking_proxy_available: bool = False
    ranking_proxy_pace_ratio: float | None = None
    broker_stable: bool = True
    recovery_uncertainty: bool = False
    execution_anomaly_cluster: bool = False
    news_clear: bool = True
    open_positions: tuple[PositionSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class PortfolioContext:
    active_positions: tuple[PositionSnapshot, ...] = ()
    current_time_utc: datetime | None = None
    execution_health: HealthState = HealthState.GREEN
    spread_health: HealthState = HealthState.GREEN
    latency_health: HealthState = HealthState.GREEN
    replacement_superiority: float = 0.12
    max_positions: int = 2
    symbol_cooldown_until_utc: dict[str, datetime] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GovernorDecision:
    state: GovernorState
    state_reason: str
    pace_classification: PaceClassification
    aggression_multiplier: float
    profile_constraints: dict[str, Any]
    escalation_flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    setup_id: str
    signal_family: SignalFamily
    symbol: str
    direction: Direction
    decision_price: float
    sized_volume: float
    submission_uuid: str
    cycle_time_utc: datetime


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    accepted: bool
    rejected: bool
    filled: bool
    partial_fill: bool
    ticket: int | None
    broker_code: str
    classification: str
    retryable: bool
    fatal: bool
    terminal: bool
    message: str
    submission_uuid: str
    setup_id: str
    symbol: str
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    registry_state: ExecutionRegistryState | None = None


@dataclass(frozen=True, slots=True)
class ExecutionRegistryEntry:
    setup_id: str
    submission_uuid: str
    symbol: str
    state: ExecutionRegistryState
    updated_at_utc: datetime
    direction: Direction | None = None
    decision_price: float | None = None
    cycle_time_utc: datetime | None = None
    expires_at_utc: datetime | None = None
    broker_ticket: int | None = None


@dataclass(frozen=True, slots=True)
class LifecycleState:
    position_ticket: int
    setup_id: str
    symbol: str
    opened_at_utc: datetime
    direction: Direction
    entry_price: float
    initial_stop: float
    initial_r_distance: float
    current_stop: float
    partial_taken: bool
    trail_active: bool
    pyramid_count: int
    thesis_expiry_utc: datetime
    orphan_recovered: bool


@dataclass(slots=True)
class RuntimeState:
    mode: RuntimeMode
    governor_state: GovernorState
    started_at_utc: datetime
    last_cycle_time_utc: datetime | None = None
    last_processed_m5_close_utc: datetime | None = None
    kill_reason: str = ""
    health: dict[str, HealthState] = field(default_factory=dict)
