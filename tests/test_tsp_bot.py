from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import unittest

from tsp.bot import TSPBot
from tsp.config import (
    AppConfig,
    BotConfig,
    CompetitionConfig,
    ExecutionConfig,
    LifecycleConfig,
    MT5Credentials,
    RegimeConfig,
    SignalConfig,
)
from tsp.data_pipeline import SnapshotBuildConfig
from tsp.state import RiskParams, TradePhase


@dataclass
class FakeSymbolInfo:
    digits: int = 2
    point: float = 0.01
    spread: int = 12
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    trade_tick_size: float = 0.01
    trade_tick_value: float = 1.0
    trade_stops_level: int = 30
    trade_freeze_level: int = 10


class FakeBotAdapter:
    def __init__(self) -> None:
        self.now = datetime(2026, 5, 22, 12, 30, tzinfo=timezone.utc)
        self.active_time = self.now
        self.symbol_info = FakeSymbolInfo()
        self.positions: dict[int, dict[str, Any]] = {}

    def get_rates(self, symbol: str, timeframe: str, count: int):
        del symbol
        step_minutes = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}[timeframe]
        current = self.active_time.replace(second=0, microsecond=0)
        start = current - timedelta(minutes=step_minutes * (count - 1))
        base = {"M1": 3300.0, "M5": 3300.8, "M15": 3288.0, "H1": 3260.0}[timeframe]
        bars = []
        for idx in range(count):
            open_price = base + (idx * 0.6)
            close_price = open_price + 0.35
            bars.append(
                {
                    "time": int((start + timedelta(minutes=step_minutes * idx)).timestamp()),
                    "open": open_price,
                    "high": close_price + 0.20,
                    "low": open_price - 0.15,
                    "close": close_price,
                    "tick_volume": 150 + idx,
                }
            )
        return bars

    def get_latest_tick(self, symbol: str):
        del symbol
        return type("Tick", (), {"bid": 3323.40, "ask": 3323.55, "time": self.now.timestamp()})()

    def get_symbol_info(self, symbol: str):
        del symbol
        return self.symbol_info

    def get_server_time(self) -> datetime:
        current = self.now
        self.active_time = current
        self.now = self.now + timedelta(seconds=5)
        return current

    def get_equity(self) -> float:
        return 10_000.0

    def send_market_order(self, symbol, action, volume, sl, tp, comment, magic):
        del action, comment, magic
        ticket = 202 + len(self.positions)
        self.positions[ticket] = {
            "ticket": ticket,
            "symbol": symbol,
            "volume": volume,
            "sl": sl,
            "tp": tp or 0.0,
        }
        return {"retcode": 10009, "order": ticket, "deal": ticket, "price": 3323.55, "volume": volume}

    def modify_position(self, ticket, sl, tp):
        if ticket in self.positions:
            if sl is not None:
                self.positions[ticket]["sl"] = sl
            if tp is not None:
                self.positions[ticket]["tp"] = tp
        return {"retcode": 10009, "sl_confirmed": True, "tp_confirmed": True}

    def partial_close(self, ticket, symbol, volume, comment):
        del symbol, comment
        if ticket in self.positions:
            self.positions[ticket]["volume"] = max(0.0, self.positions[ticket]["volume"] - volume)
        return {"retcode": 10009, "volume_executed": 0.1}

    def get_position_by_ticket(self, ticket):
        return self.positions.get(ticket)

    def get_all_positions(self, magic):
        del magic
        return list(self.positions.values())

    def emergency_close(self, ticket, symbol, volume, reason):
        del symbol, volume, reason
        self.positions.pop(ticket, None)
        return {}


def _config() -> AppConfig:
    return AppConfig(
        config_version=1,
        supported_symbol="XAUUSD",
        config_path=None,  # type: ignore[arg-type]
        fingerprint="test",
        credentials=MT5Credentials("123", "secret", "server", ""),
        bot=BotConfig(
            magic_number=20260522,
            poll_interval_seconds=5,
            max_consecutive_bar_errors=5,
            db_path=None,  # type: ignore[arg-type]
            log_dir=None,  # type: ignore[arg-type]
            state_dir=None,  # type: ignore[arg-type]
            config_last_known_good_path=None,  # type: ignore[arg-type]
            expert_mode=False,
        ),
        risk=RiskParams(),
        regime=RegimeConfig(
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
        ),
        signal=SignalConfig(
            threshold_trend=40.0,
            threshold_breakout=45.0,
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
        ),
        lifecycle=LifecycleConfig(
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
        ),
        execution=ExecutionConfig(
            signal_ttl_seconds=90,
            spread_hard_veto_ratio=2.50,
            spread_soft_penalty_ratio=1.30,
            max_slippage_ticks=10.0,
            market_order_retry_count=2,
            market_order_timeout_seconds=3,
            dedup_ttl_seconds=120,
        ),
        competition=CompetitionConfig(
            total_days=30,
            target_total_pnl_r=25.0,
            lead_protect_r=12.0,
            sprint_pct=0.15,
            session_risk_budget_r=3.0,
            hunt_aggression_bias=0.4,
            protect_aggression_bias=-0.6,
            sprint_aggression_bias=0.8,
            hunt_threshold_modifier=-4.0,
            protect_threshold_modifier=6.0,
            sprint_threshold_modifier=-8.0,
            circuit_loss_count=3,
            circuit_session_pnl_r=-2.0,
        ),
    )


class TestTSPBot(unittest.TestCase):
    def test_bootstrap_initializes_runtime_and_registry(self) -> None:
        bot = TSPBot(config=_config(), adapter=FakeBotAdapter(), snapshot_config=SnapshotBuildConfig())
        bot.bootstrap()

        self.assertIsNotNone(bot.runtime)
        self.assertIsNotNone(bot.contract)
        self.assertIsNotNone(bot.registry)

    def test_process_bar_generates_position_and_updates_last_bar(self) -> None:
        bot = TSPBot(config=_config(), adapter=FakeBotAdapter(), snapshot_config=SnapshotBuildConfig())
        result = bot.process_bar()

        self.assertTrue(result.signal_generated)
        self.assertTrue(result.executed)
        self.assertTrue(result.processed_new_bar)
        self.assertFalse(result.duplicate_bar_skip)
        self.assertIsNotNone(result.bar_timestamp)
        self.assertIsNotNone(result.regime_confidence)
        assert bot.runtime is not None
        self.assertGreaterEqual(bot.runtime.position.layer_count, 1)
        self.assertIn(bot.runtime.position.phase, {TradePhase.ENTERED, TradePhase.PYRAMIDED})
        self.assertIsNotNone(bot.last_bar_time)

    def test_process_bar_skips_duplicate_closed_bar(self) -> None:
        adapter = FakeBotAdapter()
        bot = TSPBot(config=_config(), adapter=adapter, snapshot_config=SnapshotBuildConfig())
        first = bot.process_bar()
        adapter.now = bot.last_bar_time + timedelta(minutes=1, seconds=30)

        second = bot.process_bar()

        self.assertTrue(first.processed_new_bar)
        self.assertFalse(second.processed_new_bar)
        self.assertTrue(second.duplicate_bar_skip)
        self.assertIsNotNone(second.bar_timestamp)
