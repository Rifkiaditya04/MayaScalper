from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest
from pathlib import Path

from tsp_v2.config import load_config
from tsp_v2.enums import Direction, ExecutionRegistryState, GovernorState, PaceClassification
from tsp_v2.execution import build_execution_intent
from tsp_v2.models import ExecutionRegistryEntry, PositionSnapshot
from tsp_v2.persistence import AccountStateRecord, SQLiteRuntimeStore
from tsp_v2.recovery import BrokerReconciliationRuntime, bootstrap_recovery_runtime


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


@dataclass
class FakeBrokerTruth:
    account: dict[str, object]
    positions: tuple[dict[str, object], ...] = ()
    orders: tuple[dict[str, object], ...] = ()
    deals: tuple[dict[str, object], ...] = ()

    def query_account(self) -> dict[str, object]:
        return dict(self.account)

    def query_positions(self) -> tuple[dict[str, object], ...]:
        return self.positions

    def query_orders(self) -> tuple[dict[str, object], ...]:
        return self.orders

    def query_deals(self) -> tuple[dict[str, object], ...]:
        return self.deals


class BrokerReconciliationRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved_env)

    def test_reconciliation_marks_match_and_persists_account_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            store = SQLiteRuntimeStore(config.persistence.sqlite_path)
            try:
                store.initialize()
                store.set_config_fingerprint(config.fingerprint)
                cycle_time = datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
                local_position = PositionSnapshot(
                    symbol="XAUUSD",
                    direction=Direction.LONG,
                    setup_id="setup-1",
                    correlation_group="XAU",
                    risk_pct=0.10,
                    signal_score=0.90,
                    open_time_utc=cycle_time,
                    pyramid_count=0,
                )
                store.store_position(local_position)
                store.store_account_state(
                    AccountStateRecord(
                        equity=100000.0,
                        balance=100000.0,
                        drawdown_pct=0.0,
                        daily_loss_pct=0.0,
                        unrealized_r=0.0,
                        updated_at_utc=cycle_time,
                    )
                )
                store.store_execution_registry(
                    (
                        ExecutionRegistryEntry(
                            setup_id="setup-1",
                            submission_uuid="submission-1",
                            symbol="XAUUSD",
                            state=ExecutionRegistryState.PENDING,
                            updated_at_utc=cycle_time,
                            direction=Direction.LONG,
                            decision_price=100.1,
                            cycle_time_utc=cycle_time,
                            expires_at_utc=cycle_time + timedelta(minutes=2),
                        ),
                    )
                )
                runtime = BrokerReconciliationRuntime(store)
                report = runtime.reconcile(
                    FakeBrokerTruth(
                        account={
                            "equity": 100000.0,
                            "balance": 100000.0,
                            "drawdown_pct": 0.0,
                            "daily_loss_pct": 0.0,
                            "unrealized_r": 0.0,
                        },
                        positions=(
                            {
                                "setup_id": "setup-1",
                                "submission_uuid": "submission-1",
                                "symbol": "XAUUSD",
                                "direction": "LONG",
                                "state": "DONE",
                                "ticket": 555,
                            },
                        ),
                    ),
                    at_utc=cycle_time + timedelta(minutes=1),
                )
                self.assertTrue(report.ready_to_resume)
                self.assertEqual(report.matched_count, 3)
                self.assertEqual(report.orphan_position_count, 0)
                self.assertEqual(report.state_divergence_count, 0)
                account_state = store.load_account_state()
                self.assertIsNotNone(account_state)
                self.assertAlmostEqual(account_state.equity, 100000.0)
                registry_entry = store.load_execution_registry()[0]
                self.assertEqual(registry_entry.state, ExecutionRegistryState.FILLED)
            finally:
                store.close()

    def test_reconciliation_reports_missing_broker_and_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            store = SQLiteRuntimeStore(config.persistence.sqlite_path)
            try:
                store.initialize()
                store.set_config_fingerprint(config.fingerprint)
                cycle_time = datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
                store.store_position(
                    PositionSnapshot(
                        symbol="XAUUSD",
                        direction=Direction.LONG,
                        setup_id="local-only",
                        correlation_group="XAU",
                        risk_pct=0.10,
                        signal_score=0.90,
                        open_time_utc=cycle_time,
                        pyramid_count=0,
                    )
                )
                runtime = BrokerReconciliationRuntime(store)
                report = runtime.reconcile(
                    FakeBrokerTruth(
                        account={
                            "equity": 100000.0,
                            "balance": 100000.0,
                            "drawdown_pct": 0.0,
                            "daily_loss_pct": 0.0,
                            "unrealized_r": 0.0,
                        },
                        positions=(
                            {
                                "setup_id": "broker-only",
                                "symbol": "XAUUSD",
                                "direction": "LONG",
                                "state": "DONE",
                                "ticket": 777,
                            },
                        ),
                    ),
                    at_utc=cycle_time + timedelta(minutes=1),
                )
                self.assertGreaterEqual(report.missing_local_count, 1)
                self.assertEqual(report.orphan_position_count, 1)
                statuses = {finding.status for finding in report.findings}
                self.assertIn("MISSING_BROKER", statuses)
                self.assertIn("ORPHAN_POSITION", statuses)
            finally:
                store.close()

    def test_expired_entries_without_ticket_are_not_counted_as_missing_broker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            store = SQLiteRuntimeStore(config.persistence.sqlite_path)
            try:
                store.initialize()
                store.set_config_fingerprint(config.fingerprint)
                cycle_time = datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
                store.store_execution_registry(
                    (
                        ExecutionRegistryEntry(
                            setup_id="expired-1",
                            submission_uuid="expired-1",
                            symbol="XAUUSD",
                            state=ExecutionRegistryState.EXPIRED,
                            updated_at_utc=cycle_time - timedelta(minutes=1),
                            direction=Direction.LONG,
                            decision_price=100.1,
                            cycle_time_utc=cycle_time - timedelta(minutes=2),
                            expires_at_utc=cycle_time - timedelta(minutes=1),
                        ),
                        ExecutionRegistryEntry(
                            setup_id="expired-2",
                            submission_uuid="expired-2",
                            symbol="XAUUSD",
                            state=ExecutionRegistryState.CANCELLED,
                            updated_at_utc=cycle_time - timedelta(minutes=1),
                            direction=Direction.SHORT,
                            decision_price=100.2,
                            cycle_time_utc=cycle_time - timedelta(minutes=2),
                            expires_at_utc=cycle_time - timedelta(minutes=1),
                        ),
                        ExecutionRegistryEntry(
                            setup_id="active-1",
                            submission_uuid="active-1",
                            symbol="XAUUSD",
                            state=ExecutionRegistryState.PENDING,
                            updated_at_utc=cycle_time,
                            direction=Direction.LONG,
                            decision_price=100.3,
                            cycle_time_utc=cycle_time,
                            expires_at_utc=cycle_time + timedelta(minutes=2),
                        ),
                    )
                )
                runtime = BrokerReconciliationRuntime(store)
                report = runtime.reconcile(
                    FakeBrokerTruth(
                        account={
                            "equity": 100000.0,
                            "balance": 100000.0,
                            "drawdown_pct": 0.0,
                            "daily_loss_pct": 0.0,
                            "unrealized_r": 0.0,
                        },
                    ),
                    at_utc=cycle_time + timedelta(seconds=30),
                )
                self.assertEqual(report.missing_broker_count, 1)
                missing_broker_identifiers = {
                    finding.identifier
                    for finding in report.findings
                    if finding.scope == "registry" and finding.status == "MISSING_BROKER"
                }
                self.assertEqual(missing_broker_identifiers, {"active-1"})
                self.assertTrue(report.ready_to_resume is False)
            finally:
                store.close()

    def test_reconciliation_detects_account_and_order_divergence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            store = SQLiteRuntimeStore(config.persistence.sqlite_path)
            try:
                store.initialize()
                store.set_config_fingerprint(config.fingerprint)
                cycle_time = datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
                store.store_account_state(
                    AccountStateRecord(
                        equity=100000.0,
                        balance=100000.0,
                        drawdown_pct=0.0,
                        daily_loss_pct=0.0,
                        unrealized_r=0.0,
                        updated_at_utc=cycle_time,
                    )
                )
                store.store_execution_registry(
                    (
                        ExecutionRegistryEntry(
                            setup_id="setup-1",
                            submission_uuid="submission-1",
                            symbol="XAUUSD",
                            state=ExecutionRegistryState.PENDING,
                            updated_at_utc=cycle_time,
                            direction=Direction.LONG,
                            decision_price=100.1,
                            cycle_time_utc=cycle_time,
                            expires_at_utc=cycle_time + timedelta(minutes=2),
                        ),
                    )
                )
                runtime = BrokerReconciliationRuntime(store)
                report = runtime.reconcile(
                    FakeBrokerTruth(
                        account={
                            "equity": 99950.0,
                            "balance": 99950.0,
                            "drawdown_pct": 0.5,
                            "daily_loss_pct": 0.1,
                            "unrealized_r": -0.1,
                        },
                        orders=(
                            {
                                "setup_id": "setup-1",
                                "submission_uuid": "submission-1",
                                "symbol": "XAUUSD",
                                "state": "REJECTED",
                                "ticket": 999,
                            },
                        ),
                    ),
                    at_utc=cycle_time + timedelta(minutes=1),
                )
                self.assertGreaterEqual(report.account_divergence_count, 1)
                self.assertGreaterEqual(report.state_divergence_count, 1)
                statuses = {finding.status for finding in report.findings}
                self.assertIn("STATE_DIVERGENCE", statuses)
            finally:
                store.close()

    def test_bootstrap_uses_broker_truth_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            store = SQLiteRuntimeStore(config.persistence.sqlite_path)
            try:
                store.initialize()
                store.set_config_fingerprint(config.fingerprint)
                store.store_execution_registry(
                    (
                        ExecutionRegistryEntry(
                            setup_id="setup-1",
                            submission_uuid="submission-1",
                            symbol="XAUUSD",
                            state=ExecutionRegistryState.PENDING,
                            updated_at_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                            direction=Direction.LONG,
                            decision_price=100.1,
                            cycle_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                            expires_at_utc=datetime(2026, 5, 29, 9, 2, 0, tzinfo=timezone.utc),
                        ),
                    )
                )
                report = bootstrap_recovery_runtime(
                    store,
                    schema_version="1",
                    config_fingerprint=config.fingerprint,
                    broker_truth_provider=FakeBrokerTruth(
                        account={
                            "equity": 100000.0,
                            "balance": 100000.0,
                            "drawdown_pct": 0.0,
                            "daily_loss_pct": 0.0,
                            "unrealized_r": 0.0,
                        },
                        positions=(
                            {
                                "setup_id": "setup-1",
                                "submission_uuid": "submission-1",
                                "symbol": "XAUUSD",
                                "direction": "LONG",
                                "state": "DONE",
                                "ticket": 555,
                            },
                        ),
                    ),
                    current_time_utc=datetime(2026, 5, 29, 9, 1, 0, tzinfo=timezone.utc),
                    lock_owned=True,
                )
                self.assertTrue(report.ready_to_resume)
                self.assertEqual(report.reconciliation_report.filled_count, 1)
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
