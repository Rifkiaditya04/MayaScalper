from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tsp_v2.adapters.mt5_bridge import MT5Bridge


@dataclass
class FakeOrderResult:
    retcode: int
    order: int = 0
    deal: int = 0
    comment: str = ""


class FakeMT5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 10
    TRADE_ACTION_REMOVE = 11
    TRADE_ACTION_MODIFY = 12
    ORDER_TIME_GTC = 20
    ORDER_FILLING_RETURN = 30
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010
    TRADE_RETCODE_PLACED = 10011
    TRADE_RETCODE_REQUOTE = 10004
    TRADE_RETCODE_PRICE_CHANGED = 10020
    TRADE_RETCODE_INVALID_VOLUME = 10013
    TRADE_RETCODE_MARKET_CLOSED = 10018
    TRADE_RETCODE_TRADE_CONTEXT_BUSY = 10025
    TRADE_RETCODE_CONNECTION = 10031

    def __init__(
        self,
        *,
        tick_time: datetime | None = None,
        tick_time_missing: bool = False,
        rates_time: datetime | None = None,
        rates_failures: int = 0,
        post_reconnect_rate_failures: int = 0,
        post_reconnect_last_error: tuple[int, str] | None = None,
    ) -> None:
        self.initialize_calls: list[dict[str, object]] = []
        self.login_calls: list[dict[str, object]] = []
        self.shutdown_calls = 0
        self.selected_symbols: list[str] = []
        self.order_requests: list[dict[str, object]] = []
        self.order_send_result = FakeOrderResult(self.TRADE_RETCODE_DONE, order=111, deal=222, comment="done")
        self._last_error = (0, "")
        self._visible_symbols = set()
        self._ipc_loss_requires_reconnect = False
        self._post_reconnect_rate_failures_remaining = max(0, int(post_reconnect_rate_failures))
        self._post_reconnect_last_error = post_reconnect_last_error
        self._account_info = SimpleNamespace(
            balance=100000.0,
            equity=100500.0,
            margin=5000.0,
            free_margin=95500.0,
            login=123456,
            server="Demo-Server",
        )
        self._terminal_info = SimpleNamespace(connected=True, trade_allowed=True, trade_expert=True)
        self._tick_time = None if tick_time_missing else (tick_time or datetime(2026, 5, 29, 10, 0, tzinfo=timezone.utc))
        self._rates_time = rates_time or datetime(2026, 5, 29, 9, 0, tzinfo=timezone.utc)
        self._rates_failures_remaining = max(0, int(rates_failures))
        self._positions = (
            SimpleNamespace(
                ticket=10,
                symbol="XAUUSD",
                type=self.ORDER_TYPE_BUY,
                volume=0.20,
                price_open=2350.10,
                sl=2345.0,
                tp=2360.0,
                profit=12.3,
                time=int(datetime(2026, 5, 29, 10, 0, tzinfo=timezone.utc).timestamp()),
            ),
        )
        self._orders: tuple[object, ...] = ()
        self._deals: tuple[object, ...] = ()

    def initialize(self, **kwargs):
        self.initialize_calls.append(dict(kwargs))
        self._last_error = (0, "")
        return True

    def login(self, **kwargs):
        self.login_calls.append(dict(kwargs))
        return True

    def shutdown(self):
        self.shutdown_calls += 1
        return True

    def last_error(self):
        return self._last_error

    def account_info(self):
        return self._account_info

    def terminal_info(self):
        return self._terminal_info

    def symbol_info(self, symbol):
        visible = symbol.upper() in self._visible_symbols
        return SimpleNamespace(
            symbol=symbol.upper(),
            point=0.1,
            trade_tick_size=0.1,
            trade_tick_value=1.0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_stops_level=20,
            trade_freeze_level=0,
            spread=2,
            digits=1,
            visible=visible,
        )

    def symbol_select(self, symbol, enable):
        if enable:
            self._visible_symbols.add(symbol.upper())
            self.selected_symbols.append(symbol.upper())
            return True
        self._visible_symbols.discard(symbol.upper())
        return True

    def symbol_info_tick(self, symbol):
        del symbol
        payload = {
            "bid": 2350.0,
            "ask": 2350.2,
            "last": 2350.1,
            "volume": 1234,
        }
        if self._tick_time is not None:
            payload["time"] = self._tick_time
        return SimpleNamespace(**payload)

    def copy_ticks_from(self, symbol, date_from, count, flags):
        del symbol, date_from, flags
        if self._tick_time is None or count <= 0:
            return ()
        return (
            SimpleNamespace(
                time=int(self._tick_time.timestamp()),
                time_msc=int(self._tick_time.timestamp() * 1000),
                bid=2350.0,
                ask=2350.2,
                last=2350.1,
                volume=1234,
            ),
        )

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        del symbol, timeframe, start_pos
        if self._ipc_loss_requires_reconnect and len(self.initialize_calls) < 2:
            self._last_error = (-10004, "No IPC connection")
            return ()
        if self._ipc_loss_requires_reconnect and len(self.initialize_calls) >= 2 and self._post_reconnect_rate_failures_remaining > 0:
            self._post_reconnect_rate_failures_remaining -= 1
            if self._post_reconnect_last_error is not None:
                self._last_error = self._post_reconnect_last_error
            return ()
        if self._rates_failures_remaining > 0:
            self._rates_failures_remaining -= 1
            return ()
        base = self._rates_time - timedelta(minutes=max(count - 1, 0))
        return [
            SimpleNamespace(
                time=int((base + timedelta(minutes=i)).timestamp()),
                open=100.0 + i,
                high=100.5 + i,
                low=99.5 + i,
                close=100.2 + i,
                tick_volume=100 + i,
                real_volume=50 + i,
            )
            for i in range(count)
        ]

    def positions_get(self, symbol=None, ticket=None):
        records = self._positions
        if ticket is not None:
            records = tuple(record for record in records if getattr(record, "ticket", None) == ticket)
        if symbol is not None:
            records = tuple(record for record in records if getattr(record, "symbol", "").upper() == symbol.upper())
        return records

    def orders_get(self, symbol=None, ticket=None):
        del ticket
        if symbol is None:
            return self._orders
        return tuple(record for record in self._orders if getattr(record, "symbol", "").upper() == symbol.upper())

    def history_deals_get(self, **kwargs):
        del kwargs
        return self._deals

    def order_send(self, request):
        self.order_requests.append(dict(request))
        return self.order_send_result


class MT5BridgeTests(unittest.TestCase):
    def test_connect_query_and_close_position_roundtrip(self) -> None:
        fake = FakeMT5()
        bridge = MT5Bridge(
            terminal_path=Path(r"C:\MT5\terminal64.exe"),
            login="123456",
            password="secret",
            server="Demo-Server",
            mt5_module=fake,
        )

        status = bridge.connect()
        self.assertTrue(status.ok)
        self.assertTrue(bridge.connected)
        self.assertTrue(bridge.terminal_ready)
        self.assertTrue(bridge.broker_ready)
        self.assertEqual(fake.initialize_calls[0]["path"], r"C:\MT5\terminal64.exe")
        self.assertEqual(fake.login_calls[0]["server"], "Demo-Server")

        account = bridge.query_account()
        self.assertEqual(account["equity"], 100500.0)

        contract = bridge.query_symbol_contract("xauusd")
        self.assertEqual(contract["symbol"], "XAUUSD")
        self.assertTrue(fake.selected_symbols)

        tick = bridge.latest_tick("XAUUSD")
        self.assertEqual(tick["symbol"], "XAUUSD")
        self.assertIsNotNone(tick["time"].tzinfo)

        rates = bridge.rates("XAUUSD", "M5", 3)
        self.assertEqual(len(rates), 3)
        self.assertEqual(rates[0]["timeframe"], "M5")
        self.assertIsNotNone(rates[0]["time"].tzinfo)

        heartbeat = bridge.heartbeat()
        self.assertTrue(heartbeat.ok)
        self.assertEqual(heartbeat.response_class, "OK")

        result = bridge.close_position(10, comment="close_test")
        self.assertTrue(result.ok)
        self.assertEqual(result.response_class, "OK")
        self.assertEqual(fake.order_requests[-1]["position"], 10)
        self.assertEqual(fake.order_requests[-1]["type"], fake.ORDER_TYPE_SELL)
        self.assertEqual(fake.order_requests[-1]["symbol"], "XAUUSD")
        self.assertEqual(fake.order_requests[-1]["volume"], 0.2)
        self.assertEqual(fake.order_requests[-1]["price"], 2350.0)

    def test_retryable_trade_classification(self) -> None:
        fake = FakeMT5()
        fake.order_send_result = FakeOrderResult(fake.TRADE_RETCODE_TRADE_CONTEXT_BUSY, order=0, comment="busy")
        bridge = MT5Bridge(mt5_module=fake)
        bridge.connect()
        result = bridge.place_order(
            {
                "action": fake.TRADE_ACTION_DEAL,
                "symbol": "XAUUSD",
                "volume": 0.10,
                "type": fake.ORDER_TYPE_BUY,
            }
        )
        self.assertFalse(result.ok)
        self.assertTrue(result.retryable)
        self.assertEqual(result.failure_class, "TRADE_CONTEXT_BUSY")
        self.assertEqual(result.response_class, "BLOCK_EXECUTION")

    def test_non_retryable_trade_classification(self) -> None:
        fake = FakeMT5()
        fake.order_send_result = FakeOrderResult(fake.TRADE_RETCODE_INVALID_VOLUME, order=0, comment="invalid")
        bridge = MT5Bridge(mt5_module=fake)
        bridge.connect()
        result = bridge.place_order(
            {
                "action": fake.TRADE_ACTION_DEAL,
                "symbol": "XAUUSD",
                "volume": 0.10,
                "type": fake.ORDER_TYPE_BUY,
            }
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)
        self.assertEqual(result.failure_class, "INVALID_VOLUME")
        self.assertEqual(result.response_class, "BLOCK_EXECUTION")

    def test_query_positions_and_orders_return_empty_tuples_when_unavailable_without_filters(self) -> None:
        fake = FakeMT5()
        fake.positions_get = lambda symbol=None, ticket=None: None  # type: ignore[method-assign]
        fake.orders_get = lambda symbol=None, ticket=None: None  # type: ignore[method-assign]
        bridge = MT5Bridge(mt5_module=fake)
        bridge.connect()

        self.assertEqual(bridge.query_positions(), ())
        self.assertEqual(bridge.query_orders(), ())

    def test_connect_falls_back_to_existing_terminal_session(self) -> None:
        fake = FakeMT5()

        def initialize(**kwargs):
            fake.initialize_calls.append(dict(kwargs))
            return False

        fake.initialize = initialize  # type: ignore[method-assign]
        bridge = MT5Bridge(
            terminal_path=Path(r"C:\MT5\terminal64.exe"),
            login="123456",
            password="secret",
            server="Demo-Server",
            mt5_module=fake,
        )

        status = bridge.connect()

        self.assertTrue(status.ok)
        self.assertTrue(bridge.connected)
        self.assertTrue(bridge.terminal_ready)
        self.assertTrue(bridge.broker_ready)
        self.assertEqual(status.message, "MT5 bridge connected via existing terminal session")

    def test_latest_tick_audit_detects_and_corrects_broker_offset(self) -> None:
        captured_at_utc = datetime.now(timezone.utc)
        fake = FakeMT5(
            tick_time=captured_at_utc + timedelta(hours=3),
            rates_time=captured_at_utc + timedelta(hours=3),
        )
        bridge = MT5Bridge(
            terminal_path=Path(r"C:\MT5\terminal64.exe"),
            login="123456",
            password="secret",
            server="Demo-Server",
            mt5_module=fake,
        )

        tick = bridge.get_latest_tick("XAUUSD")
        rates = bridge.get_rates("XAUUSD", "M1", 3)
        audit = bridge.get_latest_tick_audit("XAUUSD")

        self.assertEqual(audit["broker_time_offset_hours"], 3)
        self.assertEqual(audit["broker_time_offset_seconds"], 10800)
        self.assertEqual(audit["broker_time_utc"], tick["timestamp"])
        self.assertLess(abs((tick["timestamp"] - captured_at_utc).total_seconds()), 5.0)
        self.assertLess(abs((rates[-1]["timestamp"] - captured_at_utc).total_seconds()), 5.0)
        self.assertLess(abs(audit["broker_delta_seconds_from_capture"]), 5.0)

    def test_latest_tick_retries_rate_fallback_on_initial_empty_response(self) -> None:
        captured_at_utc = datetime.now(timezone.utc)
        fake = FakeMT5(
            tick_time_missing=True,
            rates_time=captured_at_utc + timedelta(hours=3),
            rates_failures=1,
        )
        bridge = MT5Bridge(
            terminal_path=Path(r"C:\MT5\terminal64.exe"),
            login="123456",
            password="secret",
            server="Demo-Server",
            mt5_module=fake,
        )

        tick = bridge.get_latest_tick("XAUUSD")

        self.assertEqual(tick["symbol"], "XAUUSD")
        self.assertIsNotNone(tick["time"].tzinfo)
        self.assertEqual(tick["source_time_fallback"], "rates_m1")
        self.assertEqual(tick["broker_time_offset_hours"], 0)
        self.assertLess(abs(tick["broker_delta_seconds_from_capture"]), 5.0)

    def test_get_rates_recovers_once_from_ipc_loss(self) -> None:
        fake = FakeMT5()
        fake._ipc_loss_requires_reconnect = True
        bridge = MT5Bridge(
            terminal_path=Path(r"C:\MT5\terminal64.exe"),
            login="123456",
            password="secret",
            server="Demo-Server",
            mt5_module=fake,
        )
        bridge.connect()

        rates = bridge.get_rates("XAUUSD", "M1", 3)

        self.assertEqual(len(rates), 3)
        self.assertGreaterEqual(fake.initialize_calls.__len__(), 2)
        self.assertGreaterEqual(fake.shutdown_calls, 1)
        self.assertTrue(bridge.connected)
        self.assertTrue(bridge.market_data_probe_attempted)
        self.assertTrue(bridge.market_data_usable)
        self.assertTrue(bridge.recovery_fully_usable)
        self.assertIsNone(bridge.market_data_probe_error)
        self.assertEqual(fake.last_error(), (0, ""))

    def test_get_rates_fails_recovery_when_probe_fails_after_connect(self) -> None:
        fake = FakeMT5(
            tick_time_missing=True,
            post_reconnect_rate_failures=40,
            post_reconnect_last_error=(10054, "History not ready"),
        )
        fake._ipc_loss_requires_reconnect = True
        bridge = MT5Bridge(
            terminal_path=Path(r"C:\MT5\terminal64.exe"),
            login="123456",
            password="secret",
            server="Demo-Server",
            mt5_module=fake,
        )
        bridge.connect()

        with self.assertRaises(Exception) as ctx:
            bridge.get_rates("XAUUSD", "M1", 3)

        status = getattr(ctx.exception, "status", None)
        self.assertIsNotNone(status)
        diagnostics = status.diagnostics
        self.assertTrue(diagnostics["ipc_recovery_attempted"])
        self.assertTrue(diagnostics["ipc_recovery_ok"])
        self.assertEqual(diagnostics["pre_recovery_last_error"]["code"], -10004)
        self.assertEqual(diagnostics["pre_recovery_last_error"]["message"], "No IPC connection")
        self.assertTrue(diagnostics["market_data_probe_attempted"])
        self.assertFalse(diagnostics["market_data_usable"])
        self.assertFalse(diagnostics["recovery_fully_usable"])
        self.assertIsInstance(diagnostics["market_data_probe_error"], dict)
        self.assertEqual(diagnostics["market_data_probe_error"]["failure_class"], "CONTRACT_QUERY_FAILURE")

    def test_recover_ipc_connection_fails_when_connect_fails(self) -> None:
        fake = FakeMT5()

        def initialize(**kwargs):
            fake.initialize_calls.append(dict(kwargs))
            return False

        fake.initialize = initialize  # type: ignore[method-assign]
        fake.terminal_info = lambda: SimpleNamespace(connected=False)  # type: ignore[method-assign]
        fake.account_info = lambda: SimpleNamespace(login=123456, server="Demo-Server")  # type: ignore[method-assign]
        fake._ipc_loss_requires_reconnect = True
        bridge = MT5Bridge(
            terminal_path=Path(r"C:\MT5\terminal64.exe"),
            login="123456",
            password="secret",
            server="Demo-Server",
            mt5_module=fake,
        )

        with self.assertRaises(Exception) as ctx:
            bridge.get_rates("XAUUSD", "M1", 3)

        status = getattr(ctx.exception, "status", None)
        self.assertIsNotNone(status)
        diagnostics = status.diagnostics
        self.assertTrue(diagnostics["ipc_recovery_attempted"])
        self.assertFalse(diagnostics["ipc_recovery_ok"])
        self.assertFalse(diagnostics["market_data_probe_attempted"])
        self.assertFalse(diagnostics["market_data_usable"])
        self.assertFalse(diagnostics["recovery_fully_usable"])
        self.assertEqual(diagnostics["recovery_connect_path"], "connect_failed")

        # Recovery is exercised indirectly through get_rates; assert the helper contract via state.
        self.assertGreaterEqual(fake.initialize_calls.__len__(), 1)
        self.assertFalse(bridge.connected)

    def test_latest_tick_audit_falls_back_when_tick_is_unavailable(self) -> None:
        captured_at_utc = datetime.now(timezone.utc)
        fake = FakeMT5(
            tick_time=captured_at_utc + timedelta(hours=3),
            rates_time=captured_at_utc + timedelta(hours=3),
            rates_failures=0,
        )
        fake.symbol_info_tick = lambda symbol: None  # type: ignore[method-assign]
        bridge = MT5Bridge(
            terminal_path=Path(r"C:\MT5\terminal64.exe"),
            login="123456",
            password="secret",
            server="Demo-Server",
            mt5_module=fake,
        )

        audit = bridge.get_latest_tick_audit("XAUUSD")

        self.assertEqual(audit["symbol"], "XAUUSD")
        self.assertTrue(audit["tick_time_fallback_used"])
        self.assertIn(audit["tick_time_fallback_source"], {"ticks_stream", "rates_m1"})
        self.assertIsNotNone(audit["normalized_timestamp"].tzinfo)
        self.assertLess(abs(audit["broker_delta_seconds_from_capture"]), 5.0)

    def test_package_unavailable_fails_startup(self) -> None:
        with patch("tsp_v2.adapters.mt5_bridge.importlib.import_module", side_effect=ModuleNotFoundError()):
            bridge = MT5Bridge(terminal_path=Path(r"C:\MT5\terminal64.exe"))
            status = bridge.connect()
            self.assertFalse(status.ok)
            self.assertEqual(status.failure_class, "MT5_PACKAGE_UNAVAILABLE")
            self.assertEqual(status.response_class, "FAIL_STARTUP")


if __name__ == "__main__":
    unittest.main()
