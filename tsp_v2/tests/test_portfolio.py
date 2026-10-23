from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from tsp_v2.enums import Direction, HealthState, NewsProviderMode, NewsProviderState, SessionName, SignalFamily
from tsp_v2.models import ContractSnapshot, MarketSnapshot, NewsSnapshot, PortfolioContext, PositionSnapshot, SignalDecision
from tsp_v2.portfolio import rank_opportunities, trace_rank_opportunities


class PortfolioTests(unittest.TestCase):
    def test_portfolio_ranks_top_two(self) -> None:
        signals = [
            _signal("XAUUSD", 0.72),
            _signal("GBPUSD", 0.81),
            _signal("EURUSD", 0.92),
        ]
        ranked = rank_opportunities(signals, context=_context())
        self.assertEqual([item.symbol for item in ranked], ["EURUSD", "GBPUSD"])

    def test_replacement_qualification_requires_superiority(self) -> None:
        signals = [_signal("XAUUSD", 0.61), _signal("GBPUSD", 0.73)]
        ranked = rank_opportunities(
            signals,
            context=_context(
                active_positions=(
                    PositionSnapshot("XAUUSD", Direction.LONG, "seed-1", "XAUUSD", 0.50, signal_score=0.60),
                    PositionSnapshot("GBPUSD", Direction.LONG, "seed-2", "GBPUSD_EURUSD", 0.50, signal_score=0.60),
                )
            ),
        )
        self.assertEqual([item.symbol for item in ranked], ["GBPUSD"])

    def test_correlation_aware_filtering_blocks_exhausted_group(self) -> None:
        signals = [_signal("GBPUSD", 0.88), _signal("EURUSD", 0.86), _signal("XAUUSD", 0.80)]
        ranked = rank_opportunities(
            signals,
            context=_context(
                active_positions=(
                    PositionSnapshot("GBPUSD", Direction.LONG, "seed-1", "GBPUSD_EURUSD", 2.25, signal_score=0.75),
                )
            ),
        )
        self.assertEqual([item.symbol for item in ranked], ["XAUUSD"])

    def test_symbol_cooldown_blocks_reentry(self) -> None:
        current_time = datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
        signals = [_signal("XAUUSD", 0.91, current_time=current_time)]
        ranked = rank_opportunities(
            signals,
            context=_context(
                current_time_utc=current_time,
                symbol_cooldown_until_utc={"XAUUSD": current_time + timedelta(minutes=4)},
            ),
        )
        self.assertEqual(ranked, [])

    def test_trace_rank_opportunities_reports_first_rejection_reason_per_candidate(self) -> None:
        current_time = datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
        cases = [
            (
                "expired",
                [_signal("XAUUSD", 0.91, current_time=current_time - timedelta(minutes=12))],
                _context(current_time_utc=current_time),
                [],
                ["EXPIRED"],
            ),
            (
                "cooldown",
                [_signal("XAUUSD", 0.91, current_time=current_time)],
                _context(
                    current_time_utc=current_time,
                    symbol_cooldown_until_utc={"XAUUSD": current_time + timedelta(minutes=4)},
                ),
                [],
                ["SYMBOL_COOLDOWN"],
            ),
            (
                "duplicate",
                [_signal("XAUUSD", 0.95), _signal("XAUUSD", 0.90)],
                _context(),
                ["XAUUSD"],
                [None, "DUPLICATE_SYMBOL"],
            ),
            (
                "execution_health",
                [_signal("XAUUSD", 0.91)],
                _context(execution_health=HealthState.YELLOW),
                [],
                ["EXECUTION_HEALTH"],
            ),
            (
                "spread_health",
                [_signal("XAUUSD", 0.91)],
                _context(spread_health=HealthState.RED),
                [],
                ["SPREAD_HEALTH"],
            ),
            (
                "latency_health",
                [_signal("XAUUSD", 0.91)],
                _context(latency_health=HealthState.RED),
                [],
                ["LATENCY_HEALTH"],
            ),
            (
                "correlation_budget",
                [_signal("GBPUSD", 0.91)],
                _context(
                    active_positions=(
                        PositionSnapshot("GBPUSD", Direction.LONG, "seed-1", "GBPUSD_EURUSD", 2.25, signal_score=0.75),
                    )
                ),
                [],
                ["CORRELATION_BUDGET"],
            ),
            (
                "replacement_not_allowed",
                [_signal("GBPUSD", 0.91)],
                _context(
                    active_positions=(
                        PositionSnapshot("XAUUSD", Direction.LONG, "seed-1", "XAUUSD", 0.40, signal_score=0.90),
                        PositionSnapshot("GBPUSD", Direction.LONG, "seed-2", "GBPUSD_EURUSD", 0.50, signal_score=0.88),
                    ),
                ),
                [],
                ["REPLACEMENT_NOT_ALLOWED"],
            ),
            (
                "capacity_limit_reached",
                [_signal("XAUUSD", 0.95), _signal("GBPUSD", 0.90), _signal("EURUSD", 0.85)],
                _context(),
                ["XAUUSD", "GBPUSD"],
                [None, None, "CAPACITY_LIMIT_REACHED"],
            ),
            (
                "selected",
                [_signal("XAUUSD", 0.95)],
                _context(),
                ["XAUUSD"],
                [None],
            ),
        ]

        for label, signals, context, expected_selected_symbols, expected_reasons in cases:
            with self.subTest(label=label):
                selected, traces = trace_rank_opportunities(signals, context=context)
                self.assertEqual([item.symbol for item in selected], expected_selected_symbols)
                self.assertEqual(
                    [item.symbol for item in rank_opportunities(signals, context=context)],
                    expected_selected_symbols,
                )
                self.assertEqual(len(traces), len(signals))
                self.assertEqual([trace.selected for trace in traces], [reason is None for reason in expected_reasons])
                self.assertEqual([trace.first_rejection_reason for trace in traces], expected_reasons)
                for trace in traces:
                    self.assertEqual(trace.score, next(signal.score for signal in signals if signal.setup_id == trace.setup_id))
                    self.assertEqual(trace.threshold, next(signal.threshold for signal in signals if signal.setup_id == trace.setup_id))

    def test_symbol_specific_health_does_not_block_healthy_candidate(self) -> None:
        selected, traces = trace_rank_opportunities(
            [_signal("GBPUSD", 0.91)],
            context=_context(
                spread_health=HealthState.RED,
                latency_health=HealthState.RED,
                spread_health_by_symbol={"GBPUSD": HealthState.GREEN, "EURUSD": HealthState.RED},
                latency_health_by_symbol={"GBPUSD": HealthState.GREEN, "EURUSD": HealthState.RED},
            ),
        )

        self.assertEqual([signal.symbol for signal in selected], ["GBPUSD"])
        self.assertTrue(traces[0].selected)

    def test_symbol_specific_latency_health_blocks_its_own_candidate(self) -> None:
        selected, traces = trace_rank_opportunities(
            [_signal("GBPUSD", 0.91)],
            context=_context(
                latency_health=HealthState.GREEN,
                latency_health_by_symbol={"GBPUSD": HealthState.RED},
            ),
        )

        self.assertEqual(selected, [])
        self.assertEqual(traces[0].first_rejection_reason, "LATENCY_HEALTH")

    def test_symbol_health_mapping_falls_back_to_global_health(self) -> None:
        selected, traces = trace_rank_opportunities(
            [_signal("GBPUSD", 0.91)],
            context=_context(spread_health=HealthState.YELLOW),
        )

        self.assertEqual(selected, [])
        self.assertEqual(traces[0].first_rejection_reason, "SPREAD_HEALTH")


def _context(
    *,
    active_positions: tuple[PositionSnapshot, ...] = (),
    current_time_utc: datetime | None = None,
    symbol_cooldown_until_utc: dict[str, datetime] | None = None,
    execution_health: HealthState = HealthState.GREEN,
    spread_health: HealthState = HealthState.GREEN,
    latency_health: HealthState = HealthState.GREEN,
    spread_health_by_symbol: dict[str, HealthState] | None = None,
    latency_health_by_symbol: dict[str, HealthState] | None = None,
    replacement_superiority: float = 0.12,
    max_positions: int = 2,
) -> PortfolioContext:
    return PortfolioContext(
        active_positions=active_positions,
        current_time_utc=current_time_utc,
        execution_health=execution_health,
        spread_health=spread_health,
        latency_health=latency_health,
        spread_health_by_symbol=spread_health_by_symbol or {},
        latency_health_by_symbol=latency_health_by_symbol or {},
        replacement_superiority=replacement_superiority,
        max_positions=max_positions,
        symbol_cooldown_until_utc=symbol_cooldown_until_utc or {},
    )


def _signal(symbol: str, score: float, *, current_time: datetime | None = None) -> SignalDecision:
    cycle_time = current_time or datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
    return SignalDecision(
        setup_id=f"{symbol.lower()}-{score:.2f}",
        signal_family=SignalFamily.TREND_CONTINUATION,
        symbol=symbol,
        direction=Direction.LONG,
        score=score,
        threshold=0.70,
        expires_at_utc=cycle_time + timedelta(minutes=10),
        rationale="test",
        lineage=("REGIME:TREND", "FAMILY:TREND_CONTINUATION"),
    )


if __name__ == "__main__":
    unittest.main()
