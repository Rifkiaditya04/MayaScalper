from __future__ import annotations

from datetime import datetime, timezone
import unittest

from tsp.config import SignalConfig
from tsp.signals import evaluate_signals
from tsp.state import (
    AggressionState,
    ConfidenceTier,
    Direction,
    MarketSnapshot,
    Module,
    PositionState,
    Regime,
    RegimeResult,
    RiskParams,
    RuntimeState,
    SignalScore,
    TradePhase,
    CandleData,
)


def _candle(close: float, timeframe: str) -> CandleData:
    return CandleData(
        timestamp=datetime(2026, 5, 22, 13, 0, tzinfo=timezone.utc),
        open=close - 1.0,
        high=close + 0.2,
        low=close - 1.2,
        close=close,
        volume=150.0,
        timeframe=timeframe,
    )


def _snapshot(**overrides: object) -> MarketSnapshot:
    base = dict(
        symbol="XAUUSD",
        timestamp=datetime(2026, 5, 22, 13, 30, tzinfo=timezone.utc),
        m1=_candle(3320.6, "M1"),
        m5=_candle(3319.8, "M5"),
        m15=_candle(3318.2, "M15"),
        h1=_candle(3310.4, "H1"),
        atr_m1=1.6,
        atr_m1_base=1.0,
        atr_m5=2.2,
        atr_m5_base=1.4,
        atr_m15_base=2.4,
        atr_h1_base=4.5,
        atr_m1_prev_window=0.7,
        atr_m5_prev_window=1.0,
        adx_m5=24.0,
        adx_m15=26.0,
        adx_h1=28.0,
        h1_slope=0.9,
        m15_slope=0.5,
        spread_current=0.12,
        spread_baseline=0.10,
        tick_vol_m1=180.0,
        tick_vol_m1_base=100.0,
        swing_high_m5=3321.4,
        swing_low_m5=3317.0,
        m1_closes_recent=(3318.6, 3319.0, 3319.4, 3319.8, 3320.1, 3320.3, 3320.5, 3320.6),
        is_news_window=False,
        session="OVERLAP",
        bid=3320.55,
        ask=3320.70,
    )
    base.update(overrides)
    return MarketSnapshot(**base)


class TestTSPSignals(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = SignalConfig(
            threshold_trend=55.0,
            threshold_breakout=58.0,
            threshold_chop=999.0,
            aggression_adj_aggressive=-7.0,
            aggression_adj_normal=0.0,
            aggression_adj_defensive=10.0,
            confidence_penalty_weight=4.0,
            stale_bars=3,
            stale_score_improvement_min=8.0,
            roc_lookback_bars=3,
            roc_min_atr_fraction=0.20,
            pullback_min_depth_atr=0.30,
            pullback_max_depth_atr=2.50,
            spread_penalty_ratio_start=1.30,
            breakout_atr_boost_multiplier=1.30,
        )

    def test_evaluate_signals_returns_pullback_signal_for_trend(self) -> None:
        regime = RegimeResult(
            regime=Regime.TREND,
            confidence=0.82,
            direction_bias=Direction.LONG,
            conflict_note="",
            raw_scores={},
        )
        signal = evaluate_signals(_snapshot(), regime, AggressionState.NORMAL, None, self.cfg)

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.module, Module.PULLBACK_CONTINUATION)
        self.assertEqual(signal.direction, Direction.LONG)
        self.assertGreaterEqual(signal.score, 55.0)

    def test_evaluate_signals_returns_breakout_signal(self) -> None:
        regime = RegimeResult(
            regime=Regime.BREAKOUT,
            confidence=0.78,
            direction_bias=Direction.FLAT,
            conflict_note="",
            raw_scores={},
        )
        signal = evaluate_signals(
            _snapshot(
                m1=_candle(3321.0, "M1"),
                bid=3320.95,
                ask=3321.10,
                m1_closes_recent=(3318.5, 3319.0, 3319.8, 3320.1, 3320.4, 3320.7, 3320.9, 3321.0),
                tick_vol_m1=210.0,
            ),
            regime,
            AggressionState.AGGRESSIVE,
            None,
            self.cfg,
        )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.module, Module.BREAKOUT_MOMENTUM)
        self.assertIn(signal.direction, {Direction.LONG, Direction.SHORT})

    def test_evaluate_signals_suppresses_stale_same_lineage_signal(self) -> None:
        regime = RegimeResult(
            regime=Regime.TREND,
            confidence=0.82,
            direction_bias=Direction.LONG,
            conflict_note="",
            raw_scores={},
        )
        previous_signal = SignalScore(
            module=Module.PULLBACK_CONTINUATION,
            direction=Direction.LONG,
            score=70.0,
            confidence_tier=ConfidenceTier.GOOD,
            body_score=15.0,
            wick_score=12.0,
            atr_expansion=15.0,
            session_bonus=7.0,
            spread_penalty=0.0,
            htf_alignment=15.0,
            momentum_score=6.0,
            volume_score=0.0,
            entry_hint=3320.7,
            invalidation_anchor=3317.0,
            setup_id="previoussetup1234",
            signal_timestamp=datetime(2026, 5, 22, 13, 29, tzinfo=timezone.utc),
            setup_metadata={},
        )
        runtime = RuntimeState(
            symbol="XAUUSD",
            magic=20260522,
            starting_equity=10_000.0,
            start_time=datetime(2026, 5, 22, 0, 0, tzinfo=timezone.utc),
            position=PositionState(phase=TradePhase.IDLE),
            equity_current=10_000.0,
            equity_peak=10_000.0,
            daily_start_equity=10_000.0,
            risk_params=RiskParams(),
            last_signal=previous_signal,
            last_signal_age_bars=2,
        )

        signal = evaluate_signals(_snapshot(), regime, AggressionState.NORMAL, runtime, self.cfg)
        self.assertIsNone(signal)
