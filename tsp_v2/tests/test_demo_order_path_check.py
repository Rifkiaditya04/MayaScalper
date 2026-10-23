from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from tools.demo_order_path_check import DemoOrderPathSafetyError, validate_demo_order_path_preconditions


class FakeDemoBridge:
    def __init__(self) -> None:
        self.account = {"login": 123456, "trade_mode": 0}
        self.positions: tuple[dict[str, object], ...] = ()
        self.orders: tuple[dict[str, object], ...] = ()
        self.now = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)

    def query_account(self):
        return self.account

    def query_positions(self):
        return self.positions

    def query_orders(self):
        return self.orders

    def query_symbol_contract(self, symbol):
        return {"symbol": symbol, "point": 0.00001, "volume_min": 0.01, "trade_stops_level": 10}

    def latest_tick(self, symbol):
        return {"symbol": symbol, "bid": 1.10000, "ask": 1.10010, "time": self.now}


class DemoOrderPathCheckTests(unittest.TestCase):
    def test_demo_flat_fresh_account_passes_read_only_preconditions(self) -> None:
        bridge = FakeDemoBridge()

        account, contract, tick = validate_demo_order_path_preconditions(bridge, symbol="EURUSD", now_utc=bridge.now)

        self.assertEqual(account["trade_mode"], 0)
        self.assertEqual(contract["symbol"], "EURUSD")
        self.assertEqual(tick["symbol"], "EURUSD")

    def test_non_demo_account_is_fail_closed_before_order_path(self) -> None:
        bridge = FakeDemoBridge()
        bridge.account["trade_mode"] = 2

        with self.assertRaisesRegex(DemoOrderPathSafetyError, "trade_mode=0"):
            validate_demo_order_path_preconditions(bridge, symbol="EURUSD", now_utc=bridge.now)

    def test_positions_pending_orders_and_stale_tick_are_fail_closed(self) -> None:
        for label, configure in (
            ("position", lambda bridge: setattr(bridge, "positions", ({"ticket": 1},))),
            ("order", lambda bridge: setattr(bridge, "orders", ({"ticket": 2},))),
            ("stale", lambda bridge: setattr(bridge, "now", bridge.now - timedelta(seconds=6))),
        ):
            with self.subTest(label=label):
                bridge = FakeDemoBridge()
                configure(bridge)
                with self.assertRaises(DemoOrderPathSafetyError):
                    validate_demo_order_path_preconditions(
                        bridge,
                        symbol="EURUSD",
                        now_utc=datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc),
                    )
