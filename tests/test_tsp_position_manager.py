from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import unittest

from tsp.config import BotConfig, LifecycleConfig
from tsp.data_pipeline import SymbolContract
from tsp.position_manager import (
    LayerMutation,
    LifecycleAction,
    evaluate_lifecycle,
    recover_orphans,
)
from tsp.state import (
    AggressionState,
    Direction,
    LayerState,
    Module,
    PositionState,
    Regime,
    RegimeResult,
    RiskParams,
    RuntimeState,
    CandleData,
    MarketSnapshot,
)


@dataclass
class FakeLifecycleAdapter:
    modify_response: dict
    partial_response: dict
    broker_position: dict | None

    def modify_position(self, ticket, sl, tp):
        del ticket, sl, tp
        return dict(self.modify_response)

    def partial_close(self, ticket, symbol, volume, comment):
        del ticket, symbol, volume, comment
        return dict(self.partial_response)

    def get_position_by_ticket(self, ticket):
        del ticket
        return None if self.broker_position is None else dict(self.broker_position)


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


def _layer(**overrides: object) -> LayerState:
    base = dict(
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
        partial_taken=False,
        bars_in_trade=4,
        tp_attach_attempts=0,
    )
    base.update(overrides)
    return LayerState(**base)


def _runtime(layer: LayerState, snap: MarketSnapshot) -> RuntimeState:
    position = PositionState(
        direction=layer.direction,
        module=layer.module,
        layers=[layer],
    )
    position.update_unrealized(layer.unrealized_r(snap.bid))
    return RuntimeState(
        symbol="XAUUSD",
        magic=20260522,
        starting_equity=10_000.0,
        start_time=datetime(2026, 5, 22, 0, 0, tzinfo=timezone.utc),
        snap=snap,
        regime=RegimeResult(Regime.TREND, 0.8, Direction.LONG, "", {}),
        aggression=AggressionState.NORMAL,
        position=position,
        equity_current=10_000.0,
        equity_peak=10_500.0,
        daily_start_equity=10_000.0,
        risk_params=RiskParams(),
    )


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


def _cfg() -> LifecycleConfig:
    return LifecycleConfig(
        tp_rr_pullback=1.8,
        tp_rr_breakout=2.2,
        be_trigger_r=0.80,
        be_buffer_ticks=3.0,
        trail_trigger_r=1.50,
        trail_atr_multiplier=1.20,
        trail_min_improve_ticks=5.0,
        partial_trigger_r=1.00,
        partial_size_ratio=0.50,
        tp_attach_retry_limit=3,
        orphan_unknown_action="FLATTEN",
    )


class TestTSPPositionManager(unittest.TestCase):
    def test_evaluate_lifecycle_attaches_missing_tp_before_other_actions(self) -> None:
        layer = _layer(tp_price=None, tp_attach_attempts=0)
        adapter = FakeLifecycleAdapter(
            modify_response={"retcode": 10009, "tp_confirmed": True, "sl_confirmed": False},
            partial_response={"retcode": 10009, "volume_executed": 0.05},
            broker_position={"ticket": 1001, "tp": 0.0},
        )
        result = evaluate_lifecycle(adapter, _runtime(layer, _snapshot()), _cfg(), _contract())

        self.assertEqual(result.events[0].action, LifecycleAction.TP_ATTACHED)
        self.assertTrue(any(m.new_tp_price is not None for m in result.mutations))

    def test_evaluate_lifecycle_moves_be_and_partials(self) -> None:
        snap = _snapshot(bid=3320.8, ask=3320.95)
        layer = _layer(sl_price=3316.0, tp_price=3321.6, bars_in_trade=4)
        adapter = FakeLifecycleAdapter(
            modify_response={"retcode": 10009, "tp_confirmed": True, "sl_confirmed": True},
            partial_response={"retcode": 10009, "volume_executed": 0.05},
            broker_position={"ticket": 1001, "tp": 3321.6},
        )
        result = evaluate_lifecycle(adapter, _runtime(layer, snap), _cfg(), _contract())

        actions = {event.action for event in result.events}
        self.assertIn(LifecycleAction.SL_MOVED_TO_BE, actions)
        self.assertIn(LifecycleAction.PARTIAL_CLOSED, actions)

    def test_recover_orphans_flags_no_sl_and_unknown(self) -> None:
        bot_cfg = BotConfig(
            magic_number=20260522,
            poll_interval_seconds=5,
            max_consecutive_bar_errors=5,
            db_path=None,  # type: ignore[arg-type]
            log_dir=None,  # type: ignore[arg-type]
            state_dir=None,  # type: ignore[arg-type]
            config_last_known_good_path=None,  # type: ignore[arg-type]
            expert_mode=False,
        )
        result = recover_orphans(
            [
                {"ticket": 1, "sl": 0.0, "tp": 0.0},
                {"ticket": 2, "sl": 3310.0, "tp": 0.0},
            ],
            known_tickets={1},
            bot_cfg=bot_cfg,
            lifecycle_cfg=_cfg(),
        )

        self.assertTrue(result.signal_kill)
        self.assertEqual(result.kill_reason, "orphan_no_sl")
        self.assertTrue(any(event.action == LifecycleAction.ORPHAN_RECOVERED for event in result.events))
