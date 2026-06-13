from __future__ import annotations

from datetime import datetime, timezone
import unittest

from tsp_v2.clock import evaluate_clock_state, is_execution_blocked
from tsp_v2.enums import ClockHealth


class ClockTests(unittest.TestCase):
    def test_clock_warning_on_skew_above_60_seconds(self) -> None:
        state = evaluate_clock_state(
            broker_time=datetime(2026, 5, 26, 10, 1, 1, tzinfo=timezone.utc),
            local_time_utc=datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(state.health, ClockHealth.WARNING)
        self.assertFalse(is_execution_blocked(state))

    def test_clock_soft_fail_blocks_execution(self) -> None:
        state = evaluate_clock_state(
            broker_time=datetime(2026, 5, 26, 10, 3, 5, tzinfo=timezone.utc),
            local_time_utc=datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(state.health, ClockHealth.SOFT_FAIL)
        self.assertTrue(is_execution_blocked(state))

    def test_clock_hard_fail_on_large_backward_jump(self) -> None:
        state = evaluate_clock_state(
            broker_time=datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc),
            local_time_utc=datetime(2026, 5, 26, 10, 0, 1, tzinfo=timezone.utc),
            previous_broker_time_utc=datetime(2026, 5, 26, 10, 1, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(state.health, ClockHealth.HARD_FAIL)
        self.assertIn("broker_backward_jump_hard_fail", state.diagnostic_flags)


if __name__ == "__main__":
    unittest.main()
