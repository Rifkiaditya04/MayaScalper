from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from tsp_v2.enums import (
    Direction,
    GovernorState,
    HealthState,
    NewsProviderMode,
    NewsProviderState,
    RegimeName,
    RiskAction,
    SessionName,
    SignalFamily,
)
from tsp_v2.models import (
    ContractSnapshot,
    GovernorDecision,
    MarketSnapshot,
    NewsSnapshot,
    PositionSnapshot,
    RiskContext,
    SignalDecision,
)
from tsp_v2.risk import evaluate_risk


class RiskTests(unittest.TestCase):
    def test_sizing_correctness_uses_governor_base_risk(self) -> None:
        snapshot = _snapshot(symbol="XAUUSD")
        signal = _signal(snapshot, family=SignalFamily.TREND_CONTINUATION, score=0.82)
        governor = _governor(GovernorState.NORMAL)
        result = evaluate_risk(snapshot, signal, governor)
        self.assertEqual(result.action, RiskAction.ENTER)
        self.assertGreater(result.sized_volume, 0.0)
        self.assertAlmostEqual(result.diagnostics["base_risk_pct"], 0.75, places=2)
        self.assertEqual(result.governor_adjusted_state, GovernorState.NORMAL)

    def test_cap_enforcement_blocks_when_portfolio_budget_exhausted(self) -> None:
        snapshot = _snapshot(symbol="XAUUSD")
        signal = _signal(snapshot, family=SignalFamily.BREAKOUT_MOMENTUM, score=0.90)
        governor = _governor(GovernorState.ATTACK)
        context = RiskContext(
            account_equity=100_000.0,
            open_positions=(
                PositionSnapshot("XAUUSD", Direction.LONG, "seed-1", "XAUUSD", 3.50),
            ),
        )
        result = evaluate_risk(snapshot, signal, governor, context=context)
        self.assertEqual(result.action, RiskAction.BLOCK)
        self.assertEqual(result.hard_block_reason, "cap_exhausted")

    def test_correlation_enforcement_blocks_group_overflow(self) -> None:
        snapshot = _snapshot(symbol="EURUSD")
        signal = _signal(snapshot, family=SignalFamily.TREND_CONTINUATION, score=0.88)
        governor = _governor(GovernorState.HUNTER)
        context = RiskContext(
            account_equity=100_000.0,
            open_positions=(
                PositionSnapshot("GBPUSD", Direction.LONG, "seed-1", "GBPUSD_EURUSD", 2.25),
            ),
        )
        result = evaluate_risk(snapshot, signal, governor, context=context)
        self.assertEqual(result.action, RiskAction.BLOCK)
        self.assertEqual(result.hard_block_reason, "cap_exhausted")

    def test_pyramid_qualification_allows_single_add(self) -> None:
        snapshot = _snapshot(symbol="XAUUSD")
        signal = _signal(snapshot, family=SignalFamily.TREND_CONTINUATION, score=0.90, setup_id="thesis-1")
        governor = _governor(GovernorState.ATTACK)
        context = RiskContext(
            account_equity=100_000.0,
            current_unrealized_r=0.90,
            spread_health=HealthState.GREEN,
            latency_health=HealthState.GREEN,
            execution_health=HealthState.GREEN,
            open_positions=(
                PositionSnapshot("XAUUSD", Direction.LONG, "thesis-1", "XAUUSD", 0.50),
            ),
        )
        result = evaluate_risk(snapshot, signal, governor, context=context)
        self.assertEqual(result.action, RiskAction.PYRAMID)
        self.assertGreater(result.sized_volume, 0.0)

    def test_anti_revenge_block_rejects_loser_adds(self) -> None:
        snapshot = _snapshot(symbol="XAUUSD")
        signal = _signal(snapshot, family=SignalFamily.TREND_CONTINUATION, score=0.88)
        governor = _governor(GovernorState.NORMAL)
        context = RiskContext(
            account_equity=100_000.0,
            current_unrealized_r=-0.35,
            loss_streak=2,
            open_positions=(
                PositionSnapshot("XAUUSD", Direction.LONG, "thesis-1", "XAUUSD", 0.50),
            ),
        )
        result = evaluate_risk(snapshot, signal, governor, context=context)
        self.assertEqual(result.action, RiskAction.BLOCK)
        self.assertEqual(result.hard_block_reason, "anti_revenge_block")

    def test_emergency_escalation_triggers_exit(self) -> None:
        snapshot = _snapshot(symbol="XAUUSD")
        signal = _signal(snapshot, family=SignalFamily.BREAKOUT_MOMENTUM, score=0.95)
        governor = _governor(GovernorState.SPRINT)
        context = RiskContext(
            broker_stable=False,
            account_equity=100_000.0,
        )
        result = evaluate_risk(snapshot, signal, governor, context=context)
        self.assertEqual(result.action, RiskAction.EMERGENCY_EXIT)
        self.assertEqual(result.governor_adjusted_state, GovernorState.KILL_REVIEW)

    def test_max_positions_enforced(self) -> None:
        snapshot = _snapshot(symbol="XAUUSD")
        signal = _signal(snapshot, family=SignalFamily.MICRO_IMPULSE, score=0.84)
        governor = _governor(GovernorState.ATTACK)
        context = RiskContext(
            account_equity=100_000.0,
            open_positions=(
                PositionSnapshot("XAUUSD", Direction.LONG, "thesis-1", "XAUUSD", 0.75),
                PositionSnapshot("EURUSD", Direction.LONG, "thesis-2", "GBPUSD_EURUSD", 0.75),
            ),
        )
        result = evaluate_risk(snapshot, signal, governor, context=context)
        self.assertEqual(result.action, RiskAction.BLOCK)
        self.assertEqual(result.hard_block_reason, "max_positions")


def _signal(
    snapshot: MarketSnapshot,
    *,
    family: SignalFamily,
    score: float,
    setup_id: str | None = None,
) -> SignalDecision:
    setup = setup_id or f"{family.value.lower()}-1"
    return SignalDecision(
        setup_id=setup,
        signal_family=family,
        symbol=snapshot.symbol,
        direction=Direction.LONG,
        score=score,
        threshold=0.72,
        expires_at_utc=snapshot.cycle_time_utc + timedelta(seconds=120),
        rationale="test",
        lineage=("REGIME:TREND", f"FAMILY:{family.value}"),
    )


def _governor(state: GovernorState) -> GovernorDecision:
    return GovernorDecision(
        state=state,
        state_reason="test",
        pace_classification="NORMAL",
        aggression_multiplier=1.0,
        profile_constraints={},
    )


def _snapshot(*, symbol: str) -> MarketSnapshot:
    cycle_time = datetime(2026, 5, 28, 9, 15, 0, tzinfo=timezone.utc)
    bars_h1 = _bars(cycle_time, 40, 60, 100.0, 0.60)
    bars_m15 = _bars(cycle_time, 40, 15, 100.0, 0.50)
    bars_m5 = _bars(cycle_time, 70, 5, 100.0, 0.40)
    bars_m1 = _bars(cycle_time, 40, 1, 100.0, 0.15)
    return MarketSnapshot(
        cycle_time_utc=cycle_time,
        symbol=symbol,
        tick_bid=100.0,
        tick_ask=100.2,
        spread_points=2.0,
        spread_ratio=1.0,
        spread_health=HealthState.GREEN,
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
            symbol=symbol,
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
        latency_health=HealthState.GREEN,
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
