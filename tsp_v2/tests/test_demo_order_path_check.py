from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from tools.demo_order_path_check import (
    DemoOrderPathSafetyError,
    _protective_stop_loss,
    _run_order_path_with_bridge,
    validate_demo_order_path_preconditions,
)
from tsp_v2.adapters.mt5_bridge import MT5Bridge
from tsp_v2.tests.test_mt5_bridge import FakeMT5, FakeOrderResult


class OrderPathFakeMT5(FakeMT5):
    def __init__(self, *, reject_sltp: bool = False, partial_fill: bool = False) -> None:
        now = datetime.now(tz=timezone.utc)
        super().__init__(tick_time=now, rates_time=now)
        self._positions = ()
        self._orders = ()
        self.reject_sltp = reject_sltp
        self.partial_fill = partial_fill
        self.next_ticket = 900

    def symbol_info(self, symbol):
        info = super().symbol_info(symbol)
        info.filling_mode = 1
        info.trade_exemode = 2
        return info

    def order_send(self, request):
        self.order_requests.append(dict(request))
        action = request["action"]
        if action == self.TRADE_ACTION_SLTP:
            if self.reject_sltp:
                return FakeOrderResult(self.TRADE_RETCODE_INVALID_VOLUME, comment="reject sltp")
            ticket = int(request["position"])
            self._positions = tuple(
                SimpleNamespace(**(vars(position) | {"sl": request["sl"]}))
                if position.ticket == ticket
                else position
                for position in self._positions
            )
            return FakeOrderResult(self.TRADE_RETCODE_DONE, order=ticket, comment="sltp changed")
        if action == self.TRADE_ACTION_DEAL and "position" in request:
            ticket = int(request["position"])
            self._positions = tuple(position for position in self._positions if position.ticket != ticket)
            return FakeOrderResult(self.TRADE_RETCODE_DONE, order=ticket, comment="closed")
        if action == self.TRADE_ACTION_DEAL:
            ticket = self.next_ticket
            self.next_ticket += 1
            self._positions = (
                SimpleNamespace(
                    ticket=ticket,
                    symbol=request["symbol"],
                    type=request["type"],
                    volume=request["volume"],
                    price_open=request["price"],
                    sl=request["sl"],
                    tp=request.get("tp", 0.0),
                    profit=0.0,
                    time=int(datetime.now(tz=timezone.utc).timestamp()),
                ),
            )
            retcode = self.TRADE_RETCODE_DONE_PARTIAL if self.partial_fill else self.TRADE_RETCODE_DONE
            return FakeOrderResult(retcode, order=ticket, deal=ticket, comment="filled")
        raise AssertionError(f"Unexpected order request: {request}")


class DemoOrderPathCheckTests(unittest.TestCase):
    def _bridge(self, *, reject_sltp: bool = False, partial_fill: bool = False) -> tuple[MT5Bridge, OrderPathFakeMT5]:
        fake = OrderPathFakeMT5(reject_sltp=reject_sltp, partial_fill=partial_fill)
        return MT5Bridge(mt5_module=fake), fake

    def test_demo_flat_fresh_account_passes_read_only_preconditions(self) -> None:
        bridge, _ = self._bridge()
        bridge.connect()

        account, contract, tick = validate_demo_order_path_preconditions(
            bridge,
            symbol="EURUSD",
            now_utc=datetime.now(tz=timezone.utc),
        )

        self.assertEqual(account["trade_mode"], 0)
        self.assertEqual(contract["symbol"], "EURUSD")
        self.assertEqual(tick["symbol"], "EURUSD")

    def test_real_mt5_bridge_success_path_disconnects_without_attribute_error(self) -> None:
        bridge, fake = self._bridge()

        result = _run_order_path_with_bridge(bridge, symbol="EURUSD")

        self.assertEqual(result, 0)
        self.assertEqual(bridge.connected, False)
        self.assertEqual(fake.shutdown_calls, 1)
        self.assertEqual(fake._positions, ())
        self.assertEqual(fake.order_requests[0]["price"], 2350.1)

    def test_middle_rejection_preserves_original_error_and_closes_filled_position(self) -> None:
        bridge, fake = self._bridge(reject_sltp=True)

        with self.assertRaisesRegex(RuntimeError, "Demo SLTP modification failed: reject sltp"):
            _run_order_path_with_bridge(bridge, symbol="EURUSD")

        self.assertEqual(fake._positions, ())
        self.assertEqual(bridge.connected, False)
        self.assertTrue(any(request.get("position") == 900 for request in fake.order_requests))

    def test_partial_fill_is_cleaned_up_by_symbol_finally(self) -> None:
        bridge, fake = self._bridge(partial_fill=True)

        with self.assertRaisesRegex(RuntimeError, "did not fill"):
            _run_order_path_with_bridge(bridge, symbol="EURUSD")

        self.assertEqual(fake._positions, ())
        self.assertTrue(any(request.get("position") == 900 for request in fake.order_requests))

    def test_protective_stop_remains_on_long_side_with_minimum_distance(self) -> None:
        contract = {"point": 0.00001, "trade_stops_level": 10, "digits": 5}
        initial_stop = 1.09800

        protective_stop = _protective_stop_loss(bid=1.10000, initial_stop=initial_stop, contract=contract)

        self.assertGreater(protective_stop, initial_stop)
        self.assertLess(protective_stop, 1.10000)
        self.assertEqual(protective_stop, 1.099)

    def test_non_demo_account_is_fail_closed_before_order_path(self) -> None:
        bridge, fake = self._bridge()
        fake._account_info.trade_mode = 2
        bridge.connect()

        with self.assertRaisesRegex(DemoOrderPathSafetyError, "trade_mode=0"):
            validate_demo_order_path_preconditions(bridge, symbol="EURUSD", now_utc=datetime.now(tz=timezone.utc))
