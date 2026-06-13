"""Pure risk engine for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor

from .enums import Direction, GovernorState, HealthState, RiskAction, SessionName
from .models import GovernorDecision, MarketSnapshot, PositionSnapshot, RiskContext, RiskDecision, SignalDecision


BASE_RISK_BY_GOVERNOR: dict[GovernorState, float] = {
    GovernorState.SURVIVE: 0.35,
    GovernorState.NORMAL: 0.75,
    GovernorState.ATTACK: 1.00,
    GovernorState.HUNTER: 1.25,
    GovernorState.CHASE: 1.50,
    GovernorState.SPRINT: 1.75,
    GovernorState.PROTECT: 0.35,
    GovernorState.KILL_REVIEW: 0.0,
}

MAX_RISK_PER_TRADE_PCT = 2.25
PORTFOLIO_RISK_CAP_PCT = 3.50
MAX_CONCURRENT_POSITIONS = 2
PYRAMID_TRIGGER_R = 0.75
MAX_PYRAMIDS_PER_THESIS = 1

SESSION_MULTIPLIER: dict[SessionName, float] = {
    SessionName.LONDON_NY: 1.00,
    SessionName.LONDON: 0.95,
    SessionName.EARLY_NY: 0.90,
    SessionName.LATE_NY: 0.75,
    SessionName.ASIA: 0.60,
    SessionName.DEAD: 0.00,
}

SYMBOL_MULTIPLIER: dict[str, float] = {
    "XAUUSD": 0.90,
    "GBPJPY": 0.85,
}

CORRELATION_GROUP_CAPS: dict[str, float] = {
    "GBPUSD_EURUSD": 2.25,
    "GBPJPY_COMBOS": 3.00,
}


@dataclass(frozen=True, slots=True)
class _RiskProfile:
    base_risk_pct: float
    governor_multiplier: float
    confidence_multiplier: float
    regime_multiplier: float
    session_multiplier: float
    drawdown_multiplier: float
    symbol_multiplier: float

    @property
    def composite_multiplier(self) -> float:
        return (
            self.governor_multiplier
            * self.confidence_multiplier
            * self.regime_multiplier
            * self.session_multiplier
            * self.drawdown_multiplier
            * self.symbol_multiplier
        )

    @property
    def target_risk_pct(self) -> float:
        return self.base_risk_pct * self.composite_multiplier


def evaluate_risk(
    snapshot: MarketSnapshot,
    signal: SignalDecision,
    governor: GovernorDecision,
    *,
    context: RiskContext | None = None,
) -> RiskDecision:
    ctx = context or RiskContext()
    open_positions = tuple(ctx.open_positions)
    derived_portfolio_risk = sum(position.risk_pct for position in open_positions)
    derived_symbol_risk = sum(position.risk_pct for position in open_positions if position.symbol == snapshot.symbol)
    correlation_group = _correlation_group(snapshot.symbol)
    derived_correlation_risk = sum(
        position.risk_pct
        for position in open_positions
        if _correlation_group(position.symbol) == correlation_group
    )

    profile = _build_risk_profile(snapshot=snapshot, signal=signal, governor=governor, context=ctx)
    diagnostics = {
        "base_risk_pct": round(profile.base_risk_pct, 6),
        "governor_multiplier": round(profile.governor_multiplier, 6),
        "confidence_multiplier": round(profile.confidence_multiplier, 6),
        "regime_multiplier": round(profile.regime_multiplier, 6),
        "session_multiplier": round(profile.session_multiplier, 6),
        "drawdown_multiplier": round(profile.drawdown_multiplier, 6),
        "symbol_multiplier": round(profile.symbol_multiplier, 6),
        "composite_multiplier": round(profile.composite_multiplier, 6),
        "target_risk_pct": round(profile.target_risk_pct, 6),
        "portfolio_risk_pct": round(derived_portfolio_risk, 6),
        "symbol_risk_pct": round(derived_symbol_risk, 6),
        "correlation_risk_pct": round(derived_correlation_risk, 6),
        "correlation_group": correlation_group,
        "open_positions": len(open_positions),
        "current_drawdown_pct": round(ctx.current_drawdown_pct, 6),
        "current_daily_loss_pct": round(ctx.current_daily_loss_pct, 6),
        "current_unrealized_r": round(ctx.current_unrealized_r, 6),
        "loss_streak": ctx.loss_streak,
    }

    if signal.expires_at_utc <= snapshot.cycle_time_utc:
        return _decision(
            action=RiskAction.BLOCK,
            risk_multiplier=0.0,
            sized_volume=0.0,
            invalidation_price=0.0,
            hard_block_reason="stale_signal",
            governor_adjusted_state=_adjusted_state_for_drawdown(governor.state, ctx),
            diagnostics=diagnostics,
        )

    emergency_reason = _emergency_reason(ctx)
    if emergency_reason is not None:
        return _decision(
            action=RiskAction.EMERGENCY_EXIT,
            risk_multiplier=0.0,
            sized_volume=0.0,
            invalidation_price=0.0,
            hard_block_reason=emergency_reason,
            governor_adjusted_state=GovernorState.KILL_REVIEW,
            diagnostics=diagnostics,
        )

    if ctx.current_drawdown_pct >= 15.0 or ctx.current_daily_loss_pct >= 15.0:
        action = RiskAction.REDUCE if open_positions else RiskAction.BLOCK
        return _decision(
            action=action,
            risk_multiplier=profile.composite_multiplier,
            sized_volume=0.0,
            invalidation_price=_compute_invalidation_price(snapshot, signal),
            hard_block_reason="kill_review",
            governor_adjusted_state=GovernorState.KILL_REVIEW,
            diagnostics=diagnostics,
        )

    if ctx.current_drawdown_pct > 10.0 or ctx.current_daily_loss_pct > 10.0:
        action = RiskAction.REDUCE if open_positions else RiskAction.BLOCK
        return _decision(
            action=action,
            risk_multiplier=profile.composite_multiplier,
            sized_volume=0.0,
            invalidation_price=_compute_invalidation_price(snapshot, signal),
            hard_block_reason="survive_posture",
            governor_adjusted_state=GovernorState.SURVIVE,
            diagnostics=diagnostics,
        )

    if _anti_revenge_block(snapshot=snapshot, signal=signal, context=ctx, open_positions=open_positions):
        return _decision(
            action=RiskAction.BLOCK,
            risk_multiplier=0.0,
            sized_volume=0.0,
            invalidation_price=_compute_invalidation_price(snapshot, signal),
            hard_block_reason="anti_revenge_block",
            governor_adjusted_state=_adjusted_state_for_drawdown(governor.state, ctx),
            diagnostics=diagnostics,
        )

    if len(open_positions) >= MAX_CONCURRENT_POSITIONS and not _pyramid_eligible(
        snapshot=snapshot,
        signal=signal,
        context=ctx,
        open_positions=open_positions,
    ):
        return _decision(
            action=RiskAction.BLOCK,
            risk_multiplier=0.0,
            sized_volume=0.0,
            invalidation_price=_compute_invalidation_price(snapshot, signal),
            hard_block_reason="max_positions",
            governor_adjusted_state=governor.state,
            diagnostics=diagnostics,
        )

    target_risk_pct = min(profile.target_risk_pct, MAX_RISK_PER_TRADE_PCT, _available_portfolio_budget(derived_portfolio_risk))
    target_risk_pct = min(target_risk_pct, _available_correlation_budget(derived_correlation_risk, correlation_group))
    if target_risk_pct <= 0.0:
        return _decision(
            action=RiskAction.BLOCK,
            risk_multiplier=0.0,
            sized_volume=0.0,
            invalidation_price=_compute_invalidation_price(snapshot, signal),
            hard_block_reason="cap_exhausted",
            governor_adjusted_state=governor.state,
            diagnostics=diagnostics,
        )

    if target_risk_pct < profile.target_risk_pct * 0.75:
        action = RiskAction.REDUCE if open_positions else RiskAction.SCALE
    elif _pyramid_eligible(snapshot=snapshot, signal=signal, context=ctx, open_positions=open_positions):
        action = RiskAction.PYRAMID
    elif open_positions:
        action = RiskAction.SCALE
    else:
        action = RiskAction.ENTER

    invalidation_price = _compute_invalidation_price(snapshot, signal)
    sized_volume = _size_volume(
        snapshot=snapshot,
        target_risk_pct=target_risk_pct,
        invalidation_price=invalidation_price,
        account_equity=ctx.account_equity,
    )
    if sized_volume <= 0.0:
        return _decision(
            action=RiskAction.BLOCK,
            risk_multiplier=profile.composite_multiplier,
            sized_volume=0.0,
            invalidation_price=invalidation_price,
            hard_block_reason="insufficient_risk_budget",
            governor_adjusted_state=governor.state,
            diagnostics=diagnostics,
        )

    return _decision(
        action=action,
        risk_multiplier=profile.composite_multiplier,
        sized_volume=sized_volume,
        invalidation_price=invalidation_price,
        hard_block_reason="",
        governor_adjusted_state=governor.state,
        diagnostics=diagnostics,
    )


def _build_risk_profile(
    *,
    snapshot: MarketSnapshot,
    signal: SignalDecision,
    governor: GovernorDecision,
    context: RiskContext,
) -> _RiskProfile:
    base_risk_pct = BASE_RISK_BY_GOVERNOR.get(governor.state, 0.75)
    governor_multiplier = 0.0 if base_risk_pct <= 0.0 else base_risk_pct / BASE_RISK_BY_GOVERNOR[GovernorState.NORMAL]
    confidence_multiplier = _clamp(0.80 + 0.40 * _clamp(signal.score))
    regime_multiplier = _clamp(0.90 + 0.20 * _clamp(signal.score if governor.state is GovernorState.ATTACK else signal.threshold))
    session_multiplier = SESSION_MULTIPLIER.get(snapshot.session, 0.0)
    drawdown_multiplier = _drawdown_multiplier(context.current_drawdown_pct)
    symbol_multiplier = SYMBOL_MULTIPLIER.get(snapshot.symbol.upper(), 1.0)
    return _RiskProfile(
        base_risk_pct=base_risk_pct,
        governor_multiplier=governor_multiplier,
        confidence_multiplier=confidence_multiplier,
        regime_multiplier=regime_multiplier,
        session_multiplier=session_multiplier,
        drawdown_multiplier=drawdown_multiplier,
        symbol_multiplier=symbol_multiplier,
    )


def _drawdown_multiplier(drawdown_pct: float) -> float:
    if drawdown_pct <= 5.0:
        return 1.0
    if drawdown_pct <= 10.0:
        return 0.80
    if drawdown_pct <= 15.0:
        return 0.55
    if drawdown_pct <= 20.0:
        return 0.25
    return 0.0


def _emergency_reason(context: RiskContext) -> str | None:
    if not context.broker_stable:
        return "broker_instability"
    if context.recovery_uncertainty:
        return "recovery_uncertainty"
    if context.execution_anomaly_cluster:
        return "execution_anomaly_cluster"
    if context.current_drawdown_pct >= 20.0:
        return "hard_shutdown_drawdown"
    if context.current_daily_loss_pct >= 20.0:
        return "hard_shutdown_daily_loss"
    return None


def _adjusted_state_for_drawdown(governor_state: GovernorState, context: RiskContext) -> GovernorState:
    if context.current_drawdown_pct > 10.0 or context.current_daily_loss_pct > 10.0:
        return GovernorState.SURVIVE
    return governor_state


def _available_portfolio_budget(current_portfolio_risk_pct: float) -> float:
    return max(0.0, PORTFOLIO_RISK_CAP_PCT - current_portfolio_risk_pct)


def _available_correlation_budget(current_group_risk_pct: float, correlation_group: str) -> float:
    cap = CORRELATION_GROUP_CAPS.get(correlation_group, PORTFOLIO_RISK_CAP_PCT)
    return max(0.0, cap - current_group_risk_pct)


def _anti_revenge_block(
    *,
    snapshot: MarketSnapshot,
    signal: SignalDecision,
    context: RiskContext,
    open_positions: tuple[PositionSnapshot, ...],
) -> bool:
    if context.current_unrealized_r >= 0.0 and context.loss_streak <= 0:
        return False
    same_symbol_positions = [position for position in open_positions if position.symbol == snapshot.symbol]
    if not same_symbol_positions:
        return False
    if any(position.direction != signal.direction for position in same_symbol_positions):
        return False
    return True


def _pyramid_eligible(
    *,
    snapshot: MarketSnapshot,
    signal: SignalDecision,
    context: RiskContext,
    open_positions: tuple[PositionSnapshot, ...],
) -> bool:
    same_thesis_positions = [position for position in open_positions if position.setup_id == signal.setup_id]
    if not same_thesis_positions:
        return False
    if len(same_thesis_positions) > MAX_PYRAMIDS_PER_THESIS:
        return False
    if context.current_unrealized_r < PYRAMID_TRIGGER_R:
        return False
    if context.execution_health is not HealthState.GREEN:
        return False
    if context.spread_health is not HealthState.GREEN:
        return False
    if context.latency_health is not HealthState.GREEN:
        return False
    if any(position.direction != signal.direction for position in same_thesis_positions):
        return False
    if any(position.symbol != snapshot.symbol for position in same_thesis_positions):
        return False
    return True


def _compute_invalidation_price(snapshot: MarketSnapshot, signal: SignalDecision) -> float:
    bars = snapshot.bars_m5[-3:]
    entry = _mid_price(snapshot)
    atr_m5 = float(snapshot.indicator_bundle.get("atr_m5", 0.0))
    cushion = max(atr_m5 * 0.25, snapshot.contract.point * 5.0)
    if signal.direction is Direction.LONG:
        structural_low = min(float(bar["low"]) for bar in bars)
        return structural_low - cushion
    structural_high = max(float(bar["high"]) for bar in bars)
    return structural_high + cushion


def _size_volume(
    *,
    snapshot: MarketSnapshot,
    target_risk_pct: float,
    invalidation_price: float,
    account_equity: float,
) -> float:
    if account_equity <= 0.0:
        return 0.0
    entry_price = _mid_price(snapshot)
    stop_distance = abs(entry_price - invalidation_price)
    if stop_distance <= 0.0:
        return 0.0
    contract = snapshot.contract
    if contract.tick_size <= 0.0 or contract.tick_value <= 0.0:
        return 0.0
    risk_cash = account_equity * (target_risk_pct / 100.0)
    risk_per_lot = (stop_distance / contract.tick_size) * contract.tick_value
    if risk_per_lot <= 0.0:
        return 0.0
    raw_volume = risk_cash / risk_per_lot
    if raw_volume < contract.min_lot:
        return 0.0
    stepped_volume = _floor_to_step(raw_volume, contract.lot_step)
    return min(contract.max_lot, max(contract.min_lot, stepped_volume))


def _mid_price(snapshot: MarketSnapshot) -> float:
    return (snapshot.tick_bid + snapshot.tick_ask) / 2.0


def _floor_to_step(value: float, step: float) -> float:
    if step <= 0.0:
        return value
    return floor(value / step) * step


def _correlation_group(symbol: str) -> str:
    canonical = symbol.upper()
    if canonical in {"GBPUSD", "EURUSD"}:
        return "GBPUSD_EURUSD"
    if canonical == "GBPJPY":
        return "GBPJPY_COMBOS"
    if canonical.endswith("JPY") and canonical.startswith("GBP"):
        return "GBPJPY_COMBOS"
    return canonical


def _decision(
    *,
    action: RiskAction,
    risk_multiplier: float,
    sized_volume: float,
    invalidation_price: float,
    hard_block_reason: str,
    governor_adjusted_state: GovernorState,
    diagnostics: dict[str, float | int | str],
) -> RiskDecision:
    return RiskDecision(
        action=action,
        risk_multiplier=round(risk_multiplier, 6),
        sized_volume=round(sized_volume, 2),
        invalidation_price=round(invalidation_price, 5),
        hard_block_reason=hard_block_reason,
        governor_adjusted_state=governor_adjusted_state,
        diagnostics=diagnostics,
    )


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))
