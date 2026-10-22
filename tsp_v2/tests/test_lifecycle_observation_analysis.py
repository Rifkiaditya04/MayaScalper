from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools.analyze_lifecycle_observation import load_observations, render_markdown, summarize_observation


class LifecycleObservationAnalysisTests(unittest.TestCase):
    def test_read_only_analysis_calculates_long_mae_mfe_and_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "runtime.sqlite3"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "CREATE TABLE telemetry_index (id INTEGER PRIMARY KEY, event_time_utc TEXT, topic TEXT, payload_json TEXT)"
                )
                events = (
                    ("runtime.lifecycle_state_created", {"position_ticket": 11, "cycle_time_utc": "2026-05-29T09:00:00+00:00"}),
                    ("runtime.lifecycle_price_sample", _sample("2026-05-29T09:05:00+00:00", 99.0, trail_active=False)),
                    ("runtime.lifecycle_price_sample", _sample("2026-05-29T09:10:00+00:00", 102.0, trail_active=True)),
                    ("runtime.lifecycle_stop_updated", {"position_ticket": 11, "cycle_time_utc": "2026-05-29T09:10:00+00:00"}),
                    ("runtime.lifecycle_state_closed", {"position_ticket": 11, "cycle_time_utc": "2026-05-29T09:15:00+00:00"}),
                )
                for index, (topic, payload) in enumerate(events, start=1):
                    conn.execute(
                        "INSERT INTO telemetry_index (id, event_time_utc, topic, payload_json) VALUES (?, ?, ?, ?)",
                        (index, payload["cycle_time_utc"], topic, json.dumps(payload)),
                    )
                conn.commit()

            observations = load_observations(db_path)
            self.assertEqual(list(observations), [11])
            summary = summarize_observation(observations[11])
            self.assertEqual(summary["mae_r"], -0.5)
            self.assertEqual(summary["mfe_r"], 1.0)
            self.assertEqual(summary["final_r"], 1.0)
            self.assertEqual(summary["mae_minutes_after_entry"], 5.0)
            self.assertTrue(summary["breakeven_reached"])
            self.assertFalse(summary["trailing_reached"])
            self.assertTrue(summary["recovered_after_mae"])
            self.assertEqual(summary["status"], "CLOSED")
            report = render_markdown([summary], db_path=db_path)
            self.assertIn("Trades observed: 1", report)
            self.assertIn("Positions with final observed R <= -0.9: 0", report)

    def test_report_counts_positions_with_deep_final_observed_r(self) -> None:
        summary = {
            "ticket": 12,
            "symbol": "GBPJPY",
            "setup_id": "setup-12",
            "direction": "SHORT",
            "sample_count": 1,
            "mae_r": -1.0,
            "mfe_r": -1.0,
            "final_r": -1.0,
            "mae_minutes_after_entry": 5.0,
            "breakeven_reached": False,
            "trailing_reached": False,
            "modify_failure_count": 0,
            "status": "CLOSED",
            "recovered_after_mae": False,
        }

        report = render_markdown([summary], db_path=Path("runtime.sqlite3"))

        self.assertIn("Final R", report)
        self.assertIn("Positions with final observed R <= -0.9: 1", report)


def _sample(cycle_time_utc: str, current_price: float, *, trail_active: bool) -> dict[str, object]:
    return {
        "cycle_time_utc": cycle_time_utc,
        "position_ticket": 11,
        "symbol": "GBPUSD",
        "setup_id": "setup-11",
        "direction": "LONG",
        "entry_price": 100.0,
        "initial_stop": 98.0,
        "current_stop": 98.0,
        "initial_r_distance": 2.0,
        "current_price": current_price,
        "trail_active": trail_active,
    }


if __name__ == "__main__":
    unittest.main()
