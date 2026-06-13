"""Risk engine for TSP V1."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from .data_pipeline import SymbolContract
from .state import (
    AggressionState,
    ConfidenceTier,
    Direction,
    ExecutionStatus,
    MarketSnapshot,
    Module,
    PositionState,
    Regime,
    RuntimeState,
    SignalScore,
)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _quantize_volume(raw_lot: float, contract: SymbolContract) -> float:
    step = Decimal(str(contract.volume_step))
    volume_min = Decimal(str(contract.volume_min))
    volume_max = Decimal(str(contract.volume_max))
    quantized = (
        Decimal(str(raw_lot)) / step
    ).to_integral_value(rounding=ROUND_DOWN) * step
    if quantized < volume_min:
        return 0.0
    if quantized > volume_max:
        quantized = volume_max
    return float(quantized)


@dataclass(frozen=True, slots=True)
class AggressionTransitionResult:
    new_state: AggressionState
    activate_kill: bool
    kill_reason: str


@dataclass(frozen=True, slots=True)
class PyramidCheckResult:
    allowed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class EmergencyExitDecision:
    should_exit: bool
    reason: str


@dataclass(frozen=True, slots=True)
class RiskDecision:
    action: str
    reason: str
    r_percent: float
    effective_equity: float
    lot_size: float
    entry_price: float
    invalidation_price: float
    allow_pyramid: bool


def compute_effective_equity(current_equity: float, starting_equity: float, equity_peak: float) -> float:
    floor = starting_equity * 0.70
    ceiling = max(equity_peak, floor)
    return _clamp(current_equity, floor, ceiling)


def compute_drawdown_pct(current_equity: float, equity_peak: float) -> float:
    if equity_peak <= 0.0:
        return 0.0
    return max(0.0, ((equity_peak - current_equity) / equity_peak) * 100.0)


def r_percent_for_tier(signal: SignalScore, runtime: RuntimeState) -> float:
    base = {
        ConfidenceTier.WEAK: runtime.risk_params.r_weak,
        ConfidenceTier.NORMAL: runtime.risk_params.r_normal,
        ConfidenceTier.GOOD: runtime.risk_params.r_good,
        ConfidenceTier.ELITE: runtime.risk_params.r_elite,
    }[signal.confidence_tier]
    multiplier = {
        AggressionState.DEFENSIVE: runtime.risk_params.mult_defensive,
        AggressionState.NORMAL: runtime.risk_params.mult_normal,
        AggressionState.AGGRESSIVE: runtime.risk_params.mult_aggressive,
    }[runtime.aggression]
    return min(base * multiplier, runtime.risk_params.r_max_single)


def compute_lot_size(
    entry: float,
    invalidation: float,
    r_percent: float,
    effective_equity: float,
    contract: SymbolContract,
) -> float:
    sl_distance = abs(entry - invalidation)
    if sl_distance <= 0.0 or contract.tick_size <= 0.0 or contract.tick_value <= 0.0:
        return 0.0
    risk_amount = effective_equity * (r_percent / 100.0)
    sl_in_ticks = sl_distance / contract.tick_size
    risk_per_lot = sl_in_ticks * contract.tick_value
    if risk_per_lot <= 0.0:
        return 0.0
    raw_lot = risk_amount / risk_per_lot
    return _quantize_volume(raw_lot, contract)


def evaluate_aggression_transition(runtime: RuntimeState) -> AggressionTransitionResult:
    dd_pct = compute_drawdown_pct(runtime.equity_current, runtime.equity_peak)
    params = runtime.risk_params

    if dd_pct >= params.dd_pct_kill:
        return AggressionTransitionResult(AggressionState.DEFENSIVE, True, "dd_kill")
    if runtime.consecutive_losses >= params.losses_kill:
        return AggressionTransitionResult(AggressionState.DEFENSIVE, True, "loss_streak_kill")

    if dd_pct >= params.dd_pct_defensive or runtime.consecutive_losses >= params.losses_defensive:
        return AggressionTransitionResult(AggressionState.DEFENSIVE, False, "")

    if runtime.aggression == AggressionState.DEFENSIVE:
        if dd_pct < 1.0 and runtime.consecutive_losses <= 1:
            return AggressionTransitionResult(AggressionState.NORMAL, False, "")
        return AggressionTransitionResult(AggressionState.DEFENSIVE, False, "")

    if runtime.aggression == AggressionState.AGGRESSIVE:
        if runtime.consecutive_losses >= 1 or dd_pct >= 1.0:
            return AggressionTransitionResult(AggressionState.NORMAL, False, "")
        return AggressionTransitionResult(AggressionState.AGGRESSIVE, False, "")

    if (
        runtime.consecutive_wins >= params.wins_for_aggressive
        and runtime.daily_pnl_r >= params.daily_r_for_aggressive
        and dd_pct < params.dd_max_for_aggressive
    ):
        return AggressionTransitionResult(AggressionState.AGGRESSIVE, False, "")

    return AggressionTransitionResult(AggressionState.NORMAL, False, "")


def check_pyramid_eligibility(
    position: PositionState,
    signal: SignalScore,
    new_aggression: AggressionState,
    runtime: RuntimeState,
) -> PyramidCheckResult:
    params = runtime.risk_params
    if position.layer_count >= params.pyramid_max_layers:
        return PyramidCheckResult(False, "max_layers_reached")
    if position.direction != signal.direction:
        return PyramidCheckResult(False, "direction_mismatch")
    if position.unrealized_pnl_r < params.pyramid_min_profit_r:
        return PyramidCheckResult(False, "insufficient_profit_r")
    if new_aggression == AggressionState.DEFENSIVE:
        return PyramidCheckResult(False, "defensive_aggression")
    if signal.confidence_tier not in {ConfidenceTier.GOOD, ConfidenceTier.ELITE}:
        return PyramidCheckResult(False, "confidence_tier_too_low")
    new_r = r_percent_for_tier(signal, runtime)
    if (position.total_r_risk + new_r) > params.pyramid_aggregate_cap:
        return PyramidCheckResult(False, "aggregate_r_cap")
    return PyramidCheckResult(True, "")


def evaluate_emergency_exit(
    runtime: RuntimeState,
    snap: MarketSnapshot,
    *,
    spread_persist_bars: int,
) -> EmergencyExitDecision:
    if runtime.position.layer_count == 0:
        return EmergencyExitDecision(False, "")
    if runtime.kill_switch_active:
        return EmergencyExitDecision(True, "kill_switch_active")

    pnl_r = runtime.position.unrealized_pnl_r
    fresh_trade = any(layer.bars_in_trade < 3 for layer in runtime.position.layers)
    near_breakeven = pnl_r < 0.30
    deep_loser = pnl_r < -0.50
    protected_runner = pnl_r >= 0.30 and not fresh_trade

    dd_pct = compute_drawdown_pct(runtime.equity_current, runtime.equity_peak)
    if dd_pct >= runtime.risk_params.dd_pct_kill:
        return EmergencyExitDecision(True, "drawdown_kill")

    spread_ratio = snap.spread_current / snap.spread_baseline if snap.spread_baseline > 0 else 0.0
    if spread_ratio > 3.5 and spread_persist_bars >= 2:
        if deep_loser or protected_runner:
            return EmergencyExitDecision(False, "")
        return EmergencyExitDecision(True, "spread_persist")

    if snap.is_news_window:
        if deep_loser:
            return EmergencyExitDecision(False, "")
        if near_breakeven or fresh_trade:
            return EmergencyExitDecision(True, "news_window")

    return EmergencyExitDecision(False, "")


def _invalidation_for_signal(signal: SignalScore) -> float:
    return signal.invalidation_anchor


def evaluate_risk(
    signal: SignalScore,
    snap: MarketSnapshot,
    runtime: RuntimeState,
    contract: SymbolContract,
) -> RiskDecision:
    effective_equity = compute_effective_equity(
        runtime.equity_current,
        runtime.starting_equity,
        runtime.equity_peak,
    )

    entry_price = snap.ask if signal.direction == Direction.LONG else snap.bid
    invalidation_price = _invalidation_for_signal(signal)
    if runtime.kill_switch_active:
        return RiskDecision(
            action="BLOCK",
            reason="kill_switch_active",
            r_percent=0.0,
            effective_equity=effective_equity,
            lot_size=0.0,
            entry_price=entry_price,
            invalidation_price=invalidation_price,
            allow_pyramid=False,
        )

    if runtime.position.layer_count > 0:
        pyramid = check_pyramid_eligibility(runtime.position, signal, runtime.aggression, runtime)
        if not pyramid.allowed:
            return RiskDecision(
                action="BLOCK",
                reason=pyramid.reason,
                r_percent=0.0,
                effective_equity=effective_equity,
                lot_size=0.0,
                entry_price=entry_price,
                invalidation_price=invalidation_price,
                allow_pyramid=False,
            )
        r_percent = r_percent_for_tier(signal, runtime)
        lot_size = compute_lot_size(entry_price, invalidation_price, r_percent, effective_equity, contract)
        if lot_size <= 0.0:
            return RiskDecision(
                action="BLOCK",
                reason="lot_size_below_min",
                r_percent=r_percent,
                effective_equity=effective_equity,
                lot_size=0.0,
                entry_price=entry_price,
                invalidation_price=invalidation_price,
                allow_pyramid=True,
            )
        return RiskDecision(
            action="PYRAMID",
            reason="eligible",
            r_percent=r_percent,
            effective_equity=effective_equity,
            lot_size=lot_size,
            entry_price=entry_price,
            invalidation_price=invalidation_price,
            allow_pyramid=True,
        )

    r_percent = r_percent_for_tier(signal, runtime)
    lot_size = compute_lot_size(entry_price, invalidation_price, r_percent, effective_equity, contract)
    if lot_size <= 0.0:
        return RiskDecision(
            action="BLOCK",
            reason="lot_size_below_min",
            r_percent=r_percent,
            effective_equity=effective_equity,
            lot_size=0.0,
            entry_price=entry_price,
            invalidation_price=invalidation_price,
            allow_pyramid=False,
        )
    return RiskDecision(
        action="ENTER",
        reason="eligible",
        r_percent=r_percent,
        effective_equity=effective_equity,
        lot_size=lot_size,
        entry_price=entry_price,
        invalidation_price=invalidation_price,
        allow_pyramid=False,
    )


__all__ = [
    "AggressionTransitionResult",
    "EmergencyExitDecision",
    "PyramidCheckResult",
    "RiskDecision",
    "check_pyramid_eligibility",
    "compute_drawdown_pct",
    "compute_effective_equity",
    "compute_lot_size",
    "evaluate_aggression_transition",
    "evaluate_emergency_exit",
    "evaluate_risk",
    "r_percent_for_tier",
]
