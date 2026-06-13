"""Portfolio ranking for TSP V2."""

from __future__ import annotations

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


def rank_opportunities(
    signals: list[SignalDecision],
    *,
    context: PortfolioContext | None = None,
) -> list[SignalDecision]:
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
    active_group_risk = _active_group_risk(active_positions)
    weakest_active = _weakest_active_position(active_positions)

    for signal in ranked:
        if len(selected) >= MAX_CONCURRENT_POSITIONS:
            break
        if _is_expired(signal, current_time):
            continue
        if _is_on_cooldown(signal.symbol, current_time, ctx.symbol_cooldown_until_utc):
            continue
        if signal.symbol in selected_symbols:
            continue
        if ctx.execution_health is not HealthState.GREEN:
            continue
        if ctx.spread_health is not HealthState.GREEN:
            continue
        if ctx.latency_health is not HealthState.GREEN:
            continue
        if not _correlation_budget_allows(signal, active_group_risk):
            continue
        if len(active_positions) >= MAX_CONCURRENT_POSITIONS and not _replacement_allowed(signal, weakest_active, ctx):
            continue
        selected.append(signal)
        selected_symbols.add(signal.symbol)

    return selected


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
