from __future__ import annotations

from datetime import datetime, timezone
import unittest

from tsp.config import RegimeConfig
from tsp.regime import classify_regime, derive_direction_bias
from tsp.state import CandleData, Direction, MarketSnapshot, Regime


def _candle(ts_hour: int, close: float, timeframe: str) -> CandleData:
    return CandleData(
        timestamp=datetime(2026, 5, 22, ts_hour, 0, tzinfo=timezone.utc),
        open=close - 0.2,
        high=close + 0.3,
        low=close - 0.4,
        close=close,
        volume=100.0,
        timeframe=timeframe,
    )


def _snapshot(**overrides: object) -> MarketSnapshot:
    base = dict(
        symbol="XAUUSD",
        timestamp=datetime(2026, 5, 22, 13, 30, tzinfo=timezone.utc),
        m1=_candle(13, 3320.4, "M1"),
        m5=_candle(13, 3319.8, "M5"),
        m15=_candle(13, 3318.5, "M15"),
        h1=_candle(13, 3310.5, "H1"),
        atr_m1=1.8,
        atr_m1_base=1.0,
        atr_m5=2.0,
        atr_m5_base=1.4,
        atr_m15_base=2.2,
        atr_h1_base=4.5,
        atr_m1_prev_window=0.7,
        atr_m5_prev_window=1.0,
        adx_m5=24.0,
        adx_m15=26.0,
        adx_h1=28.0,
        h1_slope=0.9,
        m15_slope=0.5,
        spread_current=0.25,
        spread_baseline=0.10,
        tick_vol_m1=180.0,
        tick_vol_m1_base=100.0,
        swing_high_m5=3321.2,
        swing_low_m5=3314.4,
        m1_closes_recent=(3318.9, 3319.2, 3319.6, 3320.0, 3320.4, 3320.7, 3321.0, 3321.3),
        is_news_window=False,
        session="OVERLAP",
        bid=3321.2,
        ask=3321.45,
    )
    base.update(overrides)
    return MarketSnapshot(**base)


class TestTSPRegime(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = RegimeConfig(
            atr_collapse_asia=0.30,
            atr_collapse_other=0.35,
            dead_spread_ratio=4.0,
            trend_strength_min=0.55,
            trend_adx_boost=0.12,
            trend_adx_min=22.0,
            breakout_compression_max=0.80,
            breakout_burst_min=1.50,
            breakout_adx_min=16.0,
            breakout_adx_max=40.0,
            slope_agree_min=0.10,
            slope_bias_long_min=0.08,
            breakout_secondary_min_count=2,
            breakout_direction_emergence_min=0.20,
            breakout_volume_expansion_min=1.20,
            breakout_m5_expansion_min=1.05,
        )

    def test_classify_regime_news_dead_on_news_window(self) -> None:
        result = classify_regime(_snapshot(is_news_window=True), self.cfg)
        self.assertEqual(result.regime, Regime.NEWS_DEAD)

    def test_classify_regime_trend_with_breakout_confirm(self) -> None:
        result = classify_regime(_snapshot(), self.cfg)
        self.assertEqual(result.regime, Regime.TREND)
        self.assertEqual(result.direction_bias, Direction.LONG)
        self.assertEqual(result.conflict_note, "TREND_WITH_BO_CONFIRM")

    def test_classify_regime_breakout_when_trend_not_ready(self) -> None:
        result = classify_regime(
            _snapshot(
                adx_h1=18.0,
                adx_m15=18.0,
                h1_slope=0.35,
                m15_slope=0.22,
            ),
            self.cfg,
        )
        self.assertEqual(result.regime, Regime.BREAKOUT)

    def test_classify_regime_htf_trend_pending_downgrades_to_chop(self) -> None:
        result = classify_regime(
            _snapshot(adx_m15=18.0, atr_m1_prev_window=0.95, atr_m1=1.05),
            self.cfg,
        )
        self.assertEqual(result.regime, Regime.CHOP)
        self.assertEqual(result.conflict_note, "HTF_TREND_PENDING")
        self.assertEqual(result.diagnostics["trend_fail_reason"], "adx_m15_below_threshold")

    def test_classify_regime_exposes_breakout_fail_reason_on_chop(self) -> None:
        result = classify_regime(
            _snapshot(
                adx_h1=18.0,
                adx_m15=18.0,
                h1_slope=0.05,
                m15_slope=0.04,
                atr_m1_prev_window=0.95,
                atr_m1=1.05,
            ),
            self.cfg,
        )
        self.assertEqual(result.regime, Regime.CHOP)
        self.assertEqual(result.diagnostics["breakout_fail_reason"], "compression_above_threshold")

    def test_derive_direction_bias_flat_on_conflict(self) -> None:
        bias = derive_direction_bias(
            _snapshot(h1_slope=0.9, m15_slope=-0.5),
            self.cfg,
        )
        self.assertEqual(bias, Direction.FLAT)
