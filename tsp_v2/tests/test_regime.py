from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from tsp_v2.enums import Direction, HealthState, NewsProviderMode, NewsProviderState, RegimeName, SessionName
from tsp_v2.models import ContractSnapshot, MarketSnapshot, NewsSnapshot
from tsp_v2.regime import classify_regime


class RegimeTests(unittest.TestCase):
    def test_news_lockout_overrides_offensive_regimes(self) -> None:
        snapshot = _snapshot(news_lockout=True, trend_up=True)
        result = classify_regime(snapshot)
        self.assertEqual(result.regime, RegimeName.NEWS_LOCKOUT)

    def test_trend_classification(self) -> None:
        snapshot = _snapshot(trend_up=True)
        result = classify_regime(snapshot)
        self.assertEqual(result.regime, RegimeName.TREND)
        self.assertEqual(result.direction_bias, Direction.LONG)

    def test_breakout_classification(self) -> None:
        snapshot = _snapshot(breakout_up=True)
        result = classify_regime(snapshot)
        self.assertEqual(result.regime, RegimeName.BREAKOUT)
        self.assertEqual(result.direction_bias, Direction.LONG)

    def test_micro_classification(self) -> None:
        snapshot = _snapshot(micro_up=True)
        result = classify_regime(snapshot)
        self.assertEqual(result.regime, RegimeName.MICRO_MOMENTUM)
        self.assertEqual(result.direction_bias, Direction.LONG)

    def test_chop_fallback(self) -> None:
        snapshot = _snapshot()
        result = classify_regime(snapshot)
        self.assertEqual(result.regime, RegimeName.CHOP)

    def test_trend_priority_over_breakout(self) -> None:
        snapshot = _snapshot(trend_up=True, breakout_up=True)
        result = classify_regime(snapshot)
        self.assertEqual(result.regime, RegimeName.TREND)
        self.assertEqual(result.diagnostics["priority_resolution"], "TREND")


def _snapshot(
    *,
    news_lockout: bool = False,
    trend_up: bool = False,
    breakout_up: bool = False,
    micro_up: bool = False,
) -> MarketSnapshot:
    cycle_time = datetime(2026, 5, 26, 13, 10, 0, tzinfo=timezone.utc)
    bars_h1 = _build_trend_bars("H1", cycle_time, 40, 60, direction=1 if trend_up else 0)
    bars_m15 = _build_trend_bars("M15", cycle_time, 40, 15, direction=1 if trend_up else 0)
    if breakout_up:
        bars_m5 = _build_breakout_bars(cycle_time)
        bars_m1 = _build_micro_m1_bars(cycle_time, direction=1)
    elif micro_up:
        bars_m5 = _build_micro_m5_bars(cycle_time, direction=1)
        bars_m1 = _build_micro_m1_bars(cycle_time, direction=1)
    else:
        bars_m5 = _build_flat_bars("M5", cycle_time, 70, 5)
        bars_m1 = _build_flat_bars("M1", cycle_time, 40, 1)

    indicator_bundle = {
        "atr_m1": 1.0 if micro_up or breakout_up else 0.4,
        "atr_m1_base": 0.6 if breakout_up else 0.8,
        "atr_m5": 1.2 if breakout_up else (0.9 if micro_up else 0.5),
        "atr_m5_base": 0.7 if breakout_up else 0.8,
        "atr_m15_base": 1.0,
        "atr_h1_base": 1.0,
        "adx_h1": 24.0 if trend_up else 15.0,
        "adx_m15": 24.0 if trend_up else 15.0,
        "spread_points_baseline": 4.0,
    }

    if micro_up:
        indicator_bundle["adx_h1"] = 10.0
        indicator_bundle["adx_m15"] = 10.0

    return MarketSnapshot(
        cycle_time_utc=cycle_time,
        symbol="XAUUSD",
        tick_bid=2350.0,
        tick_ask=2350.4,
        spread_points=4.0,
        spread_ratio=1.0,
        spread_health=HealthState.GREEN,
        session=SessionName.LONDON_NY,
        news=NewsSnapshot(
            provider_mode=NewsProviderMode.STATIC_FILE,
            provider_state=NewsProviderState.READY,
            snapshot_generated_at_utc=cycle_time - timedelta(minutes=5),
            lockout_active=news_lockout,
            next_relevant_event_utc=cycle_time + timedelta(minutes=10) if news_lockout else None,
            relevant_events=({"impact": "HIGH"},) if news_lockout else (),
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
        latency_health=HealthState.GREEN,
        bars_h1=tuple(bars_h1),
        bars_m15=tuple(bars_m15),
        bars_m5=tuple(bars_m5),
        bars_m1=tuple(bars_m1),
        indicator_bundle=indicator_bundle,
    )


def _build_trend_bars(
    timeframe: str,
    cycle_time: datetime,
    count: int,
    step_minutes: int,
    *,
    direction: int,
) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    start = cycle_time - timedelta(minutes=step_minutes * count)
    price = 100.0
    for idx in range(count):
        drift = 0.8 if direction > 0 else 0.05
        open_price = price
        close = price + drift
        high = close + 0.2
        low = open_price - 0.2
        bar_time = start + timedelta(minutes=step_minutes * idx)
        bars.append(_bar(timeframe, bar_time, step_minutes, open_price, high, low, close, 100 + idx))
        price = close
    return bars


def _build_flat_bars(timeframe: str, cycle_time: datetime, count: int, step_minutes: int) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    start = cycle_time - timedelta(minutes=step_minutes * count)
    price = 100.0
    for idx in range(count):
        wiggle = 0.05 if idx % 2 == 0 else -0.05
        open_price = price
        close = price + wiggle
        high = max(open_price, close) + 0.1
        low = min(open_price, close) - 0.1
        bar_time = start + timedelta(minutes=step_minutes * idx)
        bars.append(_bar(timeframe, bar_time, step_minutes, open_price, high, low, close, 100))
        price = close
    return bars


def _build_breakout_bars(cycle_time: datetime) -> list[dict[str, object]]:
    bars = _build_flat_bars("M5", cycle_time, 70, 5)
    for idx in range(-20, -1):
        bars[idx]["open"] = 100.55
        bars[idx]["close"] = 100.57
        bars[idx]["high"] = 100.62
        bars[idx]["low"] = 100.50
        bars[idx]["tick_volume"] = 85
    for idx in range(-10, -1):
        bars[idx]["high"] = 100.62
        bars[idx]["low"] = 100.50
    bars[-1]["open"] = 100.6
    bars[-1]["close"] = 103.6
    bars[-1]["high"] = 103.9
    bars[-1]["low"] = 100.4
    bars[-1]["tick_volume"] = 220
    return bars


def _build_micro_m5_bars(cycle_time: datetime, *, direction: int) -> list[dict[str, object]]:
    bars = _build_flat_bars("M5", cycle_time, 70, 5)
    if direction > 0:
        bars[-1]["open"] = 100.0
        bars[-1]["close"] = 101.0
        bars[-1]["high"] = 101.1
        bars[-1]["low"] = 99.9
    return bars


def _build_micro_m1_bars(cycle_time: datetime, *, direction: int) -> list[dict[str, object]]:
    bars = _build_flat_bars("M1", cycle_time, 40, 1)
    if direction > 0:
        bars[-2]["close"] = 100.4
        bars[-1]["open"] = 100.4
        bars[-1]["close"] = 100.8
        bars[-1]["high"] = 100.9
        bars[-1]["low"] = 100.3
        bars[-1]["tick_volume"] = 150
        bars[-1]["close_time_utc"] = cycle_time
    return bars


def _bar(
    timeframe: str,
    timestamp: datetime,
    step_minutes: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: int,
) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "close_time_utc": timestamp + timedelta(minutes=step_minutes),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "tick_volume": float(volume),
        "timeframe": timeframe,
    }


if __name__ == "__main__":
    unittest.main()
