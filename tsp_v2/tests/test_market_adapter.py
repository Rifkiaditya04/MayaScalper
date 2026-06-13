from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import unittest

from tsp_v2.adapters.market_adapter import MT5MarketAdapter
from tsp_v2.adapters.mt5_bridge import MT5BridgeError, MT5BridgeStatus
from tsp_v2.config_schema import ConfigValidationError
from tsp_v2.enums import HealthState


@dataclass
class FakeBridge:
    tick_time: datetime
    heartbeat_status: MT5BridgeStatus
    contract_visible: bool = True
    contract_failure: MT5BridgeError | None = None

    def heartbeat(self) -> MT5BridgeStatus:
        return self.heartbeat_status

    def get_latest_tick(self, symbol: str) -> dict[str, object]:
        return {
            "symbol": symbol.upper(),
            "timestamp": self.tick_time,
            "bid": 2350.0,
            "ask": 2350.2,
        }

    def get_rates(self, symbol: str, timeframe: str, count: int) -> tuple[dict[str, object], ...]:
        del symbol, timeframe
        bar_time = self.tick_time - timedelta(minutes=count)
        return tuple(
            {
                "symbol": "XAUUSD",
                "time": bar_time + timedelta(minutes=index),
                "open": 2300.0 + index,
                "high": 2300.5 + index,
                "low": 2299.5 + index,
                "close": 2300.2 + index,
                "tick_volume": 100 + index,
            }
            for index in range(count)
        )

    def query_symbol_contract(self, symbol: str) -> dict[str, object]:
        if self.contract_failure is not None:
            raise self.contract_failure
        return {
            "symbol": symbol.upper(),
            "visible": self.contract_visible,
            "point": 0.1,
            "trade_tick_size": 0.1,
            "trade_tick_value": 1.0,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "trade_stops_level": 20,
            "trade_freeze_level": 0,
            "spread": 2,
            "digits": 1,
        }


class MarketAdapterTests(unittest.TestCase):
    def test_adapter_normalizes_contract_and_broker_time(self) -> None:
        tick_time = datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
        bridge = FakeBridge(
            tick_time=tick_time,
            heartbeat_status=MT5BridgeStatus(
                ok=True,
                failure_class="",
                response_class="OK",
                retryable=False,
                fatal=False,
                message="ok",
                terminal_ready=True,
                broker_ready=True,
            ),
        )
        adapter = MT5MarketAdapter(bridge=bridge, primary_symbol="xauusd")

        self.assertEqual(adapter.get_broker_time(), tick_time)
        rates = adapter.get_rates("xauusd", "M5", 3)
        self.assertIsInstance(rates, list)
        self.assertEqual(len(rates), 3)

        contract = adapter.get_symbol_contract("xauusd")
        self.assertEqual(contract["symbol"], "XAUUSD")
        self.assertEqual(contract["tick_size"], 0.1)
        self.assertEqual(contract["min_lot"], 0.01)
        self.assertEqual(contract["stop_level_points"], 20)

        status = adapter.market_status(now_utc=tick_time + timedelta(seconds=2))
        self.assertTrue(status.ok)
        self.assertEqual(status.health, HealthState.GREEN)
        self.assertEqual(status.response_class, "OK")
        self.assertEqual(status.symbol, "XAUUSD")

    def test_adapter_marks_stale_market_yellow(self) -> None:
        tick_time = datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
        bridge = FakeBridge(
            tick_time=tick_time,
            heartbeat_status=MT5BridgeStatus(
                ok=True,
                failure_class="",
                response_class="OK",
                retryable=False,
                fatal=False,
                message="ok",
                terminal_ready=True,
                broker_ready=True,
            ),
        )
        adapter = MT5MarketAdapter(bridge=bridge, primary_symbol="XAUUSD")

        status = adapter.market_status(now_utc=tick_time + timedelta(seconds=7))
        self.assertFalse(status.ok)
        self.assertEqual(status.health, HealthState.YELLOW)
        self.assertEqual(status.response_class, "DEGRADE_SYMBOL")
        self.assertGreater(status.tick_age_seconds or 0.0, 5.0)

    def test_adapter_marks_heartbeat_failure_red(self) -> None:
        tick_time = datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
        bridge = FakeBridge(
            tick_time=tick_time,
            heartbeat_status=MT5BridgeStatus(
                ok=False,
                failure_class="BROKER_DISCONNECTED",
                response_class="TRIGGER_RECONCILIATION",
                retryable=True,
                fatal=False,
                message="broker down",
                terminal_ready=True,
                broker_ready=False,
            ),
        )
        adapter = MT5MarketAdapter(bridge=bridge, primary_symbol="XAUUSD")

        status = adapter.market_status(now_utc=tick_time)
        self.assertFalse(status.ok)
        self.assertEqual(status.health, HealthState.RED)
        self.assertEqual(status.failure_class, "BROKER_DISCONNECTED")
        self.assertEqual(status.response_class, "TRIGGER_RECONCILIATION")

    def test_adapter_rejects_bad_primary_symbol(self) -> None:
        with self.assertRaises(ConfigValidationError):
            MT5MarketAdapter(
                bridge=FakeBridge(
                    tick_time=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                    heartbeat_status=MT5BridgeStatus(
                        ok=True,
                        failure_class="",
                        response_class="OK",
                        retryable=False,
                        fatal=False,
                        message="ok",
                    ),
                ),
                primary_symbol="   ",
            )


if __name__ == "__main__":
    unittest.main()
