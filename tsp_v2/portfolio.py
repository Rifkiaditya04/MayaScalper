"""Portfolio ranking for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enums import HealthState, SignalFamily
from .models import PortfolioContext, PositionSnapshot, SignalDecision


PORTFOLIO_RISK_CAP_PCT = 3.50
MAX_CONCURRENT_POSITIONS = 2
REPLACEMENT_SUPERIORITY = 0.12
SYMBOL_COOLDOWN_MINUTES = 5.0

CORRELATION_GROUP_CAPS: dict[str, float] = {
    "GBPUSD_EURUSD": 2.25,
    "GBPJPY_COMBOS": 3.00,
}


@dataclass(frozen=True, slots=True)
class PortfolioSelectionTrace:
    setup_id: str
    symbol: str
    score: float
    threshold: float
    selected: bool
    first_rejection_reason: str | None = None


def rank_opportunities(
    signals: list[SignalDecision],
    *,
    context: PortfolioContext | None = None,
) -> list[SignalDecision]:
    selected, _ = trace_rank_opportunities(signals, context=context)
    return selected


def trace_rank_opportunities(
    signals: list[SignalDecision],
    *,
    context: PortfolioContext | None = None,
) -> tuple[list[SignalDecision], tuple[PortfolioSelectionTrace, ...]]:
    ctx = context or PortfolioContext()
    active_positions = tuple(ctx.active_positions)
    current_time = ctx.current_time_utc
    ranked = sorted(
        signals,
        key=lambda signal: (
            -signal.score,
            signal.threshold,
            signal.expires_at_utc,
            signal.setup_id,
        ),
    )
    selected: list[SignalDecision] = []
    selected_symbols: set[str] = set()
    traces: list[PortfolioSelectionTrace] = []
    active_group_risk = _active_group_risk(active_positions)
    weakest_active = _weakest_active_position(active_positions)

    for index, signal in enumerate(ranked):
        if len(selected) >= MAX_CONCURRENT_POSITIONS:
            for remaining in ranked[index:]:
                traces.append(
                    PortfolioSelectionTrace(
                        setup_id=remaining.setup_id,
                        symbol=remaining.symbol,
                        score=remaining.score,
                        threshold=remaining.threshold,
                        selected=False,
                        first_rejection_reason="CAPACITY_LIMIT_REACHED",
                    )
                )
            break
        rejection_reason: str | None = None
        if _is_expired(signal, current_time):
            rejection_reason = "EXPIRED"
        elif _is_on_cooldown(signal.symbol, current_time, ctx.symbol_cooldown_until_utc):
            rejection_reason = "SYMBOL_COOLDOWN"
        elif signal.symbol in selected_symbols:
            rejection_reason = "DUPLICATE_SYMBOL"
        elif ctx.execution_health is not HealthState.GREEN:
            rejection_reason = "EXECUTION_HEALTH"
        elif ctx.spread_health is not HealthState.GREEN:
            rejection_reason = "SPREAD_HEALTH"
        elif ctx.latency_health is not HealthState.GREEN:
            rejection_reason = "LATENCY_HEALTH"
        elif not _correlation_budget_allows(signal, active_group_risk):
            rejection_reason = "CORRELATION_BUDGET"
        elif len(active_positions) >= MAX_CONCURRENT_POSITIONS and not _replacement_allowed(signal, weakest_active, ctx):
            rejection_reason = "REPLACEMENT_NOT_ALLOWED"

        if rejection_reason is not None:
            traces.append(
                PortfolioSelectionTrace(
                    setup_id=signal.setup_id,
                    symbol=signal.symbol,
                    score=signal.score,
                    threshold=signal.threshold,
                    selected=False,
                    first_rejection_reason=rejection_reason,
                )
            )
            continue
        selected.append(signal)
        selected_symbols.add(signal.symbol)
        traces.append(
            PortfolioSelectionTrace(
                setup_id=signal.setup_id,
                symbol=signal.symbol,
                score=signal.score,
                threshold=signal.threshold,
                selected=True,
                first_rejection_reason=None,
            )
        )

    return selected, tuple(traces)


def _replacement_allowed(
    candidate: SignalDecision,
    weakest_active: PositionSnapshot | None,
    context: PortfolioContext,
) -> bool:
    if weakest_active is None:
        return True
    if candidate.score - weakest_active.signal_score < context.replacement_superiority:
        return False
    if candidate.direction != weakest_active.direction:
        return False
    return True


def _active_group_risk(active_positions: tuple[PositionSnapshot, ...]) -> dict[str, float]:
    group_risk: dict[str, float] = {}
    for position in active_positions:
        group_risk[position.correlation_group] = group_risk.get(position.correlation_group, 0.0) + position.risk_pct
    return group_risk


def _weakest_active_position(active_positions: tuple[PositionSnapshot, ...]) -> PositionSnapshot | None:
    if not active_positions:
        return None
    return min(active_positions, key=lambda position: (position.signal_score, position.risk_pct))


def _correlation_budget_allows(signal: SignalDecision, active_group_risk: dict[str, float]) -> bool:
    group = _correlation_group(signal.symbol)
    cap = CORRELATION_GROUP_CAPS.get(group, PORTFOLIO_RISK_CAP_PCT)
    return active_group_risk.get(group, 0.0) < cap


def _is_expired(signal: SignalDecision, current_time: datetime | None) -> bool:
    if current_time is None:
        return False
    return signal.expires_at_utc <= current_time


def _is_on_cooldown(
    symbol: str,
    current_time: datetime | None,
    cooldown_until_utc: dict[str, datetime],
) -> bool:
    if current_time is None:
        return False
    cooldown = cooldown_until_utc.get(symbol.upper())
    return cooldown is not None and cooldown > current_time


def _correlation_group(symbol: str) -> str:
    canonical = symbol.upper()
    if canonical in {"GBPUSD", "EURUSD"}:
        return "GBPUSD_EURUSD"
    if canonical == "GBPJPY":
        return "GBPJPY_COMBOS"
    if canonical.endswith("JPY") and canonical.startswith("GBP"):
        return "GBPJPY_COMBOS"
    return canonical
