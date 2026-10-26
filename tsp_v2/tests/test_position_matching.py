from __future__ import annotations

from datetime import datetime, timezone
import unittest

from tsp_v2.enums import Direction, SignalFamily
from tsp_v2.live_runtime import _position_matches_intent
from tsp_v2.models import ExecutionIntent


class PositionMatchingTests(unittest.TestCase):
    def test_truncated_broker_comment_resolves_when_prefix_is_long_enough(self) -> None:
        intent = _intent()
        requested_comment = f"TSP_V2|{intent.setup_id}|{intent.submission_uuid}"
        record = _record(intent, ticket=10, comment=requested_comment[:31])

        self.assertTrue(_position_matches_intent(record, intent, result_ticket=10))

    def test_same_symbol_with_different_ticket_does_not_resolve_without_matching_comment(self) -> None:
        intent = _intent()
        record = _record(intent, ticket=11, comment="other-position")

        self.assertFalse(_position_matches_intent(record, intent, result_ticket=10))

    def test_exact_comment_match_preserves_existing_matching_behavior(self) -> None:
        intent = _intent()
        comment = f"TSP_V2|{intent.setup_id}|{intent.submission_uuid}"

        self.assertTrue(_position_matches_intent(_record(intent, ticket=11, comment=comment), intent))


def _intent() -> ExecutionIntent:
    return ExecutionIntent(
        setup_id="setup-with-a-long-identifier",
        signal_family=SignalFamily.MICRO_IMPULSE,
        symbol="EURUSD",
        direction=Direction.LONG,
        decision_price=1.1001,
        sized_volume=0.01,
        stop_loss_price=1.0980,
        submission_uuid="submission-with-a-long-identifier",
        cycle_time_utc=datetime.now(tz=timezone.utc),
    )


def _record(intent: ExecutionIntent, *, ticket: int, comment: str) -> dict[str, object]:
    return {
        "ticket": ticket,
        "symbol": intent.symbol,
        "direction": intent.direction.value,
        "comment": comment,
    }
