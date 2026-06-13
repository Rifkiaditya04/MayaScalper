from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import unittest

from tsp.data_pipeline import SnapshotBuildConfig, build_market_snapshot, build_symbol_contract


@dataclass
class FakeTick:
    bid: float
    ask: float


@dataclass
class FakeSymbolInfo:
    digits: int = 2
    point: float = 0.01
    spread: int = 25
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    trade_tick_size: float = 0.01
    trade_tick_value: float = 1.0
    trade_stops_level: int = 30
    trade_freeze_level: int = 10


class FakeAdapter:
    def __init__(self) -> None:
        self._start = datetime(2026, 5, 22, 13, 32, tzinfo=timezone.utc)
        self._symbol_info = FakeSymbolInfo()

    def get_rates(self, symbol: str, timeframe: str, count: int):
        del symbol
        step_minutes = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}[timeframe]
        start = self._start - timedelta(minutes=step_minutes * (count - 1))
        base = {"M1": 3300.0, "M5": 3295.0, "M15": 3288.0, "H1": 3260.0}[timeframe]
        bars = []
        for idx in range(count):
            open_price = base + (idx * 0.6)
            close_price = open_price + 0.25 + ((idx % 3) * 0.05)
            high = close_price + 0.20
            low = open_price - 0.15
            bars.append(
                {
                    "time": int((start + timedelta(minutes=step_minutes * idx)).timestamp()),
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close_price,
                    "tick_volume": 100 + idx,
                }
            )
        return bars

    def get_latest_tick(self, symbol: str):
        del symbol
        return FakeTick(bid=3323.40, ask=3323.65)

    def get_symbol_info(self, symbol: str):
        del symbol
        return self._symbol_info

    def get_server_time(self) -> datetime:
        return self._start


class FormingBarAdapter(FakeAdapter):
    def get_rates(self, symbol: str, timeframe: str, count: int):
        bars = super().get_rates(symbol, timeframe, count)
        step_minutes = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}[timeframe]
        bars[-1]["time"] = int(self._start.timestamp())
        bars[-2]["time"] = int((self._start - timedelta(minutes=step_minutes)).timestamp())
        return bars


class TestTSPDataPipeline(unittest.TestCase):
    def test_build_market_snapshot_from_adapter(self) -> None:
        snapshot = build_market_snapshot(
            FakeAdapter(),
            symbol="XAUUSD",
            cfg=SnapshotBuildConfig(),
            is_news_window=False,
        )

        self.assertEqual(snapshot.symbol, "XAUUSD")
        self.assertEqual(snapshot.session, "OVERLAP")
        self.assertFalse(snapshot.is_news_window)
        self.assertGreater(snapshot.atr_m1, 0.0)
        self.assertGreater(snapshot.atr_m1_base, 0.0)
        self.assertGreater(snapshot.adx_m5, 0.0)
        self.assertGreater(snapshot.adx_m15, 0.0)
        self.assertGreater(snapshot.adx_h1, 0.0)
        self.assertEqual(len(snapshot.m1_closes_recent), 8)
        self.assertGreaterEqual(snapshot.ask, snapshot.bid)
        self.assertGreater(snapshot.swing_high_m5, snapshot.swing_low_m5)

    def test_build_symbol_contract_from_symbol_info(self) -> None:
        contract = build_symbol_contract("XAUUSD", FakeSymbolInfo())

        self.assertEqual(contract.symbol, "XAUUSD")
        self.assertEqual(contract.tick_size, 0.01)
        self.assertEqual(contract.stops_level, 30)
        self.assertEqual(contract.freeze_level, 10)

    def test_build_market_snapshot_uses_last_closed_bar_not_forming_bar(self) -> None:
        adapter = FormingBarAdapter()
        snapshot = build_market_snapshot(
            adapter,
            symbol="XAUUSD",
            cfg=SnapshotBuildConfig(),
            server_time=adapter.get_server_time(),
            is_news_window=False,
        )

        self.assertTrue(snapshot.is_closed_bar)
        self.assertEqual(snapshot.timestamp, adapter._start - timedelta(minutes=1))
        self.assertEqual(snapshot.m1.timestamp, snapshot.timestamp)
        self.assertEqual(snapshot.source_server_time, adapter._start)


if __name__ == "__main__":
    unittest.main()
