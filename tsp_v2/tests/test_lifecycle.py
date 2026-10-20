from __future__ import annotations

from datetime import datetime, timezone
import unittest

from tsp_v2.enums import Direction
from tsp_v2.lifecycle import evaluate_lifecycle
from tsp_v2.models import LifecycleState


def _state(direction: Direction, *, current_stop: float | None = None, trail_active: bool = False) -> LifecycleState:
    return LifecycleState(
        position_ticket=1,
        setup_id="setup-1",
        symbol="XAUUSD",
        opened_at_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
        direction=direction,
        entry_price=100.0,
        initial_stop=98.0 if direction is Direction.LONG else 102.0,
        initial_r_distance=2.0,
        current_stop=current_stop if current_stop is not None else (98.0 if direction is Direction.LONG else 102.0),
        partial_taken=False,
        trail_active=trail_active,
        pyramid_count=0,
        thesis_expiry_utc=datetime(2026, 5, 29, 10, 0, 0, tzinfo=timezone.utc),
        orphan_recovered=False,
    )


class LifecycleTests(unittest.TestCase):
    def test_break_even_triggers_at_exactly_one_r(self) -> None:
        updated = evaluate_lifecycle(_state(Direction.LONG), current_price=102.0)
        self.assertEqual(updated.current_stop, 100.0)
        self.assertTrue(updated.trail_active)

    def test_break_even_does_not_trigger_below_one_r(self) -> None:
        updated = evaluate_lifecycle(_state(Direction.LONG), current_price=101.98)
        self.assertEqual(updated.current_stop, 98.0)
        self.assertFalse(updated.trail_active)

    def test_trailing_starts_at_one_point_five_r(self) -> None:
        updated = evaluate_lifecycle(_state(Direction.LONG, current_stop=100.0, trail_active=True), current_price=103.0)
        self.assertEqual(updated.current_stop, 101.0)

    def test_long_stop_never_moves_backwards(self) -> None:
        updated = evaluate_lifecycle(_state(Direction.LONG, current_stop=102.0, trail_active=True), current_price=103.0)
        self.assertEqual(updated.current_stop, 102.0)

    def test_short_stop_never_moves_backwards(self) -> None:
        updated = evaluate_lifecycle(_state(Direction.SHORT, current_stop=98.0, trail_active=True), current_price=97.0)
        self.assertEqual(updated.current_stop, 98.0)


if __name__ == "__main__":
    unittest.main()
