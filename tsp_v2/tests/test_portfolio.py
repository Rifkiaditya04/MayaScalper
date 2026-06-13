from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from tsp_v2.enums import Direction, HealthState, NewsProviderMode, NewsProviderState, SessionName, SignalFamily
from tsp_v2.models import ContractSnapshot, MarketSnapshot, NewsSnapshot, PortfolioContext, PositionSnapshot, SignalDecision
from tsp_v2.portfolio import rank_opportunities


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


def _context(
    *,
    active_positions: tuple[PositionSnapshot, ...] = (),
    current_time_utc: datetime | None = None,
    symbol_cooldown_until_utc: dict[str, datetime] | None = None,
) -> PortfolioContext:
    return PortfolioContext(
        active_positions=active_positions,
        current_time_utc=current_time_utc,
        execution_health=HealthState.GREEN,
        spread_health=HealthState.GREEN,
        latency_health=HealthState.GREEN,
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
