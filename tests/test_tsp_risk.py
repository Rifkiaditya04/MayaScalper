from __future__ import annotations

from datetime import datetime, timezone
import unittest

from tsp.data_pipeline import SymbolContract
from tsp.risk import (
    check_pyramid_eligibility,
    compute_effective_equity,
    compute_lot_size,
    evaluate_aggression_transition,
    evaluate_emergency_exit,
    evaluate_risk,
)
from tsp.state import (
    AggressionState,
    ConfidenceTier,
    Direction,
    LayerState,
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
        open=close - 0.8,
        high=close + 0.3,
        low=close - 1.0,
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


def _signal(**overrides: object) -> SignalScore:
    base = dict(
        module=Module.PULLBACK_CONTINUATION,
        direction=Direction.LONG,
        score=74.0,
        confidence_tier=ConfidenceTier.GOOD,
        body_score=16.0,
        wick_score=11.0,
        atr_expansion=14.0,
        session_bonus=10.0,
        spread_penalty=0.0,
        htf_alignment=15.0,
        momentum_score=8.0,
        volume_score=0.0,
        entry_hint=3320.70,
        invalidation_anchor=3317.0,
        setup_id="signalrisk123456",
        signal_timestamp=datetime(2026, 5, 22, 13, 30, tzinfo=timezone.utc),
        setup_metadata={},
    )
    base.update(overrides)
    return SignalScore(**base)


def _contract() -> SymbolContract:
    return SymbolContract(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=30,
        freeze_level=10,
    )


def _runtime(**overrides: object) -> RuntimeState:
    base = dict(
        symbol="XAUUSD",
        magic=20260522,
        starting_equity=10_000.0,
        start_time=datetime(2026, 5, 22, 0, 0, tzinfo=timezone.utc),
        regime=RegimeResult(Regime.TREND, 0.8, Direction.LONG, "", {}),
        aggression=AggressionState.NORMAL,
        position=PositionState(),
        equity_current=10_000.0,
        equity_peak=10_500.0,
        daily_start_equity=10_000.0,
        consecutive_wins=0,
        consecutive_losses=0,
        daily_pnl_r=0.0,
        total_trades_today=0,
        risk_params=RiskParams(),
    )
    base.update(overrides)
    return RuntimeState(**base)


class TestTSPRisk(unittest.TestCase):
    def test_compute_effective_equity_clamps_to_floor_and_peak(self) -> None:
        self.assertEqual(compute_effective_equity(6000.0, 10_000.0, 11_000.0), 7000.0)
        self.assertEqual(compute_effective_equity(12_000.0, 10_000.0, 11_000.0), 11_000.0)

    def test_compute_lot_size_quantizes_down(self) -> None:
        lot = compute_lot_size(3320.70, 3317.00, 0.9, 10_000.0, _contract())
        self.assertGreater(lot, 0.0)
        self.assertEqual(round(lot, 2), lot)

    def test_evaluate_aggression_transition_promotes_to_aggressive(self) -> None:
        result = evaluate_aggression_transition(
            _runtime(
                consecutive_wins=5,
                daily_pnl_r=2.6,
                equity_current=10_480.0,
                equity_peak=10_500.0,
            )
        )
        self.assertEqual(result.new_state, AggressionState.AGGRESSIVE)
        self.assertFalse(result.activate_kill)

    def test_check_pyramid_eligibility_accepts_strong_runner(self) -> None:
        signal = _signal()
        position = PositionState(
            phase=TradePhase.ENTERED,
            direction=Direction.LONG,
            module=Module.PULLBACK_CONTINUATION,
            layers=[
                LayerState(
                    ticket=1001,
                    direction=Direction.LONG,
                    entry_price=3318.0,
                    sl_price=3316.0,
                    tp_price=3321.6,
                    lot_size=0.10,
                    r_risk=0.9,
                    initial_r_distance=2.0,
                    open_time=datetime(2026, 5, 22, 13, 0, tzinfo=timezone.utc),
                    layer_index=0,
                    module=Module.PULLBACK_CONTINUATION,
                    setup_id="layersetup1",
                )
            ],
            _unrealized_pnl_r=1.2,
        )
        result = check_pyramid_eligibility(position, signal, AggressionState.NORMAL, _runtime())
        self.assertTrue(result.allowed)

    def test_evaluate_risk_returns_enter_for_valid_flat_book(self) -> None:
        decision = evaluate_risk(_signal(), _snapshot(), _runtime(), _contract())
        self.assertEqual(decision.action, "ENTER")
        self.assertGreater(decision.lot_size, 0.0)

    def test_evaluate_emergency_exit_flattens_news_window_fresh_trade(self) -> None:
        position = PositionState(
            phase=TradePhase.ENTERED,
            direction=Direction.LONG,
            module=Module.PULLBACK_CONTINUATION,
            layers=[
                LayerState(
                    ticket=1001,
                    direction=Direction.LONG,
                    entry_price=3318.0,
                    sl_price=3316.0,
                    tp_price=3321.6,
                    lot_size=0.10,
                    r_risk=0.9,
                    initial_r_distance=2.0,
                    open_time=datetime(2026, 5, 22, 13, 0, tzinfo=timezone.utc),
                    layer_index=0,
                    module=Module.PULLBACK_CONTINUATION,
                    setup_id="layersetup1",
                    bars_in_trade=1,
                )
            ],
            _unrealized_pnl_r=0.1,
        )
        decision = evaluate_emergency_exit(
            _runtime(position=position, equity_current=10_480.0, equity_peak=10_500.0),
            _snapshot(is_news_window=True),
            spread_persist_bars=0,
        )
        self.assertTrue(decision.should_exit)
        self.assertEqual(decision.reason, "news_window")
