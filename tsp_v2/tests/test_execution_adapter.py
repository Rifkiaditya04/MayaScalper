from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import unittest

from tsp_v2.adapters.execution_adapter import MT5ExecutionAdapter
from tsp_v2.adapters.mt5_bridge import MT5TradeResult
from tsp_v2.enums import Direction, ExecutionRegistryState, GovernorState, HealthState, NewsProviderMode, NewsProviderState, PaceClassification, RegimeName, RiskAction, SessionName, SignalFamily
from tsp_v2.execution import ExecutionRegistryBook, build_execution_intent
from tsp_v2.models import ContractSnapshot, GovernorDecision, MarketSnapshot, NewsSnapshot, RiskDecision, SignalDecision
from tsp_v2.config_schema import ConfigValidationError


@dataclass
class FakeBridge:
    response: MT5TradeResult

    def __post_init__(self) -> None:
        self.mt5_module = _FakeMT5Module()
        self.place_order_requests: list[dict[str, object]] = []
        self.contract_requests: list[str] = []

    def query_symbol_contract(self, symbol: str) -> dict[str, object]:
        self.contract_requests.append(symbol.upper())
        return {
            "symbol": symbol.upper(),
            "visible": True,
            "point": 0.1,
            "trade_tick_size": 0.1,
            "trade_tick_value": 1.0,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "trade_stops_level": 20,
            "trade_freeze_level": 0,
        }

    def place_order(self, request: dict[str, object]) -> MT5TradeResult:
        self.place_order_requests.append(dict(request))
        return self.response

    def query_positions(self) -> list[dict[str, object]]:
        return []


class _FakeMT5Module:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 10
    ORDER_TIME_GTC = 20
    ORDER_FILLING_RETURN = 30


class ExecutionAdapterTests(unittest.TestCase):
    def test_execute_maps_filled_response_and_updates_registry(self) -> None:
        adapter, intent = self._build_adapter(
            MT5TradeResult(
                ok=True,
                failure_class="DONE",
                response_class="OK",
                retryable=False,
                fatal=False,
                terminal=True,
                message="filled",
                ticket=777,
                request={},
                response={"retcode": "DONE", "ticket": 777},
                diagnostics={},
            )
        )
        result = adapter.execute(intent)
        self.assertTrue(result.accepted)
        self.assertTrue(result.filled)
        self.assertFalse(result.partial_fill)
        self.assertEqual(result.ticket, 777)
        self.assertEqual(result.broker_code, "DONE")
        self.assertEqual(result.classification, "OK")
        self.assertEqual(adapter.registry.entries_by_submission_uuid[intent.submission_uuid].state, ExecutionRegistryState.FILLED)

    def test_execute_maps_partial_fill(self) -> None:
        adapter, intent = self._build_adapter(
            MT5TradeResult(
                ok=True,
                failure_class="DONE_PARTIAL",
                response_class="TRIGGER_RECONCILIATION",
                retryable=False,
                fatal=False,
                terminal=False,
                message="partial",
                ticket=778,
                request={},
                response={"retcode": "DONE_PARTIAL", "ticket": 778},
                diagnostics={},
            )
        )
        result = adapter.execute(intent)
        self.assertTrue(result.accepted)
        self.assertFalse(result.filled)
        self.assertTrue(result.partial_fill)
        entry = adapter.registry.entries_by_submission_uuid[intent.submission_uuid]
        self.assertEqual(entry.state.value, "PARTIAL")

    def test_execute_maps_retryable_failure(self) -> None:
        adapter, intent = self._build_adapter(
            MT5TradeResult(
                ok=False,
                failure_class="TRADE_CONTEXT_BUSY",
                response_class="BLOCK_EXECUTION",
                retryable=True,
                fatal=False,
                terminal=False,
                message="busy",
                ticket=None,
                request={},
                response={"retcode": "TRADE_CONTEXT_BUSY"},
                diagnostics={},
            )
        )
        result = adapter.execute(intent)
        self.assertFalse(result.accepted)
        self.assertTrue(result.rejected)
        self.assertTrue(result.retryable)
        self.assertEqual(adapter.registry.entries_by_submission_uuid[intent.submission_uuid].state.value, "SUBMITTED")

    def test_execute_rejects_duplicate_intent(self) -> None:
        adapter, intent = self._build_adapter(
            MT5TradeResult(
                ok=True,
                failure_class="DONE",
                response_class="OK",
                retryable=False,
                fatal=False,
                terminal=True,
                message="filled",
                ticket=779,
                request={},
                response={"retcode": "DONE", "ticket": 779},
                diagnostics={},
            )
        )
        first = adapter.execute(intent)
        second = adapter.execute(intent)
        self.assertTrue(first.accepted)
        self.assertFalse(second.accepted)
        self.assertEqual(second.broker_code, "DUPLICATE_INTENT")

    def test_execute_rejects_stale_intent(self) -> None:
        adapter, intent = self._build_adapter(
            MT5TradeResult(
                ok=True,
                failure_class="DONE",
                response_class="OK",
                retryable=False,
                fatal=False,
                terminal=True,
                message="filled",
                ticket=780,
                request={},
                response={"retcode": "DONE", "ticket": 780},
                diagnostics={},
            )
        )
        with self.assertRaises(ConfigValidationError):
            adapter.execute(intent, at_utc=intent.cycle_time_utc + timedelta(seconds=121))

    def _build_adapter(self, response: MT5TradeResult):
        cycle_time = datetime(2026, 5, 29, 10, 0, 0, tzinfo=timezone.utc)
        snapshot = MarketSnapshot(
            cycle_time_utc=cycle_time,
            symbol="XAUUSD",
            tick_bid=100.0,
            tick_ask=100.2,
            spread_points=2.0,
            spread_ratio=1.0,
            spread_health=HealthState.GREEN,
            session=SessionName.LONDON_NY,
            news=NewsSnapshot(
                provider_mode=NewsProviderMode.STATIC_FILE,
                provider_state=NewsProviderState.READY,
                snapshot_generated_at_utc=cycle_time - timedelta(minutes=5),
                lockout_active=False,
                next_relevant_event_utc=None,
                relevant_events=(),
            ),
            contract=ContractSnapshot(
                symbol="XAUUSD",
                point=0.1,
                tick_size=0.1,
                tick_value=1.0,
                min_lot=0.01,
                max_lot=100.0,
                lot_step=0.01,
                stop_level_points=20,
                freeze_level_points=0,
            ),
            feed_health=HealthState.GREEN,
            latency_health=HealthState.GREEN,
            bars_h1=(),
            bars_m15=(),
            bars_m5=(),
            bars_m1=(),
            indicator_bundle={},
        )
        signal = SignalDecision(
            setup_id="trend-setup",
            signal_family=SignalFamily.TREND_CONTINUATION,
            symbol="XAUUSD",
            direction=Direction.LONG,
            score=0.88,
            threshold=0.72,
            expires_at_utc=cycle_time + timedelta(seconds=120),
            rationale="test",
            lineage=("REGIME:TREND",),
        )
        risk = RiskDecision(
            action=RiskAction.ENTER,
            risk_multiplier=1.0,
            sized_volume=0.10,
            invalidation_price=99.5,
            hard_block_reason="",
            governor_adjusted_state=GovernorState.NORMAL,
        )
        intent = build_execution_intent(signal, risk, decision_price=100.1, cycle_time_utc=cycle_time)
        bridge = FakeBridge(response=response)
        adapter = MT5ExecutionAdapter(bridge=bridge, registry=ExecutionRegistryBook())
        return adapter, intent


if __name__ == "__main__":
    unittest.main()
