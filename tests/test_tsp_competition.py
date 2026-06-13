from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

from tsp.competition import (
    apply_governor_bias,
    build_competition_context,
    evaluate_governor,
    reset_session_metrics,
)
from tsp.config import CompetitionConfig
from tsp.state import (
    AggressionState,
    CompetitionContext,
    Direction,
    GovernorState,
    Regime,
    RegimeResult,
    RiskParams,
    RuntimeState,
)


def _competition_cfg() -> CompetitionConfig:
    return CompetitionConfig(
        total_days=30,
        target_total_pnl_r=25.0,
        lead_protect_r=12.0,
        sprint_pct=0.15,
        session_risk_budget_r=3.0,
        hunt_aggression_bias=0.4,
        protect_aggression_bias=-0.6,
        sprint_aggression_bias=0.8,
        hunt_threshold_modifier=-4.0,
        protect_threshold_modifier=6.0,
        sprint_threshold_modifier=-8.0,
        circuit_loss_count=3,
        circuit_session_pnl_r=-2.0,
    )


def _context(**overrides: object) -> CompetitionContext:
    base = dict(
        total_days=30,
        start_equity=10_000.0,
        starting_date=date(2026, 5, 1),
        total_pnl_r=0.0,
        daily_pnl_r=0.0,
        session_pnl_r=0.0,
        session_loss_count=0,
        session_risk_committed_r=0.0,
        current_session="LONDON",
        governor_state=GovernorState.NORMAL,
        days_elapsed=21,
        updated_at=datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return CompetitionContext(**base)


def _runtime(**overrides: object) -> RuntimeState:
    base = dict(
        symbol="XAUUSD",
        magic=20260522,
        starting_equity=10_000.0,
        start_time=datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),
        aggression=AggressionState.NORMAL,
        equity_current=10_000.0,
        equity_peak=10_500.0,
        daily_start_equity=10_000.0,
        consecutive_wins=0,
        consecutive_losses=0,
        daily_pnl_r=0.0,
        total_trades_today=0,
        competition_ctx=_context(),
        risk_params=RiskParams(),
    )
    base.update(overrides)
    return RuntimeState(**base)


class TestTSPCompetition(unittest.TestCase):
    def test_apply_governor_bias_respects_kill_and_promotes_normal(self) -> None:
        self.assertEqual(
            apply_governor_bias(AggressionState.NORMAL, 0.4, False),
            AggressionState.AGGRESSIVE,
        )
        self.assertEqual(
            apply_governor_bias(AggressionState.AGGRESSIVE, -0.6, True),
            AggressionState.DEFENSIVE,
        )

    def test_build_and_reset_competition_context(self) -> None:
        ctx = build_competition_context(
            cfg=_competition_cfg(),
            start_equity=10_000.0,
            starting_date=date(2026, 5, 1),
            current_session="LONDON",
            now=datetime(2026, 5, 1, 1, 0, tzinfo=timezone.utc),
        )
        reset = reset_session_metrics(
            _context(session_pnl_r=-1.2, session_loss_count=2, session_risk_committed_r=1.7),
            current_session="NY",
            now=datetime(2026, 5, 22, 13, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(ctx.governor_state, GovernorState.NORMAL)
        self.assertEqual(reset.session_pnl_r, 0.0)
        self.assertEqual(reset.current_session, "NY")

    def test_evaluate_governor_returns_hunt_when_behind_and_active(self) -> None:
        directive = evaluate_governor(
            _runtime(competition_ctx=_context(total_pnl_r=-1.0, current_session="OVERLAP")),
            RegimeResult(Regime.TREND, 0.8, Direction.LONG, "", {}),
            _competition_cfg(),
            now=datetime(2026, 5, 22, 13, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(directive.governor_state, GovernorState.HUNT)
        self.assertTrue(directive.allow_aggressive_features)

    def test_evaluate_governor_returns_protect_on_lead(self) -> None:
        directive = evaluate_governor(
            _runtime(competition_ctx=_context(total_pnl_r=13.0)),
            RegimeResult(Regime.TREND, 0.8, Direction.LONG, "", {}),
            _competition_cfg(),
            now=datetime(2026, 5, 22, 13, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(directive.governor_state, GovernorState.PROTECT)
        self.assertFalse(directive.allow_aggressive_features)

    def test_evaluate_governor_opens_session_circuit_breaker(self) -> None:
        directive = evaluate_governor(
            _runtime(
                competition_ctx=_context(
                    session_pnl_r=-2.5,
                    session_loss_count=3,
                    current_session="LONDON",
                )
            ),
            RegimeResult(Regime.BREAKOUT, 0.7, Direction.LONG, "", {}),
            _competition_cfg(),
            now=datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(directive.session_pause)
        self.assertEqual(directive.governor_note, "session_circuit_breaker")
