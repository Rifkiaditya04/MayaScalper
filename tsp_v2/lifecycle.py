"""Pure lifecycle stop-management rules for TSP V2."""

from __future__ import annotations

from dataclasses import replace

from .enums import Direction
from .models import LifecycleState


TRAIL_AFTER_R = 1.5
TRAIL_DISTANCE_R = 1.0


def evaluate_lifecycle(
    state: LifecycleState,
    *,
    current_price: float,
    break_even_after_r: float = 1.0,
) -> LifecycleState:
    """Advance a stop only after the position has earned the configured R progress.

    The function is deliberately side-effect free: runtime code owns broker calls and
    persistence.  Stops may improve but are never widened in the losing direction.
    """
    if current_price <= 0.0 or state.initial_r_distance <= 0.0:
        return state

    r_progress = _r_multiple(state, current_price)
    new_stop = state.current_stop
    trail_active = state.trail_active
    if not trail_active and r_progress >= break_even_after_r:
        new_stop = _more_protective_stop(state, state.entry_price)
        trail_active = True
    elif trail_active and r_progress >= TRAIL_AFTER_R:
        new_stop = _more_protective_stop(state, _trail_stop(state, current_price))

    return replace(state, current_stop=new_stop, trail_active=trail_active)


def _r_multiple(state: LifecycleState, current_price: float) -> float:
    moved = (
        current_price - state.entry_price
        if state.direction is Direction.LONG
        else state.entry_price - current_price
    )
    return moved / state.initial_r_distance


def _trail_stop(state: LifecycleState, current_price: float) -> float:
    if state.direction is Direction.LONG:
        return current_price - (TRAIL_DISTANCE_R * state.initial_r_distance)
    return current_price + (TRAIL_DISTANCE_R * state.initial_r_distance)


def _more_protective_stop(state: LifecycleState, candidate: float) -> float:
    if state.direction is Direction.LONG:
        return max(state.current_stop, candidate)
    return min(state.current_stop, candidate)
