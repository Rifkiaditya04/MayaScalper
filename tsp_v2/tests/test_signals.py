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
    SessionName,
    SignalFamily,
)
from tsp_v2.models import ContractSnapshot, MarketSnapshot, NewsSnapshot, RegimeDecision
from tsp_v2.signals import evaluate_signals


class SignalTests(unittest.TestCase):
    def test_trend_signal_accepts_matching_regime(self) -> None:
        snapshot = _snapshot(direction=Direction.LONG)
        regime = RegimeDecision(
            regime=RegimeName.TREND,
            confidence=0.82,
            direction_bias=Direction.LONG,
            raw_scores={},
            diagnostics={},
        )
        result = evaluate_signals(snapshot, regime)
        self.assertTrue(result.accepted)
        self.assertIsNotNone(result.decision)
        self.assertEqual(result.decision.signal_family, SignalFamily.TREND_CONTINUATION)
        self.assertGreaterEqual(result.decision.score, result.decision.threshold)

    def test_breakout_signal_accepts_matching_regime(self) -> None:
        snapshot = _snapshot(direction=Direction.LONG, breakout=True)
        regime = RegimeDecision(
            regime=RegimeName.BREAKOUT,
            confidence=0.88,
            direction_bias=Direction.LONG,
            raw_scores={
                "breakout_recent_high": 100.8,
                "breakout_recent_low": 99.8,
                "breakout_close_price": 102.0,
                "breakout_body_dominance": 0.90,
                "breakout_burst_score": 0.95,
                "breakout_burst_ratio": 1.90,
                "breakout_participation_score": 0.75,
                "breakout_participation_count": 3.0,
            },
            diagnostics={},
        )
        result = evaluate_signals(snapshot, regime)
        self.assertTrue(result.accepted)
        self.assertEqual(result.decision.signal_family, SignalFamily.BREAKOUT_MOMENTUM)

    def test_micro_signal_rejects_latency_degraded(self) -> None:
        snapshot = _snapshot(direction=Direction.LONG, latency_health=HealthState.YELLOW)
        regime = RegimeDecision(
            regime=RegimeName.MICRO_MOMENTUM,
            confidence=0.80,
            direction_bias=Direction.LONG,
            raw_scores={"micro_impulse_ratio": 1.35, "micro_continuation_ok": 1.0},
            diagnostics={},
        )
        result = evaluate_signals(snapshot, regime)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reject_reason, "latency_degraded")

    def test_signal_rejects_stale_candidate(self) -> None:
        snapshot = _snapshot(direction=Direction.LONG, stale_m1_seconds=120)
        regime = RegimeDecision(
            regime=RegimeName.MICRO_MOMENTUM,
            confidence=0.84,
            direction_bias=Direction.LONG,
            raw_scores={"micro_impulse_ratio": 1.40, "micro_continuation_ok": 1.0},
            diagnostics={},
        )
        result = evaluate_signals(snapshot, regime)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reject_reason, "stale")

    def test_signal_rejects_duplicate_with_active_ttl(self) -> None:
        snapshot = _snapshot(direction=Direction.LONG)
        regime = RegimeDecision(
            regime=RegimeName.TREND,
            confidence=0.82,
            direction_bias=Direction.LONG,
            raw_scores={},
            diagnostics={},
        )
        first = evaluate_signals(snapshot, regime)
        second = evaluate_signals(
            snapshot,
            regime,
            active_signal_keys={first.signal_key: snapshot.cycle_time_utc + timedelta(seconds=60)},
        )
        self.assertTrue(first.accepted)
        self.assertFalse(second.accepted)
        self.assertEqual(second.reject_reason, "duplicate")

    def test_signal_rejects_regime_mismatch(self) -> None:
        snapshot = _snapshot(direction=Direction.LONG)
        regime = RegimeDecision(
            regime=RegimeName.CHOP,
            confidence=0.20,
            direction_bias=Direction.FLAT,
            raw_scores={},
            diagnostics={},
        )
        result = evaluate_signals(snapshot, regime)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reject_reason, "regime_conflict")

    def test_signal_rejects_direction_conflict(self) -> None:
        snapshot = _snapshot(direction=Direction.LONG, contradictory_trend_candle=True)
        regime = RegimeDecision(
            regime=RegimeName.TREND,
            confidence=0.82,
            direction_bias=Direction.LONG,
            raw_scores={},
            diagnostics={},
        )
        result = evaluate_signals(snapshot, regime)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reject_reason, "HTF_conflict")

    def test_governor_threshold_adjustment_is_state_aware(self) -> None:
        snapshot = _snapshot(direction=Direction.LONG, softer_trend=True, spread_ratio=1.50)
        regime = RegimeDecision(
            regime=RegimeName.TREND,
            confidence=0.72,
            direction_bias=Direction.LONG,
            raw_scores={},
            diagnostics={},
        )
        normal = evaluate_signals(snapshot, regime, governor_state=GovernorState.NORMAL)
        hunter = evaluate_signals(snapshot, regime, governor_state=GovernorState.HUNTER)
        self.assertFalse(normal.accepted)
        self.assertTrue(hunter.accepted)
        self.assertLess(hunter.decision.threshold, normal.diagnostics["adjusted_threshold"])


def _snapshot(
    *,
    direction: Direction,
    breakout: bool = False,
    latency_health: HealthState = HealthState.GREEN,
    stale_m1_seconds: int = 0,
    contradictory_trend_candle: bool = False,
    softer_trend: bool = False,
    spread_ratio: float = 1.0,
) -> MarketSnapshot:
    cycle_time = datetime(2026, 5, 27, 8, 35, 0, tzinfo=timezone.utc)
    bars_h1 = _build_bars("H1", cycle_time, 40, 60, direction, step=0.9)
    bars_m15 = _build_bars("M15", cycle_time, 40, 15, direction, step=0.7)
    bars_m5 = _build_bars("M5", cycle_time, 70, 5, direction, step=0.45 if softer_trend else 0.65)
    bars_m1 = _build_bars("M1", cycle_time, 40, 1, direction, step=0.20)

    if not breakout:
        _apply_trend_pullback_pattern(bars_m5, direction=direction, body_size=0.50 if softer_trend else 0.75)

    if contradictory_trend_candle:
        bars_m5[-1]["open"] = 120.0
        bars_m5[-1]["close"] = 119.2
        bars_m5[-1]["high"] = 120.2
        bars_m5[-1]["low"] = 119.0

    if breakout:
        bars_m5[-1]["open"] = 100.9
        bars_m5[-1]["close"] = 102.0
        bars_m5[-1]["high"] = 102.2
        bars_m5[-1]["low"] = 100.7

    if stale_m1_seconds:
        bars_m1[-1]["close_time_utc"] = cycle_time - timedelta(seconds=stale_m1_seconds)

    return MarketSnapshot(
        cycle_time_utc=cycle_time,
        symbol="XAUUSD",
        tick_bid=2350.0,
        tick_ask=2350.4,
        spread_points=4.0,
        spread_ratio=spread_ratio,
        spread_health=(
            HealthState.GREEN
            if spread_ratio <= 1.20
            else (HealthState.YELLOW if spread_ratio <= 1.80 else HealthState.RED)
        ),
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
        feed_health=latency_health,
        latency_health=latency_health,
        bars_h1=tuple(bars_h1),
        bars_m15=tuple(bars_m15),
        bars_m5=tuple(bars_m5),
        bars_m1=tuple(bars_m1),
        indicator_bundle={
            "bar_anchor_m1_close_utc": bars_m1[-1]["close_time_utc"],
            "bar_anchor_m5_close_utc": bars_m5[-1]["close_time_utc"],
            "atr_m5": 1.0,
            "h1_slope": 0.8 if direction is Direction.LONG else -0.8,
            "m15_slope": 0.7 if direction is Direction.LONG else -0.7,
        },
    )


def _build_bars(
    timeframe: str,
    cycle_time: datetime,
    count: int,
    step_minutes: int,
    direction: Direction,
    *,
    step: float,
) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    start = cycle_time - timedelta(minutes=step_minutes * count)
    price = 100.0
    drift = step if direction is Direction.LONG else -step
    for idx in range(count):
        open_price = price
        close = price + drift
        high = max(open_price, close) + 0.15
        low = min(open_price, close) - 0.15
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
                "timeframe": timeframe,
            }
        )
        price = close
    return bars


def _apply_trend_pullback_pattern(
    bars_m5: list[dict[str, object]],
    *,
    direction: Direction,
    body_size: float,
) -> None:
    pivot = float(bars_m5[-4]["close"])
    if direction is Direction.LONG:
        bars_m5[-3]["open"] = pivot + 0.20
        bars_m5[-3]["close"] = pivot - 0.05
        bars_m5[-3]["high"] = pivot + 0.30
        bars_m5[-3]["low"] = pivot - 0.15
        bars_m5[-2]["open"] = pivot - 0.05
        bars_m5[-2]["close"] = pivot - 0.10
        bars_m5[-2]["high"] = pivot + 0.05
        bars_m5[-2]["low"] = pivot - 0.20
        bars_m5[-1]["open"] = pivot - 0.05
        bars_m5[-1]["close"] = bars_m5[-1]["open"] + body_size
        bars_m5[-1]["high"] = float(bars_m5[-1]["close"]) + 0.15
        bars_m5[-1]["low"] = float(bars_m5[-1]["open"]) - 0.10
    else:
        bars_m5[-3]["open"] = pivot - 0.20
        bars_m5[-3]["close"] = pivot + 0.05
        bars_m5[-3]["high"] = pivot + 0.15
        bars_m5[-3]["low"] = pivot - 0.30
        bars_m5[-2]["open"] = pivot + 0.05
        bars_m5[-2]["close"] = pivot + 0.10
        bars_m5[-2]["high"] = pivot + 0.20
        bars_m5[-2]["low"] = pivot - 0.05
        bars_m5[-1]["open"] = pivot + 0.05
        bars_m5[-1]["close"] = bars_m5[-1]["open"] - body_size
        bars_m5[-1]["high"] = float(bars_m5[-1]["open"]) + 0.10
        bars_m5[-1]["low"] = float(bars_m5[-1]["close"]) - 0.15


if __name__ == "__main__":
    unittest.main()
