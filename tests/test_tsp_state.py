from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from tsp.state import (
    CompetitionContext,
    ConfidenceTier,
    Direction,
    GovernorState,
    LayerState,
    Module,
    PositionState,
    Regime,
    RegimeResult,
    RiskParams,
    RuntimeState,
    SignalScore,
    TradePhase,
)


class TestTSPState(unittest.TestCase):
    def test_layer_state_uses_initial_r_distance_for_long_unrealized_r(self) -> None:
        layer = LayerState(
            ticket=1001,
            direction=Direction.LONG,
            entry_price=100.0,
            sl_price=99.0,
            tp_price=101.8,
            lot_size=0.10,
            r_risk=0.5,
            initial_r_distance=1.0,
            open_time=datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc),
            layer_index=0,
            module=Module.PULLBACK_CONTINUATION,
            setup_id="setup-long",
        )

        self.assertEqual(layer.unrealized_r(101.25), 1.25)

    def test_position_state_transitions_to_cooldown_and_clears_layers(self) -> None:
        signal = SignalScore(
            module=Module.BREAKOUT_MOMENTUM,
            direction=Direction.SHORT,
            score=81.0,
            confidence_tier=ConfidenceTier.ELITE,
            body_score=18.0,
            wick_score=13.0,
            atr_expansion=19.0,
            session_bonus=8.0,
            spread_penalty=-2.0,
            htf_alignment=12.0,
            momentum_score=11.0,
            volume_score=4.0,
            entry_hint=2350.0,
            invalidation_anchor=2354.0,
            setup_id="setup-short",
            signal_timestamp=datetime(2026, 5, 22, 9, 1, tzinfo=timezone.utc),
            setup_metadata={"source": "unit-test"},
        )
        layer = LayerState(
            ticket=2002,
            direction=Direction.SHORT,
            entry_price=2350.0,
            sl_price=2354.0,
            tp_price=2341.2,
            lot_size=0.15,
            r_risk=0.9,
            initial_r_distance=4.0,
            open_time=datetime(2026, 5, 22, 9, 2, tzinfo=timezone.utc),
            layer_index=0,
            module=Module.BREAKOUT_MOMENTUM,
            setup_id="setup-short",
        )
        position = PositionState()

        position.add_layer(layer, signal)
        position.transition_to_cooldown(
            Direction.SHORT,
            exited_at=datetime(2026, 5, 22, 9, 10, tzinfo=timezone.utc),
        )

        self.assertEqual(position.phase, TradePhase.COOLDOWN)
        self.assertEqual(position.layer_count, 0)
        self.assertEqual(position.direction, Direction.FLAT)
        self.assertEqual(position.module, Module.NONE)
        self.assertEqual(position.last_exit_direction, Direction.SHORT)

    def test_risk_params_enforces_locked_monotonic_tiers(self) -> None:
        params = RiskParams()

        self.assertLess(params.r_weak, params.r_normal)
        self.assertLess(params.r_normal, params.r_good)
        self.assertLess(params.r_good, params.r_elite)
        self.assertLessEqual(params.r_elite, params.r_max_single)

    def test_runtime_state_accepts_competition_context(self) -> None:
        runtime = RuntimeState(
            symbol="XAUUSD",
            magic=20260522,
            starting_equity=10_000.0,
            start_time=datetime(2026, 5, 22, 0, 0, tzinfo=timezone.utc),
            equity_current=10_000.0,
            equity_peak=10_200.0,
            daily_start_equity=10_000.0,
            regime=RegimeResult(
                regime=Regime.CHOP,
                confidence=0.30,
                direction_bias=Direction.FLAT,
                conflict_note="",
                raw_scores={"chop": 0.30},
            ),
            competition_ctx=CompetitionContext(
                total_days=30,
                start_equity=10_000.0,
                starting_date=date(2026, 5, 1),
                total_pnl_r=1.5,
                daily_pnl_r=0.4,
                session_pnl_r=0.2,
                session_loss_count=0,
                session_risk_committed_r=0.0,
                current_session="LONDON",
                governor_state=GovernorState.NORMAL,
                days_elapsed=21,
                updated_at=datetime(2026, 5, 22, 0, 5, tzinfo=timezone.utc),
            ),
        )

        self.assertEqual(runtime.symbol, "XAUUSD")
        self.assertIsNotNone(runtime.competition_ctx)
        assert runtime.competition_ctx is not None
        self.assertEqual(runtime.competition_ctx.governor_state, GovernorState.NORMAL)


if __name__ == "__main__":
    unittest.main()
