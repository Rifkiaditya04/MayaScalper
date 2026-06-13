"""Competition governor for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .enums import HealthState, PaceClassification, ProfileName, GovernorState
from .models import GovernorContext, GovernorDecision, MarketSnapshot


STARVATION_THRESHOLD_MINUTES = 180.0
PACE_BEHIND_THRESHOLD = 0.85
PACE_AHEAD_THRESHOLD = 1.15

PROFILE_PACE_CURVES: dict[ProfileName, tuple[tuple[float, float], ...]] = {
    ProfileName.FORWARD_SAFE: ((0.0, 0.0), (0.25, 0.5), (0.5, 1.0), (0.75, 1.5), (1.0, 2.0)),
    ProfileName.CONTEST_BALANCED: ((0.0, 0.0), (0.25, 0.7), (0.5, 1.2), (0.75, 1.8), (1.0, 2.35)),
    ProfileName.CONTEST_HUNTER: ((0.0, 0.0), (0.25, 0.85), (0.5, 1.55), (0.75, 2.25), (1.0, 2.85)),
    ProfileName.FINAL_SPRINT: ((0.0, 0.0), (0.25, 0.35), (0.5, 1.0), (0.75, 2.0), (1.0, 3.0)),
    ProfileName.DIAGNOSTIC: ((0.0, 0.0), (0.25, 0.1), (0.5, 0.2), (0.75, 0.3), (1.0, 0.4)),
}

STATE_PRIORITY = (
    GovernorState.KILL_REVIEW,
    GovernorState.SURVIVE,
    GovernorState.PROTECT,
    GovernorState.SPRINT,
    GovernorState.CHASE,
    GovernorState.HUNTER,
    GovernorState.ATTACK,
    GovernorState.NORMAL,
)


def evaluate_governor(snapshot: MarketSnapshot, context: GovernorContext | None = None) -> GovernorDecision:
    ctx = context or _default_context(snapshot)
    pace_ratio = _resolve_pace_ratio(ctx)
    pace_classification = _classify_pace(pace_ratio)
    drawdown_from_peak_pct = _drawdown_from_peak(ctx.equity, ctx.peak_equity)
    starvation_trigger = _starvation_trigger(ctx)

    state, reason, flags = _resolve_state(
        ctx=ctx,
        drawdown_from_peak_pct=drawdown_from_peak_pct,
        pace_ratio=pace_ratio,
        pace_classification=pace_classification,
        starvation_trigger=starvation_trigger,
    )

    return GovernorDecision(
        state=state,
        state_reason=reason,
        pace_classification=pace_classification,
        aggression_multiplier=_aggression_multiplier(state),
        profile_constraints={
            "profile": (ctx.profile.value if ctx.profile is not None else ProfileName.FORWARD_SAFE.value),
            "contest_elapsed_pct": round(ctx.contest_elapsed_pct, 3),
            "pace_ratio": round(pace_ratio, 6),
            "drawdown_from_peak_pct": round(drawdown_from_peak_pct, 6),
            "expected_pnl_r_at_t": round(_expected_pnl_r_at_t(ctx), 6),
        },
        escalation_flags=flags,
    )


def _resolve_state(
    *,
    ctx: GovernorContext,
    drawdown_from_peak_pct: float,
    pace_ratio: float,
    pace_classification: PaceClassification,
    starvation_trigger: bool,
) -> tuple[GovernorState, str, tuple[str, ...]]:
    flags: list[str] = []

    if ctx.drawdown_pct >= 20.0 or ctx.daily_loss_pct >= 20.0:
        flags.append("hard_shutdown")
        return GovernorState.KILL_REVIEW, "hard_shutdown", tuple(flags)
    if ctx.drawdown_pct >= 15.0 or ctx.daily_loss_pct >= 15.0 or not ctx.broker_stable:
        flags.append("kill_review_trigger")
        if not ctx.broker_stable:
            flags.append("broker_instability")
        return GovernorState.KILL_REVIEW, "kill_review", tuple(flags)

    if (
        ctx.drawdown_pct > 10.0
        or ctx.daily_loss_pct > 10.0
        or ctx.execution_health is not HealthState.GREEN
        or ctx.execution_anomaly_cluster
        or ctx.recovery_uncertainty
    ):
        if ctx.execution_health is not HealthState.GREEN:
            flags.append("execution_degraded")
        if ctx.execution_anomaly_cluster:
            flags.append("anomaly_cluster")
        if ctx.recovery_uncertainty:
            flags.append("recovery_uncertainty")
        return GovernorState.SURVIVE, "survive_protective", tuple(flags)

    if drawdown_from_peak_pct >= 3.0 and ctx.peak_equity > ctx.equity:
        flags.append("protect_trigger")
        return GovernorState.PROTECT, "protect_peak", tuple(flags)

    if ctx.contest_elapsed_pct > 85.0 and ctx.drawdown_pct < 8.0 and ctx.execution_health is HealthState.GREEN:
        flags.append("sprint_window")
        return GovernorState.SPRINT, "sprint_window", tuple(flags)

    if (
        (ctx.contest_elapsed_pct > 60.0 and pace_ratio < PACE_BEHIND_THRESHOLD)
        or starvation_trigger
        or (ctx.ranking_proxy_available and pace_classification is PaceClassification.BEHIND)
    ) and ctx.execution_health is HealthState.GREEN and ctx.feed_health is HealthState.GREEN and ctx.news_clear:
        if starvation_trigger:
            flags.append("starvation_escalation")
        if pace_classification is PaceClassification.BEHIND:
            flags.append("ranking_proxy_underperformance")
        return GovernorState.CHASE, "chase_pressure", tuple(flags)

    if ctx.contest_elapsed_pct > 15.0 and ctx.drawdown_pct < 5.0 and ctx.execution_health is HealthState.GREEN and _signal_density_healthy(ctx.signal_density):
        flags.append("hunter_window")
        return GovernorState.HUNTER, "hunter_window", tuple(flags)

    if ctx.contest_elapsed_pct > 10.0 and ctx.drawdown_pct < 4.0 and _signal_density_healthy(ctx.signal_density):
        flags.append("attack_window")
        return GovernorState.ATTACK, "attack_window", tuple(flags)

    if ctx.drawdown_pct < 3.0 and ctx.execution_health is HealthState.GREEN and not starvation_trigger:
        if pace_classification is PaceClassification.AHEAD:
            flags.append("ahead_pace")
        return GovernorState.NORMAL, "normal", tuple(flags)

    if starvation_trigger and ctx.execution_health is HealthState.GREEN and ctx.feed_health is HealthState.GREEN and ctx.news_clear:
        flags.append("starvation_escalation")
        return GovernorState.CHASE, "starvation_only", tuple(flags)

    return GovernorState.NORMAL, "fallback_normal", tuple(flags)


def _resolve_pace_ratio(ctx: GovernorContext) -> float:
    if ctx.ranking_proxy_available and ctx.ranking_proxy_pace_ratio is not None:
        return max(0.0, ctx.ranking_proxy_pace_ratio)
    expected = _expected_pnl_r_at_t(ctx)
    if expected <= 1e-8:
        return 1.0 if ctx.realized_pnl_r >= 0.0 else 0.0
    return ctx.realized_pnl_r / expected


def _expected_pnl_r_at_t(ctx: GovernorContext) -> float:
    profile = ctx.profile or ProfileName.FORWARD_SAFE
    curve = PROFILE_PACE_CURVES.get(profile, PROFILE_PACE_CURVES[ProfileName.FORWARD_SAFE])
    progress = _clamp(ctx.contest_elapsed_pct / 100.0)
    return _piecewise_linear(progress, curve)


def _classify_pace(pace_ratio: float) -> PaceClassification:
    if pace_ratio < PACE_BEHIND_THRESHOLD:
        return PaceClassification.BEHIND
    if pace_ratio > PACE_AHEAD_THRESHOLD:
        return PaceClassification.AHEAD
    return PaceClassification.ON_TRACK


def _drawdown_from_peak(equity: float, peak_equity: float) -> float:
    if peak_equity <= 0.0:
        return 0.0
    return max(0.0, ((peak_equity - equity) / peak_equity) * 100.0)


def _starvation_trigger(ctx: GovernorContext) -> bool:
    return (
        ctx.opportunity_starvation_minutes >= STARVATION_THRESHOLD_MINUTES
        and ctx.execution_health is HealthState.GREEN
        and ctx.feed_health is HealthState.GREEN
        and ctx.news_clear
    )


def _signal_density_healthy(signal_density: float) -> bool:
    return signal_density >= 0.50


def _aggression_multiplier(state: GovernorState) -> float:
    return {
        GovernorState.SURVIVE: 0.45,
        GovernorState.NORMAL: 1.00,
        GovernorState.ATTACK: 1.10,
        GovernorState.HUNTER: 1.20,
        GovernorState.CHASE: 1.30,
        GovernorState.SPRINT: 1.40,
        GovernorState.PROTECT: 0.70,
        GovernorState.KILL_REVIEW: 0.0,
    }[state]


def _piecewise_linear(progress: float, points: tuple[tuple[float, float], ...]) -> float:
    if not points:
        return 0.0
    ordered = tuple(sorted(points, key=lambda item: item[0]))
    if progress <= ordered[0][0]:
        return ordered[0][1]
    for idx in range(1, len(ordered)):
        left_x, left_y = ordered[idx - 1]
        right_x, right_y = ordered[idx]
        if progress <= right_x:
            span = right_x - left_x
            if span <= 1e-8:
                return right_y
            ratio = (progress - left_x) / span
            return left_y + (ratio * (right_y - left_y))
    return ordered[-1][1]


def _default_context(snapshot: MarketSnapshot) -> GovernorContext:
    return GovernorContext(
        contest_elapsed_pct=0.0,
        equity=100_000.0,
        peak_equity=100_000.0,
        drawdown_pct=0.0,
        daily_loss_pct=0.0,
        realized_pnl_r=0.0,
        signal_density=0.0,
        execution_health=snapshot.feed_health,
        feed_health=snapshot.feed_health,
        opportunity_starvation_minutes=0.0,
        recovery_momentum=0.0,
    )


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))
