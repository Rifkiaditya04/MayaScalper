"""Risk dan feasibility layer."""

from dataclasses import dataclass

from mt5_bot.mt5_client import SymbolConstraints


@dataclass(slots=True)
class FeasibilityResult:
    effective_tp_distance: float
    required_progress_distance: float
    feasible: bool
    reason: str


def build_tp_feasibility(
    atr: float,
    broker_min_tp_distance: float,
    tp_atr_mult: float,
    broker_buffer: float,
    feasibility_buffer: float,
) -> FeasibilityResult:
    effective_tp_distance = max(tp_atr_mult * atr, broker_min_tp_distance * broker_buffer)
    required_progress_distance = effective_tp_distance * feasibility_buffer
    feasible = atr >= required_progress_distance
    reason = "ok" if feasible else "m5_atr_below_required_tp_progress"
    return FeasibilityResult(
        effective_tp_distance=effective_tp_distance,
        required_progress_distance=required_progress_distance,
        feasible=feasible,
        reason=reason,
    )


def build_entry_volume(
    *,
    equity: float,
    total_setup_risk_pct: float,
    estimated_loss_per_lot: float | None,
    constraints: SymbolConstraints,
    max_lot_per_order: float,
) -> float:
    if estimated_loss_per_lot is None or estimated_loss_per_lot <= 0:
        return min(max_lot_per_order, constraints.volume_min)

    risk_budget = max(equity * total_setup_risk_pct, 0.0)
    if risk_budget <= 0:
        return min(max_lot_per_order, constraints.volume_min)

    raw_volume = risk_budget / estimated_loss_per_lot
    capped = min(raw_volume, max_lot_per_order, constraints.volume_max)
    normalized = max(capped, constraints.volume_min)
    return normalized
