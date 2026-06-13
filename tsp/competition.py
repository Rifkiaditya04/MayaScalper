"""Competition governor for TSP V1."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone

from .config import CompetitionConfig
from .state import (
    AggressionState,
    CompetitionContext,
    GovernorDirective,
    GovernorState,
    Regime,
    RegimeResult,
    RuntimeState,
)


ACTIVE_SESSIONS = {"LONDON", "NY", "OVERLAP"}


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class CompetitionContextDelta:
    total_pnl_r: float = 0.0
    daily_pnl_r: float = 0.0
    session_pnl_r: float = 0.0
    session_loss_count_delta: int = 0
    session_risk_committed_r_delta: float = 0.0
    current_session: str | None = None
    governor_state: GovernorState | None = None
    updated_at: datetime | None = None


def build_competition_context(
    *,
    cfg: CompetitionConfig,
    start_equity: float,
    starting_date: date,
    current_session: str,
    now: datetime,
) -> CompetitionContext:
    return CompetitionContext(
        total_days=cfg.total_days,
        start_equity=start_equity,
        starting_date=starting_date,
        total_pnl_r=0.0,
        daily_pnl_r=0.0,
        session_pnl_r=0.0,
        session_loss_count=0,
        session_risk_committed_r=0.0,
        current_session=current_session,
        governor_state=GovernorState.NORMAL,
        days_elapsed=0,
        updated_at=_to_utc(now),
    )


def apply_context_delta(ctx: CompetitionContext, delta: CompetitionContextDelta) -> CompetitionContext:
    updated_at = _to_utc(delta.updated_at) if delta.updated_at is not None else ctx.updated_at
    return CompetitionContext(
        total_days=ctx.total_days,
        start_equity=ctx.start_equity,
        starting_date=ctx.starting_date,
        total_pnl_r=ctx.total_pnl_r + delta.total_pnl_r,
        daily_pnl_r=ctx.daily_pnl_r + delta.daily_pnl_r,
        session_pnl_r=ctx.session_pnl_r + delta.session_pnl_r,
        session_loss_count=max(0, ctx.session_loss_count + delta.session_loss_count_delta),
        session_risk_committed_r=max(
            0.0,
            ctx.session_risk_committed_r + delta.session_risk_committed_r_delta,
        ),
        current_session=delta.current_session or ctx.current_session,
        governor_state=delta.governor_state or ctx.governor_state,
        days_elapsed=ctx.days_elapsed,
        updated_at=updated_at,
    )


def reset_session_metrics(
    ctx: CompetitionContext,
    *,
    current_session: str,
    now: datetime,
) -> CompetitionContext:
    return CompetitionContext(
        total_days=ctx.total_days,
        start_equity=ctx.start_equity,
        starting_date=ctx.starting_date,
        total_pnl_r=ctx.total_pnl_r,
        daily_pnl_r=ctx.daily_pnl_r,
        session_pnl_r=0.0,
        session_loss_count=0,
        session_risk_committed_r=0.0,
        current_session=current_session,
        governor_state=GovernorState.NORMAL,
        days_elapsed=ctx.days_elapsed,
        updated_at=_to_utc(now),
    )


def apply_governor_bias(
    acute_aggression: AggressionState,
    bias: float,
    kill_switch: bool,
) -> AggressionState:
    if kill_switch:
        return AggressionState.DEFENSIVE
    if acute_aggression == AggressionState.DEFENSIVE:
        return AggressionState.DEFENSIVE
    if bias >= 0.4 and acute_aggression == AggressionState.NORMAL:
        return AggressionState.AGGRESSIVE
    if bias <= -0.4 and acute_aggression == AggressionState.AGGRESSIVE:
        return AggressionState.NORMAL
    if bias <= -0.6:
        return AggressionState.DEFENSIVE
    return acute_aggression


def _days_elapsed(ctx: CompetitionContext, now: datetime) -> int:
    current_date = _to_utc(now).date()
    return max(0, (current_date - ctx.starting_date).days)


def _is_sprint_phase(ctx: CompetitionContext, cfg: CompetitionConfig, now: datetime) -> bool:
    elapsed = _days_elapsed(ctx, now)
    progress = (elapsed + 1) / max(ctx.total_days, 1)
    return progress >= (1.0 - cfg.sprint_pct)


def _circuit_breaker_open(ctx: CompetitionContext, cfg: CompetitionConfig) -> bool:
    if ctx.current_session not in ACTIVE_SESSIONS:
        return False
    return (
        ctx.session_loss_count >= cfg.circuit_loss_count
        and ctx.session_pnl_r < cfg.circuit_session_pnl_r
    )


def _active_regime(regime: RegimeResult) -> bool:
    return regime.regime in {Regime.TREND, Regime.BREAKOUT}


def evaluate_governor(
    runtime: RuntimeState,
    regime: RegimeResult,
    cfg: CompetitionConfig,
    *,
    now: datetime,
) -> GovernorDirective:
    ctx = runtime.competition_ctx
    if ctx is None:
        raise ValueError("runtime.competition_ctx is required for governor evaluation")

    dd_active = runtime.aggression == AggressionState.DEFENSIVE or runtime.consecutive_losses >= runtime.risk_params.losses_defensive
    session_pause = _circuit_breaker_open(ctx, cfg)
    is_sprint = _is_sprint_phase(ctx, cfg, now)
    behind_pace = ctx.total_pnl_r < 0.0
    protect_lead = ctx.total_pnl_r >= cfg.lead_protect_r

    governor_state = GovernorState.NORMAL
    aggression_bias = 0.0
    threshold_modifier = 0.0
    session_risk_budget_r = cfg.session_risk_budget_r
    allow_aggressive_features = True
    note = ""

    if dd_active:
        governor_state = GovernorState.SURVIVE
        aggression_bias = -0.8
        threshold_modifier = 8.0
        session_risk_budget_r = min(session_risk_budget_r, 1.0)
        allow_aggressive_features = False
        note = "dd_or_loss_streak"
    elif session_pause:
        governor_state = GovernorState.PROTECT
        aggression_bias = cfg.protect_aggression_bias
        threshold_modifier = cfg.protect_threshold_modifier
        session_risk_budget_r = min(session_risk_budget_r, 0.5)
        allow_aggressive_features = False
        note = "session_circuit_breaker"
    elif is_sprint:
        governor_state = GovernorState.SPRINT
        aggression_bias = cfg.sprint_aggression_bias
        threshold_modifier = cfg.sprint_threshold_modifier
        session_risk_budget_r = cfg.session_risk_budget_r * 1.2
        allow_aggressive_features = True
        note = "final_sprint"
    elif protect_lead:
        governor_state = GovernorState.PROTECT
        aggression_bias = cfg.protect_aggression_bias
        threshold_modifier = cfg.protect_threshold_modifier
        session_risk_budget_r = min(session_risk_budget_r, 1.0)
        allow_aggressive_features = False
        note = "lead_protect"
    elif behind_pace and _active_regime(regime) and ctx.current_session in ACTIVE_SESSIONS:
        governor_state = GovernorState.HUNT
        aggression_bias = cfg.hunt_aggression_bias
        threshold_modifier = cfg.hunt_threshold_modifier
        session_risk_budget_r = cfg.session_risk_budget_r
        allow_aggressive_features = True
        note = "behind_pace_active_regime"

    return GovernorDirective(
        governor_state=governor_state,
        aggression_bias=_clamp(aggression_bias, -1.0, 1.0),
        threshold_modifier=_clamp(threshold_modifier, -10.0, 12.0),
        session_risk_budget_r=max(0.0, session_risk_budget_r),
        allow_aggressive_features=allow_aggressive_features,
        session_pause=session_pause,
        governor_note=note,
    )


__all__ = [
    "CompetitionContextDelta",
    "ACTIVE_SESSIONS",
    "apply_context_delta",
    "apply_governor_bias",
    "build_competition_context",
    "evaluate_governor",
    "reset_session_metrics",
]
