from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import unittest

from tsp.config import ExecutionConfig
from tsp.data_pipeline import SymbolContract
from tsp.execution import ExecutionRegistry, execute_order, validate_execution
from tsp.risk import RiskDecision
from tsp.state import (
    AggressionState,
    CandleData,
    ConfidenceTier,
    Direction,
    ExecutionStatus,
    MarketSnapshot,
    Module,
    Regime,
    RegimeResult,
    RiskParams,
    RuntimeState,
    SignalScore,
)


@dataclass
class FakeAdapter:
    now: datetime
    response: dict
    position_exists: bool = False

    def send_market_order(self, symbol, action, volume, sl, tp, comment, magic):
        del symbol, action, volume, sl, tp, comment, magic
        return dict(self.response)

    def modify_position(self, ticket, sl, tp):
        del ticket, sl, tp
        return {}

    def partial_close(self, ticket, symbol, volume, comment):
        del ticket, symbol, volume, comment
        return {}

    def get_position_by_ticket(self, ticket):
        del ticket
        return {"ticket": 1} if self.position_exists else None

    def get_all_positions(self, magic):
        del magic
        return []

    def emergency_close(self, ticket, symbol, volume, reason):
        del ticket, symbol, volume, reason
        return {}

    def get_symbol_info(self, symbol):
        del symbol
        return None

    def get_server_time(self):
        return self.now

    def get_equity(self):
        return 10_000.0


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


def _signal(**overrides: object) -> SignalScore:
    base = dict(
        module=Module.PULLBACK_CONTINUATION,
        direction=Direction.LONG,
        score=74.0,
        confidence_tier=ConfidenceTier.GOOD,
        body_score=16.0,
        wick_score=11.0,
        atr_expansion=14.0,
        session_bonus=10.0,
        spread_penalty=0.0,
        htf_alignment=15.0,
        momentum_score=8.0,
        volume_score=0.0,
        entry_hint=3320.70,
        invalidation_anchor=3317.00,
        setup_id="signalexec123456",
        signal_timestamp=datetime(2026, 5, 22, 13, 30, tzinfo=timezone.utc),
        setup_metadata={},
    )
    base.update(overrides)
    return SignalScore(**base)


def _runtime(**overrides: object) -> RuntimeState:
    base = dict(
        symbol="XAUUSD",
        magic=20260522,
        starting_equity=10_000.0,
        start_time=datetime(2026, 5, 22, 0, 0, tzinfo=timezone.utc),
        aggression=AggressionState.NORMAL,
        equity_current=10_000.0,
        equity_peak=10_500.0,
        daily_start_equity=10_000.0,
        risk_params=RiskParams(),
    )
    base.update(overrides)
    return RuntimeState(**base)


def _decision() -> RiskDecision:
    return RiskDecision(
        action="ENTER",
        reason="eligible",
        r_percent=0.9,
        effective_equity=10_000.0,
        lot_size=0.24,
        entry_price=3320.70,
        invalidation_price=3317.00,
        allow_pyramid=False,
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


def _cfg() -> ExecutionConfig:
    return ExecutionConfig(
        signal_ttl_seconds=90,
        spread_hard_veto_ratio=2.5,
        spread_soft_penalty_ratio=1.3,
        max_slippage_ticks=10.0,
        market_order_retry_count=2,
        market_order_timeout_seconds=3,
        dedup_ttl_seconds=120,
    )


class TestTSPExecution(unittest.TestCase):
    def test_validate_execution_rejects_duplicate(self) -> None:
        registry = ExecutionRegistry(ttl_seconds=120)
        now = datetime(2026, 5, 22, 13, 30, tzinfo=timezone.utc)
        registry.mark_pending("signalexec123456", now)

        result = validate_execution(
            _signal(),
            _snapshot(),
            Regime.TREND,
            _runtime(),
            _contract(),
            _cfg(),
            registry,
            now=now,
        )
        assert result is not None
        self.assertEqual(result.status, ExecutionStatus.DUPLICATE)

    def test_validate_execution_rejects_stale_signal(self) -> None:
        now = datetime(2026, 5, 22, 13, 32, tzinfo=timezone.utc)
        result = validate_execution(
            _signal(signal_timestamp=now - timedelta(seconds=120)),
            _snapshot(),
            Regime.TREND,
            _runtime(),
            _contract(),
            _cfg(),
            ExecutionRegistry(ttl_seconds=120),
            now=now,
        )
        assert result is not None
        self.assertEqual(result.status, ExecutionStatus.STALE_SIGNAL)

    def test_execute_order_returns_filled(self) -> None:
        adapter = FakeAdapter(
            now=datetime(2026, 5, 22, 13, 30, tzinfo=timezone.utc),
            response={"retcode": 10009, "order": 11, "deal": 22, "price": 3320.75, "volume": 0.24},
        )
        result = execute_order(
            adapter,
            ExecutionRegistry(ttl_seconds=120),
            signal=_signal(),
            decision=_decision(),
            snap=_snapshot(),
            runtime=_runtime(),
            regime=Regime.TREND,
            contract=_contract(),
            cfg=_cfg(),
        )
        self.assertEqual(result.status, ExecutionStatus.FILLED)
        self.assertEqual(result.ticket, 22)

    def test_execute_order_returns_timeout_on_retryable_retcode(self) -> None:
        adapter = FakeAdapter(
            now=datetime(2026, 5, 22, 13, 30, tzinfo=timezone.utc),
            response={"retcode": 10031, "order": None, "deal": None, "price": None, "volume": None},
        )
        result = execute_order(
            adapter,
            ExecutionRegistry(ttl_seconds=120),
            signal=_signal(),
            decision=_decision(),
            snap=_snapshot(),
            runtime=_runtime(),
            regime=Regime.TREND,
            contract=_contract(),
            cfg=_cfg(),
        )
        self.assertEqual(result.status, ExecutionStatus.TIMEOUT)

    def test_execute_order_returns_filled_unverified_when_ticket_exists(self) -> None:
        adapter = FakeAdapter(
            now=datetime(2026, 5, 22, 13, 30, tzinfo=timezone.utc),
            response={"retcode": 10006, "order": 11, "deal": 22, "price": 3320.75, "volume": 0.24},
            position_exists=True,
        )
        result = execute_order(
            adapter,
            ExecutionRegistry(ttl_seconds=120),
            signal=_signal(),
            decision=_decision(),
            snap=_snapshot(),
            runtime=_runtime(),
            regime=Regime.TREND,
            contract=_contract(),
            cfg=_cfg(),
        )
        self.assertEqual(result.status, ExecutionStatus.FILLED_UNVERIFIED)
