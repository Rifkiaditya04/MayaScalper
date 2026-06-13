from __future__ import annotations

from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path

from tsp_v2.config import load_config
from tsp_v2.enums import ExecutionRegistryState, GovernorState, PaceClassification
from tsp_v2.models import ExecutionRegistryEntry, GovernorDecision
from tsp_v2.persistence import AccountStateRecord, SQLiteRuntimeStore, SCHEMA_VERSION


BASE_TEMPLATE = """
bot:
  mode: FORWARD_TEST
  profile: FORWARD_SAFE
  expert_mode: false
  poll_interval_seconds: 5
symbols:
  allowlist:
    - XAUUSD
alpha:
  setup_cooldown_bars: 1
regime:
  closed_bar_timeframe: M5
  news_lockout_minutes: 30
signal:
  min_score: 0.65
  ttl_seconds: 300
risk:
  max_open_risk_pct: 1.0
  max_daily_loss_pct: 5.0
governor:
  initial_state: NORMAL
  kill_review_drawdown_pct: 15.0
  offensive_profiles_require_expert_mode: true
lifecycle:
  thesis_ttl_minutes: 60
  break_even_after_r: 1.0
execution:
  signal_ttl_seconds: 300
  slippage_veto_ratio: 0.30
  max_spread_ratio: 1.80
telemetry:
  heartbeat_interval_seconds: 5
  emit_candidate_diagnostics: true
persistence:
  sqlite_path: {sqlite_path}
  lock_path: {lock_path}
  wal_enabled: true
contest:
  ranking_proxy_enabled: false
  contest_window_minutes: 1440
deployment:
  runtime_root: runtime
  log_root: logs
  report_root: reports
  allow_live_execution: false
news:
  provider_mode: STATIC_FILE
  source_path: {news_path}
  stale_warn_minutes: 15
  stale_soft_fail_minutes: 30
  stale_hard_fail_minutes: 60
"""


class PersistenceTests(unittest.TestCase):
    def test_initialize_and_roundtrip_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            store = SQLiteRuntimeStore(config.persistence.sqlite_path)
            try:
                store.initialize()
                store.set_config_fingerprint(config.fingerprint)
                self.assertEqual(store.get_schema_version(), SCHEMA_VERSION)
                self.assertEqual(store.get_config_fingerprint(), config.fingerprint)

                entry = ExecutionRegistryEntry(
                    setup_id="setup-1",
                    submission_uuid="submission-1",
                    symbol="XAUUSD",
                    state=ExecutionRegistryState.PENDING,
                    updated_at_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                    direction=None,
                    decision_price=100.1,
                    cycle_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                    expires_at_utc=datetime(2026, 5, 29, 9, 2, 0, tzinfo=timezone.utc),
                )
                store.store_execution_registry((entry,))
                rows = store.load_execution_registry()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].submission_uuid, "submission-1")

                store.store_account_state(
                    AccountStateRecord(
                        equity=100000.0,
                        balance=99000.0,
                        drawdown_pct=1.0,
                        daily_loss_pct=0.2,
                        unrealized_r=0.1,
                        updated_at_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                    )
                )
                account_state = store.load_account_state()
                self.assertIsNotNone(account_state)
                self.assertAlmostEqual(account_state.equity, 100000.0)
            finally:
                store.close()

    def test_store_governor_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            store = SQLiteRuntimeStore(config.persistence.sqlite_path)
            try:
                store.initialize()
                store.set_config_fingerprint(config.fingerprint)
                decision = GovernorDecision(
                    state=GovernorState.NORMAL,
                    state_reason="test",
                    pace_classification=PaceClassification.ON_TRACK,
                    aggression_multiplier=1.0,
                    profile_constraints={"profile": "FORWARD_SAFE"},
                )
                store.store_governor_state(decision)
                record = store.load_governor_state()
                self.assertIsNotNone(record)
                self.assertEqual(record.state, GovernorState.NORMAL)
            finally:
                store.close()


def _load_config(root: Path):
    news_path = root / "news.json"
    news_path.write_text(
        "{\"generated_at_utc\":\"2026-05-29T09:00:00+00:00\",\"events\":[]}",
        encoding="utf-8",
    )
    sqlite_path = root / "runtime.db"
    lock_path = root / "locks"
    base_text = BASE_TEMPLATE.format(
        sqlite_path=str(sqlite_path).replace("\\", "/"),
        lock_path=str(lock_path).replace("\\", "/"),
        news_path=str(news_path).replace("\\", "/"),
    )
    (root / "base.yaml").write_text(base_text, encoding="utf-8")
    (root / "profile.yaml").write_text(
        "bot:\n  mode: FORWARD_TEST\n  profile: FORWARD_SAFE\n",
        encoding="utf-8",
    )
    (root / ".env").write_text(
        "\n".join(
            (
                "TSP_V2_MT5_LOGIN=1",
                "TSP_V2_MT5_PASSWORD=secret",
                "TSP_V2_MT5_SERVER=Demo",
                "TSP_V2_MT5_TERMINAL_PATH=C:\\MT5\\terminal64.exe",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return load_config(config_path=root / "profile.yaml", env_path=root / ".env")


if __name__ == "__main__":
    unittest.main()
