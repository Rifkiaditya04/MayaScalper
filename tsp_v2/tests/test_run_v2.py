from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tsp_v2 import run_v2


@dataclass
class _ReadOnlyBridge:
    pending_orders: tuple[dict[str, object], ...] = ({"ticket": 9},)
    order_operations: list[str] | None = None

    def __post_init__(self) -> None:
        if self.order_operations is None:
            self.order_operations = []

    def query_orders(self) -> tuple[dict[str, object], ...]:
        return self.pending_orders

    def place_order(self, *_args, **_kwargs) -> None:
        self.order_operations.append("place_order")

    def modify_position_sltp(self, *_args, **_kwargs) -> None:
        self.order_operations.append("modify_position_sltp")

    def close_position(self, *_args, **_kwargs) -> None:
        self.order_operations.append("close_position")


@dataclass
class _ReadOnlyProbe:
    trade_mode: object
    bridge: _ReadOnlyBridge
    closed: bool = False

    def account_snapshot(self) -> dict[str, object]:
        return {"login": 123456, "server": "Demo-Server", "trade_mode": self.trade_mode}

    def live_positions(self) -> tuple[dict[str, object], ...]:
        return ({"ticket": 7, "sl": 0.0},)

    def close(self) -> None:
        self.closed = True


class _FakeRuntime:
    def __init__(self, _config: object, probe: _ReadOnlyProbe) -> None:
        self.probe = probe
        self.preflight_calls: list[dict[str, object]] = []

    def _build_live_probe(self) -> _ReadOnlyProbe:
        return self.probe

    def preflight(self, **kwargs):
        self.preflight_calls.append(kwargs)
        trade_mode = self.probe.trade_mode
        ok = isinstance(trade_mode, int) and not isinstance(trade_mode, bool) and trade_mode == 0
        return SimpleNamespace(
            ok=ok,
            blocked_reason="" if ok else "account_not_demo",
            mode="FORWARD_TEST",
            profile="FORWARD_SAFE",
            lock_snapshot=None,
            broker_ready=True,
            news_state="READY",
        )


class RunV2LivePreflightTests(unittest.TestCase):
    def test_live_preflight_reports_demo_account_without_order_operations(self) -> None:
        probe = _ReadOnlyProbe(trade_mode=0, bridge=_ReadOnlyBridge())
        runtime = _FakeRuntime(object(), probe)
        with (
            patch("tsp_v2.run_v2.load_config", return_value=object()),
            patch("tsp_v2.run_v2.DeploymentRuntime", return_value=runtime),
            patch("builtins.print") as print_mock,
        ):
            exit_code = run_v2.main(["preflight", "--live", "--config", "unused.yaml"])

        self.assertEqual(exit_code, 0)
        self.assertTrue(probe.closed)
        self.assertEqual(probe.bridge.order_operations, [])
        self.assertFalse(runtime.preflight_calls[0]["dry_run"])
        output = "\n".join(str(call.args[0]) for call in print_mock.call_args_list)
        self.assertIn("trade_mode=0 (demo)", output)
        self.assertIn("open_positions=1", output)
        self.assertIn("positions_without_sl=1", output)
        self.assertIn("pending_orders=1", output)

    def test_live_preflight_reports_guard_rejections_without_order_operations(self) -> None:
        for trade_mode in (1, 2, None):
            with self.subTest(trade_mode=trade_mode):
                probe = _ReadOnlyProbe(trade_mode=trade_mode, bridge=_ReadOnlyBridge())
                runtime = _FakeRuntime(object(), probe)
                with (
                    patch("tsp_v2.run_v2.load_config", return_value=object()),
                    patch("tsp_v2.run_v2.DeploymentRuntime", return_value=runtime),
                ):
                    exit_code = run_v2.main(["preflight", "--live", "--config", "unused.yaml"])

                self.assertEqual(exit_code, 1)
                self.assertTrue(probe.closed)
                self.assertEqual(probe.bridge.order_operations, [])


if __name__ == "__main__":
    unittest.main()
