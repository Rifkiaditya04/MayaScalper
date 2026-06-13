from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest

from tsp.bot import TSPBot
from tsp.execution import ExecutionRegistry
from tsp.persistence import SCHEMA_VERSION, SQLitePersistence
from tsp.state import (
    CompetitionContext,
    Direction,
    GovernorState,
    LayerState,
    Module,
    PositionState,
)
from tests.test_tsp_bot import FakeBotAdapter, _config


class TestTSPPersistence(unittest.TestCase):
    def test_sqlite_persistence_roundtrips_runtime_competition_and_layers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "tsp.sqlite3"
            store = SQLitePersistence(db_path)
            store.initialize()
            store.save_runtime_counters(
                last_bar_time=datetime(2026, 5, 22, 12, 30, tzinfo=timezone.utc),
                consecutive_bar_errors=2,
            )
            ctx = CompetitionContext(
                total_days=30,
                start_equity=10_000.0,
                starting_date=date(2026, 5, 1),
                total_pnl_r=1.5,
                daily_pnl_r=0.4,
                session_pnl_r=0.2,
                session_loss_count=1,
                session_risk_committed_r=0.9,
                current_session="OVERLAP",
                governor_state=GovernorState.HUNT,
                days_elapsed=21,
                updated_at=datetime(2026, 5, 22, 12, 30, tzinfo=timezone.utc),
            )
            store.save_competition_context(ctx)
            store.save_config_fingerprint("fp-123")
            position = PositionState(
                layers=[
                    LayerState(
                        ticket=1001,
                        direction=Direction.LONG,
                        entry_price=3320.5,
                        sl_price=3317.0,
                        tp_price=3326.8,
                        lot_size=0.2,
                        r_risk=0.9,
                        initial_r_distance=3.5,
                        open_time=datetime(2026, 5, 22, 12, 30, tzinfo=timezone.utc),
                        layer_index=0,
                        module=Module.PULLBACK_CONTINUATION,
                        setup_id="abc123def4567890",
                    )
                ]
            )
            store.replace_position_layers(position)
            bootstrap = store.load_bootstrap_state(now=datetime(2026, 5, 22, 12, 31, tzinfo=timezone.utc))

        self.assertEqual(bootstrap.runtime.consecutive_bar_errors, 2)
        self.assertEqual(bootstrap.runtime.last_bar_time, datetime(2026, 5, 22, 12, 30, tzinfo=timezone.utc))
        assert bootstrap.competition_ctx is not None
        self.assertEqual(bootstrap.competition_ctx.governor_state, GovernorState.HUNT)
        self.assertEqual(bootstrap.position.layer_count, 1)
        self.assertEqual(bootstrap.position.layers[0].ticket, 1001)
        self.assertEqual(bootstrap.config_fingerprint, "fp-123")

    def test_bot_bootstrap_restores_persisted_last_bar_and_layers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "tsp.sqlite3"
            config = _config()
            config = replace(
                config,
                bot=replace(
                    config.bot,
                    db_path=db_path,
                    log_dir=Path(tmp),
                    state_dir=Path(tmp),
                    config_last_known_good_path=Path(tmp) / "config_last_known_good.yaml",
                ),
                config_path=Path(tmp) / "config.yaml",
            )
            adapter = FakeBotAdapter()
            bot = TSPBot(config=config, adapter=adapter)
            process_result = bot.process_bar()

            self.assertTrue(process_result.signal_generated)
            self.assertIsNotNone(bot.last_bar_time)

            restored_adapter = FakeBotAdapter()
            restored_adapter.positions = dict(adapter.positions)
            restored_bot = TSPBot(config=config, adapter=restored_adapter)
            restored_bot.bootstrap()

            self.assertIsNotNone(restored_bot.last_bar_time)
            self.assertEqual(restored_bot.last_bar_time, bot.last_bar_time)
            assert restored_bot.runtime is not None
            self.assertGreaterEqual(restored_bot.runtime.position.layer_count, 1)

    def test_bootstrap_rejects_config_fingerprint_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "tsp.sqlite3"
            config = _config()
            config = replace(config, fingerprint="fp-a", bot=replace(config.bot, db_path=db_path))
            first_bot = TSPBot(config=config, adapter=FakeBotAdapter())
            first_bot.bootstrap()

            drifted = replace(config, fingerprint="fp-b")
            second_bot = TSPBot(config=drifted, adapter=FakeBotAdapter())
            with self.assertRaises(RuntimeError):
                second_bot.bootstrap()

    def test_registry_entries_roundtrip_and_stale_entries_are_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "tsp.sqlite3"
            store = SQLitePersistence(db_path)
            store.initialize()
            registry = ExecutionRegistry(ttl_seconds=120)
            now = datetime(2026, 5, 22, 12, 30, tzinfo=timezone.utc)
            registry.mark_pending("active-1", now)
            registry.mark_pending("stale-1", now)
            registry.mark_completed("done-1", now, 404)
            registry._entries["stale-1"] = replace(
                registry._entries["stale-1"],
                expires_at=datetime(2026, 5, 22, 12, 29, tzinfo=timezone.utc),
            )
            runtime_bot = TSPBot(config=_config(), adapter=FakeBotAdapter())
            runtime_bot.bootstrap()
            assert runtime_bot.runtime is not None
            store.persist_bar_cycle(
                runtime=runtime_bot.runtime,
                last_bar_time=None,
                regime=runtime_bot.runtime.regime,
                snap_timestamp=now,
                governor=type(
                    "Gov",
                    (),
                    {
                        "governor_state": GovernorState.NORMAL,
                        "aggression_bias": 0.0,
                        "threshold_modifier": 0.0,
                        "session_risk_budget_r": 1.0,
                        "session_pause": False,
                        "governor_note": "test",
                    },
                )(),
                signal=None,
                risk_decision=None,
                execution=None,
                lifecycle=None,
                registry=registry,
                config_fingerprint="fp-123",
            )
            bootstrap = store.load_bootstrap_state(now=datetime(2026, 5, 22, 12, 31, tzinfo=timezone.utc))

        self.assertEqual({entry.setup_id for entry in bootstrap.registry_entries}, {"active-1", "done-1"})

    def test_schema_version_metadata_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "tsp.sqlite3"
            store = SQLitePersistence(db_path)
            store.initialize()
            conn = store._connect()
            try:
                row = conn.execute(
                    "SELECT value_text FROM persistence_meta WHERE key = 'schema_version'"
                ).fetchone()
            finally:
                conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(int(row["value_text"]), SCHEMA_VERSION)

    def test_bootstrap_clears_persisted_position_when_broker_has_no_matching_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "tsp.sqlite3"
            store = SQLitePersistence(db_path)
            store.initialize()
            store.replace_position_layers(
                PositionState(
                    layers=[
                        LayerState(
                            ticket=9999,
                            direction=Direction.LONG,
                            entry_price=3320.5,
                            sl_price=3317.0,
                            tp_price=3326.8,
                            lot_size=0.2,
                            r_risk=0.9,
                            initial_r_distance=3.5,
                            open_time=datetime(2026, 5, 22, 12, 30, tzinfo=timezone.utc),
                            layer_index=0,
                            module=Module.PULLBACK_CONTINUATION,
                            setup_id="ghost-layer",
                        )
                    ]
                )
            )
            config = replace(_config(), bot=replace(_config().bot, db_path=db_path))
            bot = TSPBot(config=config, adapter=FakeBotAdapter())
            bot.bootstrap()

            assert bot.runtime is not None
            self.assertEqual(bot.runtime.position.layer_count, 0)


if __name__ == "__main__":
    unittest.main()
