from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from tsp_v2.enums import (
    Direction,
    ExecutionRegistryState,
    GovernorState,
    HealthState,
    NewsProviderMode,
    NewsProviderState,
    PaceClassification,
    RegimeName,
    RiskAction,
    SessionName,
    SignalFamily,
)
from tsp_v2.execution import (
    ExecutionRegistryBook,
    build_execution_intent,
    classify_broker_response,
    reconcile_registry_against_broker_truth,
    validate_execution_intent,
)
from tsp_v2.models import ContractSnapshot, GovernorDecision, MarketSnapshot, NewsSnapshot, RiskDecision, SignalDecision


class ExecutionTests(unittest.TestCase):
    def test_validate_accepts_live_intent(self) -> None:
        snapshot = _snapshot()
        signal = _signal(snapshot, SignalFamily.TREND_CONTINUATION)
        risk = _risk(snapshot)
        governor = _governor(GovernorState.NORMAL)
        result = validate_execution_intent(snapshot, signal, risk, governor)
        self.assertTrue(result.accepted)
        self.assertIsNotNone(result.intent)
        self.assertEqual(result.intent.signal_family, SignalFamily.TREND_CONTINUATION)

    def test_validate_rejects_duplicate_intent(self) -> None:
        snapshot = _snapshot()
        signal = _signal(snapshot, SignalFamily.TREND_CONTINUATION)
        risk = _risk(snapshot)
        governor = _governor(GovernorState.NORMAL)
        registry = ExecutionRegistryBook()
        first = validate_execution_intent(snapshot, signal, risk, governor, registry=registry)
        registry.reserve(first.intent, at_utc=snapshot.cycle_time_utc)
        second = validate_execution_intent(snapshot, signal, risk, governor, registry=registry)
        self.assertTrue(first.accepted)
        self.assertFalse(second.accepted)
        self.assertEqual(second.reject_reason, "duplicate_intent")

    def test_validate_rejects_stale_signal(self) -> None:
        snapshot = _snapshot(signal_expired=True)
        signal = _signal(snapshot, SignalFamily.MICRO_IMPULSE, expires_delta_seconds=-1)
        risk = _risk(snapshot)
        governor = _governor(GovernorState.NORMAL)
        result = validate_execution_intent(snapshot, signal, risk, governor)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reject_reason, "expired_signal")

    def test_validate_rejects_spread_gate(self) -> None:
        snapshot = _snapshot(spread_health=HealthState.YELLOW)
        signal = _signal(snapshot, SignalFamily.BREAKOUT_MOMENTUM)
        risk = _risk(snapshot)
        governor = _governor(GovernorState.NORMAL)
        result = validate_execution_intent(snapshot, signal, risk, governor)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reject_reason, "spread_degraded")

    def test_registry_lifecycle_transitions(self) -> None:
        snapshot = _snapshot()
        signal = _signal(snapshot, SignalFamily.TREND_CONTINUATION)
        risk = _risk(snapshot)
        intent = build_execution_intent(signal, risk, decision_price=100.1, cycle_time_utc=snapshot.cycle_time_utc)
        registry = ExecutionRegistryBook()
        reserved = registry.reserve(intent, at_utc=snapshot.cycle_time_utc)
        submitted = registry.mark_submitted(reserved.submission_uuid, at_utc=snapshot.cycle_time_utc + timedelta(seconds=1), broker_ticket=111)
        acknowledged = registry.mark_acknowledged(submitted.submission_uuid, at_utc=snapshot.cycle_time_utc + timedelta(seconds=2), broker_ticket=111)
        filled = registry.mark_filled(acknowledged.submission_uuid, at_utc=snapshot.cycle_time_utc + timedelta(seconds=3), broker_ticket=111)
        self.assertEqual(reserved.state, ExecutionRegistryState.PENDING)
        self.assertEqual(submitted.state, ExecutionRegistryState.SUBMITTED)
        self.assertEqual(acknowledged.state, ExecutionRegistryState.ACKNOWLEDGED)
        self.assertEqual(filled.state, ExecutionRegistryState.FILLED)

    def test_retryable_failure_mapping(self) -> None:
        disposition = classify_broker_response({"retcode": "TRADE_CONTEXT_BUSY", "ticket": 11})
        self.assertTrue(disposition.retryable)
        self.assertEqual(disposition.suggested_state, ExecutionRegistryState.SUBMITTED)

    def test_non_retryable_failure_mapping(self) -> None:
        disposition = classify_broker_response({"retcode": "INVALID_VOLUME", "ticket": 11})
        self.assertFalse(disposition.retryable)
        self.assertEqual(disposition.suggested_state, ExecutionRegistryState.REJECTED)

    def test_broker_truth_reconciliation_marks_filled(self) -> None:
        snapshot = _snapshot()
        signal = _signal(snapshot, SignalFamily.TREND_CONTINUATION)
        risk = _risk(snapshot)
        registry = ExecutionRegistryBook()
        intent = build_execution_intent(signal, risk, decision_price=100.1, cycle_time_utc=snapshot.cycle_time_utc)
        registry.reserve(intent, at_utc=snapshot.cycle_time_utc)
        reconciled = reconcile_registry_against_broker_truth(
            registry,
            [{"submission_uuid": intent.submission_uuid, "state": "DONE", "ticket": 222}],
            at_utc=snapshot.cycle_time_utc + timedelta(seconds=10),
        )
        self.assertEqual(reconciled[0].state, ExecutionRegistryState.FILLED)

    def test_symbol_lock_blocks_duplicate_until_expiry(self) -> None:
        snapshot = _snapshot()
        signal = _signal(snapshot, SignalFamily.TREND_CONTINUATION)
        risk = _risk(snapshot)
        registry = ExecutionRegistryBook()
        intent = build_execution_intent(signal, risk, decision_price=100.1, cycle_time_utc=snapshot.cycle_time_utc)
        registry.reserve(intent, at_utc=snapshot.cycle_time_utc)
        duplicate = registry.is_duplicate(intent=intent, at_utc=snapshot.cycle_time_utc + timedelta(seconds=5))
        self.assertTrue(duplicate)


def _snapshot(
    *,
    spread_health: HealthState = HealthState.GREEN,
    latency_health: HealthState = HealthState.GREEN,
    signal_expired: bool = False,
) -> MarketSnapshot:
    cycle_time = datetime(2026, 5, 29, 10, 0, 0, tzinfo=timezone.utc)
    bars_h1 = _bars(cycle_time, 40, 60, 100.0, 0.50)
    bars_m15 = _bars(cycle_time, 40, 15, 100.0, 0.45)
    bars_m5 = _bars(cycle_time, 70, 5, 100.0, 0.35)
    bars_m1 = _bars(cycle_time, 40, 1, 100.0, 0.15)
    return MarketSnapshot(
        cycle_time_utc=cycle_time,
        symbol="XAUUSD",
        tick_bid=100.0,
        tick_ask=100.2,
        spread_points=2.0,
        spread_ratio=1.0,
        spread_health=spread_health,
        session=SessionName.LONDON_NY,
        news=NewsSnapshot(
            provider_mode=NewsProviderMode.STATIC_FILE,
            provider_state=NewsProviderState.READY,
            snapshot_generated_at_utc=cycle_time - timedelta(minutes=5),
            lockout_active=False,
            next_relevant_event_utc=None,
            relevant_events=(),
        ),
        contract=ContractSnapshot(
            symbol="XAUUSD",
            point=0.1,
            tick_size=0.1,
            tick_value=1.0,
            min_lot=0.01,
            max_lot=100.0,
            lot_step=0.01,
            stop_level_points=20,
            freeze_level_points=0,
        ),
        feed_health=HealthState.GREEN,
        latency_health=latency_health,
        bars_h1=tuple(bars_h1),
        bars_m15=tuple(bars_m15),
        bars_m5=tuple(bars_m5),
        bars_m1=tuple(bars_m1),
        indicator_bundle={
            "bar_anchor_m5_close_utc": bars_m5[-1]["close_time_utc"],
            "bar_anchor_m1_close_utc": bars_m1[-1]["close_time_utc"],
            "atr_m5": 1.0,
        },
    )


def _signal(snapshot: MarketSnapshot, family: SignalFamily, *, expires_delta_seconds: int = 120) -> SignalDecision:
    cycle_time = snapshot.cycle_time_utc
    return SignalDecision(
        setup_id=f"{family.value.lower()}-{cycle_time.isoformat()}",
        signal_family=family,
        symbol=snapshot.symbol,
        direction=Direction.LONG,
        score=0.88,
        threshold=0.72,
        expires_at_utc=cycle_time + timedelta(seconds=expires_delta_seconds),
        rationale="test",
        lineage=("REGIME:TREND", f"FAMILY:{family.value}"),
    )


def _risk(snapshot: MarketSnapshot) -> RiskDecision:
    return RiskDecision(
        action=RiskAction.ENTER,
        risk_multiplier=1.0,
        sized_volume=0.10,
        invalidation_price=99.5,
        hard_block_reason="",
        governor_adjusted_state=GovernorState.NORMAL,
    )


def _governor(state: GovernorState) -> GovernorDecision:
    return GovernorDecision(
        state=state,
        state_reason="test",
        pace_classification=PaceClassification.ON_TRACK,
        aggression_multiplier=1.0,
        profile_constraints={},
    )


def _bars(
    cycle_time: datetime,
    count: int,
    step_minutes: int,
    start_price: float,
    drift: float,
) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    start = cycle_time - timedelta(minutes=step_minutes * count)
    price = start_price
    for idx in range(count):
        open_price = price
        close = price + drift
        high = close + 0.10
        low = open_price - 0.10
        bar_time = start + timedelta(minutes=step_minutes * idx)
        bars.append(
            {
                "timestamp": bar_time,
                "close_time_utc": bar_time + timedelta(minutes=step_minutes),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "tick_volume": float(100 + idx),
                "timeframe": f"M{step_minutes}",
            }
        )
        price = close
    return bars


if __name__ == "__main__":
    unittest.main()
