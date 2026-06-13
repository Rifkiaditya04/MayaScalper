from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from tsp_v2.enums import (
    HealthState,
    NewsProviderMode,
    NewsProviderState,
    PaceClassification,
    ProfileName,
    GovernorState,
    SessionName,
)
from tsp_v2.governor import evaluate_governor
from tsp_v2.models import ContractSnapshot, GovernorContext, MarketSnapshot, NewsSnapshot


class GovernorTests(unittest.TestCase):
    def test_normal_state(self) -> None:
        snapshot = _snapshot()
        result = evaluate_governor(
            snapshot,
            _context(
                contest_elapsed_pct=40.0,
                realized_pnl_r=0.45,
                signal_density=0.2,
                ranking_proxy_available=True,
                ranking_proxy_pace_ratio=1.0,
            ),
        )
        self.assertEqual(result.state, GovernorState.NORMAL)
        self.assertEqual(result.pace_classification, PaceClassification.ON_TRACK)

    def test_survive_escalation(self) -> None:
        snapshot = _snapshot()
        context = _context(drawdown_pct=11.5, daily_loss_pct=2.0, execution_health=HealthState.YELLOW)
        result = evaluate_governor(snapshot, context)
        self.assertEqual(result.state.value, "SURVIVE")
        self.assertIn("execution_degraded", result.escalation_flags)

    def test_protect_behavior(self) -> None:
        snapshot = _snapshot()
        context = _context(equity=103000.0, peak_equity=106500.0, drawdown_pct=2.0, daily_loss_pct=0.5)
        result = evaluate_governor(snapshot, context)
        self.assertEqual(result.state.value, "PROTECT")

    def test_sprint_activation(self) -> None:
        snapshot = _snapshot()
        context = _context(contest_elapsed_pct=90.0, drawdown_pct=4.0, signal_density=0.7)
        result = evaluate_governor(snapshot, context)
        self.assertEqual(result.state.value, "SPRINT")

    def test_chase_starvation_escalation(self) -> None:
        snapshot = _snapshot()
        context = _context(
            contest_elapsed_pct=62.0,
            drawdown_pct=3.5,
            opportunity_starvation_minutes=180.0,
            realized_pnl_r=0.2,
            ranking_proxy_available=False,
        )
        result = evaluate_governor(snapshot, context)
        self.assertEqual(result.state.value, "CHASE")
        self.assertIn("starvation_escalation", result.escalation_flags)

    def test_kill_review_overrides(self) -> None:
        snapshot = _snapshot()
        context = _context(drawdown_pct=15.0, daily_loss_pct=15.5)
        result = evaluate_governor(snapshot, context)
        self.assertEqual(result.state.value, "KILL_REVIEW")

    def test_pace_classification_behind_and_ahead(self) -> None:
        snapshot = _snapshot()
        behind = evaluate_governor(
            snapshot,
            _context(
                contest_elapsed_pct=50.0,
                realized_pnl_r=0.4,
                ranking_proxy_available=True,
                ranking_proxy_pace_ratio=0.7,
            ),
        )
        ahead = evaluate_governor(
            snapshot,
            _context(
                contest_elapsed_pct=50.0,
                realized_pnl_r=1.8,
                ranking_proxy_available=True,
                ranking_proxy_pace_ratio=1.3,
            ),
        )
        self.assertEqual(behind.pace_classification, PaceClassification.BEHIND)
        self.assertEqual(ahead.pace_classification, PaceClassification.AHEAD)


def _context(
    *,
    contest_elapsed_pct: float = 30.0,
    equity: float = 100000.0,
    peak_equity: float = 100000.0,
    drawdown_pct: float = 0.5,
    daily_loss_pct: float = 0.2,
    realized_pnl_r: float = 1.0,
    signal_density: float = 0.65,
    execution_health: HealthState = HealthState.GREEN,
    feed_health: HealthState = HealthState.GREEN,
    opportunity_starvation_minutes: float = 0.0,
    recovery_momentum: float = 0.2,
    profile: ProfileName = ProfileName.CONTEST_HUNTER,
    ranking_proxy_available: bool = False,
    ranking_proxy_pace_ratio: float | None = None,
) -> GovernorContext:
    return GovernorContext(
        contest_elapsed_pct=contest_elapsed_pct,
        equity=equity,
        peak_equity=peak_equity,
        drawdown_pct=drawdown_pct,
        daily_loss_pct=daily_loss_pct,
        realized_pnl_r=realized_pnl_r,
        signal_density=signal_density,
        execution_health=execution_health,
        feed_health=feed_health,
        opportunity_starvation_minutes=opportunity_starvation_minutes,
        recovery_momentum=recovery_momentum,
        profile=profile,
        ranking_proxy_available=ranking_proxy_available,
        ranking_proxy_pace_ratio=ranking_proxy_pace_ratio,
    )


def _snapshot() -> MarketSnapshot:
    cycle_time = datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
    bars = tuple(
        {
            "timestamp": cycle_time - timedelta(minutes=5 * (idx + 1)),
            "close_time_utc": cycle_time - timedelta(minutes=5 * idx),
            "open": 100.0 + idx,
            "high": 100.5 + idx,
            "low": 99.5 + idx,
            "close": 100.2 + idx,
            "tick_volume": 100.0,
            "timeframe": "M5",
        }
        for idx in range(70)
    )
    return MarketSnapshot(
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
        bars_h1=bars,
        bars_m15=bars,
        bars_m5=bars,
        bars_m1=bars,
        indicator_bundle={
            "bar_anchor_m5_close_utc": cycle_time,
            "bar_anchor_m1_close_utc": cycle_time,
            "atr_m5": 1.0,
        },
    )


if __name__ == "__main__":
    unittest.main()
