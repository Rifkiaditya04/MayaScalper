from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from tests.test_tsp_bot import _config
from tsp.backtest import (
    BacktestAdapter,
    BacktestExecutionModel,
    BacktestRunner,
    BacktestSymbolInfo,
)
from tsp.data_pipeline import SnapshotBuildConfig


def _m1_bars(count: int = 2_600) -> list[dict[str, float | datetime]]:
    start = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    bars: list[dict[str, float | datetime]] = []
    price = 3300.0
    for idx in range(count):
        drift = 0.18 if idx < count // 2 else -0.12
        wave = ((idx % 11) - 5) * 0.03
        open_price = price
        close_price = price + drift + wave
        high_price = max(open_price, close_price) + 0.22
        low_price = min(open_price, close_price) - 0.19
        bars.append(
            {
                "time": start + timedelta(minutes=idx),
                "open": round(open_price, 4),
                "high": round(high_price, 4),
                "low": round(low_price, 4),
                "close": round(close_price, 4),
                "tick_volume": 140.0 + (idx % 17),
            }
        )
        price = close_price
    return bars


class TestTSPBacktest(unittest.TestCase):
    def test_adapter_exposes_marketdata_contract(self) -> None:
        adapter = BacktestAdapter(
            symbol="XAUUSD",
            m1_bars=_m1_bars(),
            symbol_info=BacktestSymbolInfo(spread=20),
        )
        adapter.seek(2_459)

        m1 = adapter.get_rates("XAUUSD", "M1", 40)
        m5 = adapter.get_rates("XAUUSD", "M5", 40)
        m15 = adapter.get_rates("XAUUSD", "M15", 40)
        h1 = adapter.get_rates("XAUUSD", "H1", 40)
        tick = adapter.get_latest_tick("XAUUSD")

        self.assertEqual(len(m1), 40)
        self.assertEqual(len(m5), 40)
        self.assertEqual(len(m15), 40)
        self.assertEqual(len(h1), 40)
        self.assertLess(tick.bid, tick.ask)
        self.assertEqual(adapter.get_server_time(), m1[-1]["time"])

    def test_execution_simulation_supports_partial_fills_and_trade_ledger(self) -> None:
        adapter = BacktestAdapter(
            symbol="XAUUSD",
            m1_bars=_m1_bars(),
            execution_model=BacktestExecutionModel(
                entry_slippage_ticks=1.0,
                exit_slippage_ticks=1.0,
                partial_fill_every=1,
                partial_fill_ratio=0.5,
            ),
        )
        adapter.seek(2_459)
        result = adapter.send_market_order(
            symbol="XAUUSD",
            action="BUY",
            volume=0.20,
            sl=3298.0,
            tp=None,
            comment="unit",
            magic=20260522,
        )
        ticket = int(result["deal"])
        close_result = adapter.emergency_close(
            ticket=ticket,
            symbol="XAUUSD",
            volume=float(result["volume"]),
            reason="unit_exit",
        )

        self.assertEqual(int(result["retcode"]), 10010)
        self.assertEqual(int(close_result["retcode"]), 10009)
        self.assertEqual(len(adapter.closed_trades), 1)
        self.assertEqual(adapter.closed_trades[0].exit_reason, "unit_exit")

    def test_runner_returns_structured_report(self) -> None:
        config = replace(_config(), bot=replace(_config().bot, db_path=None))
        adapter = BacktestAdapter(
            symbol="XAUUSD",
            m1_bars=_m1_bars(),
            execution_model=BacktestExecutionModel(
                entry_slippage_ticks=1.5,
                exit_slippage_ticks=1.0,
                spread_multiplier=1.1,
                partial_fill_every=3,
                partial_fill_ratio=0.5,
                reject_every=7,
                latency_ms=175.0,
            ),
        )
        runner = BacktestRunner(
            config=config,
            adapter=adapter,
            snapshot_config=SnapshotBuildConfig(),
        )

        report = runner.run(max_steps=25)

        self.assertEqual(report.bars_processed, 25)
        self.assertIn("execution_model", report.assumptions)
        self.assertEqual(report.assumptions["auto_broker_tp_sl"], False)
        self.assertGreaterEqual(report.final_equity, 0.0)
        self.assertGreaterEqual(report.max_drawdown_pct, 0.0)
        self.assertGreaterEqual(report.executions_attempted, report.executions_filled)


if __name__ == "__main__":
    unittest.main()
