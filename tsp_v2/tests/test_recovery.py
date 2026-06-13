from __future__ import annotations

from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

from tsp_v2.config import load_config
from tsp_v2.config_schema import ConfigValidationError
from tsp_v2.enums import Direction, ExecutionRegistryState, GovernorState, RiskAction, SignalFamily
from tsp_v2.execution import build_execution_intent
from tsp_v2.models import ExecutionRegistryEntry, RiskDecision, SignalDecision
from tsp_v2.persistence import SQLiteRuntimeStore
from tsp_v2.recovery import bootstrap_recovery_runtime, build_reconciliation_report, check_duplicate_submission


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


class RecoveryTests(unittest.TestCase):
    def test_bootstrap_recovery_with_unresolved_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            store = SQLiteRuntimeStore(config.persistence.sqlite_path)
            try:
                store.initialize()
                store.set_config_fingerprint(config.fingerprint)
                entry = ExecutionRegistryEntry(
                    setup_id="setup-1",
                    submission_uuid="submission-1",
                    symbol="XAUUSD",
                    state=ExecutionRegistryState.PENDING,
                    updated_at_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                    direction=Direction.LONG,
                    decision_price=100.1,
                    cycle_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                    expires_at_utc=datetime(2026, 5, 29, 9, 2, 0, tzinfo=timezone.utc),
                )
                store.store_execution_registry((entry,))
                report = bootstrap_recovery_runtime(
                    store,
                    schema_version="1",
                    config_fingerprint=config.fingerprint,
                    broker_positions=(),
                    current_time_utc=datetime(2026, 5, 29, 9, 1, 0, tzinfo=timezone.utc),
                    lock_owned=True,
                    allow_flatten_unresolved=False,
                )
                self.assertFalse(report.ready_to_resume)
                self.assertIn("setup-1", report.unresolved_setup_ids)
            finally:
                store.close()

    def test_reconciliation_marks_filled_from_broker_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            store = SQLiteRuntimeStore(config.persistence.sqlite_path)
            try:
                store.initialize()
                store.set_config_fingerprint(config.fingerprint)
                entry = ExecutionRegistryEntry(
                    setup_id="setup-1",
                    submission_uuid="submission-1",
                    symbol="XAUUSD",
                    state=ExecutionRegistryState.PENDING,
                    updated_at_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                    direction=Direction.LONG,
                    decision_price=100.1,
                    cycle_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                    expires_at_utc=datetime(2026, 5, 29, 9, 2, 0, tzinfo=timezone.utc),
                )
                store.store_execution_registry((entry,))
                report = build_reconciliation_report(
                    store,
                    ({"submission_uuid": "submission-1", "state": "DONE", "ticket": 555},),
                    at_utc=datetime(2026, 5, 29, 9, 0, 10, tzinfo=timezone.utc),
                )
                self.assertEqual(report.filled_count, 1)
                self.assertEqual(report.reconciled_entries[0].state, ExecutionRegistryState.FILLED)
            finally:
                store.close()

    def test_duplicate_check_reads_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            store = SQLiteRuntimeStore(config.persistence.sqlite_path)
            try:
                store.initialize()
                store.set_config_fingerprint(config.fingerprint)
                signal = _signal()
                risk = _risk()
                intent = build_execution_intent(signal, risk, decision_price=100.1, cycle_time_utc=signal.expires_at_utc - timedelta(seconds=120))
                store.store_execution_registry(
                    (
                        ExecutionRegistryEntry(
                            setup_id=intent.setup_id,
                            submission_uuid=intent.submission_uuid,
                            symbol=intent.symbol,
                            state=ExecutionRegistryState.SUBMITTED,
                            updated_at_utc=signal.expires_at_utc - timedelta(seconds=100),
                            direction=Direction.LONG,
                            decision_price=100.1,
                            cycle_time_utc=signal.expires_at_utc - timedelta(seconds=120),
                            expires_at_utc=signal.expires_at_utc,
                        ),
                    )
                )
                duplicate = check_duplicate_submission(store, intent, at_utc=signal.expires_at_utc - timedelta(seconds=10))
                self.assertTrue(duplicate.duplicate)
            finally:
                store.close()

    def test_bootstrap_rejects_fingerprint_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            store = SQLiteRuntimeStore(config.persistence.sqlite_path)
            try:
                store.initialize()
                store.set_config_fingerprint("different")
                with self.assertRaises(ConfigValidationError):
                    bootstrap_recovery_runtime(
                        store,
                        schema_version="1",
                        config_fingerprint=config.fingerprint,
                        broker_positions=(),
                        current_time_utc=datetime(2026, 5, 29, 9, 1, 0, tzinfo=timezone.utc),
                        lock_owned=True,
                    )
            finally:
                store.close()


def _signal() -> SignalDecision:
    cycle_time = datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
    return SignalDecision(
        setup_id="setup-1",
        signal_family=SignalFamily.TREND_CONTINUATION,
        symbol="XAUUSD",
        direction=Direction.LONG,
        score=0.90,
        threshold=0.72,
        expires_at_utc=cycle_time + timedelta(seconds=120),
        rationale="test",
        lineage=("REGIME:TREND", "FAMILY:TREND_CONTINUATION"),
    )


def _risk() -> RiskDecision:
    return RiskDecision(
        action=RiskAction.ENTER,
        risk_multiplier=1.0,
        sized_volume=0.10,
        invalidation_price=99.5,
        hard_block_reason="",
        governor_adjusted_state=GovernorState.NORMAL,
    )


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
