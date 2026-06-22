from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tsp_v2.adapters import MT5BridgeError, MT5BridgeStatus, MT5TradeResult
from tsp_v2.config import load_config
from tsp_v2.config_schema import ConfigValidationError
import tsp_v2.deployment as deployment_module
from tsp_v2.deployment import DeploymentRuntime, SingleInstanceLock
from tsp_v2.enums import ClockHealth, Direction, GovernorState, HealthState, PaceClassification
from tsp_v2.live_runtime import LiveCycleReport, LiveRuntimeRunner
from tsp_v2.persistence import SQLiteRuntimeStore
from tsp_v2.models import ExecutionResult


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
  allow_live_execution: {allow_live_execution}
news:
  provider_mode: STATIC_FILE
  source_path: {news_path}
  stale_warn_minutes: 15
  stale_soft_fail_minutes: 30
  stale_hard_fail_minutes: 60
"""


@dataclass(frozen=True, slots=True)
class FakeProbe:
    broker_time_value: datetime
    symbols: tuple[str, ...] = ("XAUUSD",)
    broker_ready_flag: bool = True
    account_payload: dict[str, object] | None = None
    positions_payload: tuple[dict[str, object], ...] = ()

    def broker_ready(self) -> bool:
        return self.broker_ready_flag

    def broker_time_utc(self) -> datetime:
        return self.broker_time_value

    def supported_symbols(self) -> tuple[str, ...]:
        return self.symbols

    def account_snapshot(self) -> dict[str, object]:
        return self.account_payload or {"equity": 100000.0}

    def live_positions(self) -> tuple[dict[str, object], ...]:
        return self.positions_payload


@dataclass
class FakeLiveBridge:
    broker_time_value: datetime
    connected: bool = False
    connect_calls: int = 0
    position_ticket: int = 0
    positions_payload: list[dict[str, object]] = None
    mt5_module: object | None = None

    def __post_init__(self) -> None:
        if self.positions_payload is None:
            self.positions_payload = []

    @property
    def bridge(self) -> FakeLiveBridge:
        return self

    def connect(self) -> MT5BridgeStatus:
        self.connect_calls += 1
        self.connected = True
        return MT5BridgeStatus(
            ok=True,
            failure_class="",
            response_class="OK",
            retryable=False,
            fatal=False,
            message="connected",
            terminal_ready=True,
            broker_ready=True,
            diagnostics={"connected": True},
        )

    def disconnect(self) -> MT5BridgeStatus:
        self.connected = False
        return MT5BridgeStatus(
            ok=True,
            failure_class="",
            response_class="OK",
            retryable=False,
            fatal=False,
            message="disconnected",
            terminal_ready=False,
            broker_ready=False,
            diagnostics={"connected": False},
        )

    def broker_ready(self) -> bool:
        if not self.connected:
            self.connect()
        return self.heartbeat().ok

    def broker_time_utc(self) -> datetime:
        return self.broker_time_value

    def supported_symbols(self) -> tuple[str, ...]:
        return ("XAUUSD",)

    def account_snapshot(self) -> dict[str, object]:
        return self.query_account()

    def live_positions(self) -> tuple[dict[str, object], ...]:
        return tuple(self.positions_payload)

    def heartbeat(self) -> MT5BridgeStatus:
        return MT5BridgeStatus(
            ok=self.connected,
            failure_class="" if self.connected else "BROKER_DISCONNECTED",
            response_class="OK" if self.connected else "FAIL_STARTUP",
            retryable=False,
            fatal=not self.connected,
            message="healthy" if self.connected else "unavailable",
            terminal_ready=self.connected,
            broker_ready=self.connected,
            diagnostics={"connected": self.connected},
        )

    def close(self) -> None:
        self.disconnect()

    def query_account(self) -> dict[str, object]:
        return {
            "equity": 100000.0,
            "balance": 100000.0,
            "drawdown_pct": 0.0,
            "daily_loss_pct": 0.0,
            "unrealized_r": 0.0,
            "realized_pnl_r": 0.0,
        }

    def query_symbol_contract(self, symbol: str) -> dict[str, object]:
        return {
            "symbol": symbol.upper(),
            "visible": True,
            "point": 0.1,
            "trade_tick_size": 0.1,
            "trade_tick_value": 1.0,
            "volume_min": 0.01,
            "volume_max": 1.0,
            "volume_step": 0.01,
            "trade_stops_level": 0,
            "trade_freeze_level": 0,
            "digits": 2,
        }

    def get_latest_tick(self, symbol: str) -> dict[str, object]:
        return {
            "symbol": symbol.upper(),
            "time": self.broker_time_value,
            "timestamp": self.broker_time_value,
            "bid": 100.0,
            "ask": 100.1,
            "last": 100.05,
            "volume": 1.0,
        }

    def get_rates(self, symbol: str, timeframe: str, count: int) -> tuple[dict[str, object], ...]:
        timeframe_minutes = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}[timeframe.upper()]
        end_time = self.broker_time_value - timedelta(minutes=timeframe_minutes)
        return _build_trend_rates(
            symbol.upper(),
            timeframe=timeframe.upper(),
            count=count,
            end_time=end_time,
            step=0.5 if timeframe_minutes <= 5 else 1.5,
        )

    def query_positions(self, symbol: str | None = None, ticket: int | None = None) -> tuple[dict[str, object], ...]:
        del symbol, ticket
        return tuple(self.positions_payload)

    def query_orders(self, symbol: str | None = None, ticket: int | None = None) -> tuple[dict[str, object], ...]:
        del symbol, ticket
        return ()

    def query_deals(
        self,
        symbol: str | None = None,
        ticket: int | None = None,
        *,
        from_time_utc: datetime | None = None,
        to_time_utc: datetime | None = None,
    ) -> tuple[dict[str, object], ...]:
        del symbol, ticket, from_time_utc, to_time_utc
        return ()

    def place_order(self, request: dict[str, object]) -> MT5TradeResult:
        self.position_ticket += 1
        comment = str(request.get("comment", ""))
        setup_id = comment.split("|")[1] if "|" in comment else comment
        submission_uuid = comment.split("|")[2] if comment.count("|") >= 2 else f"submission-{self.position_ticket}"
        direction = Direction.LONG if str(request.get("type", "")).upper() in {"BUY", "LONG"} else Direction.SHORT
        position = {
            "symbol": str(request.get("symbol", "")).upper(),
            "direction": direction.value,
            "setup_id": setup_id,
            "submission_uuid": submission_uuid,
            "position_ticket": self.position_ticket,
            "ticket": self.position_ticket,
            "risk_pct": 0.5,
            "signal_score": 0.8,
        }
        self.positions_payload.append(position)
        response = {"retcode": "DONE", "ticket": self.position_ticket}
        return MT5TradeResult(
            ok=True,
            failure_class="",
            response_class="OK",
            retryable=False,
            fatal=False,
            terminal=True,
            message="filled",
            ticket=self.position_ticket,
            request=dict(request),
            response=response,
            diagnostics={"retcode": "DONE"},
        )


def _build_trend_rates(
    symbol: str,
    *,
    timeframe: str,
    count: int,
    end_time: datetime,
    step: float,
) -> tuple[dict[str, object], ...]:
    minutes = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}[timeframe]
    start_time = end_time - timedelta(minutes=(count - 1) * minutes)
    bars: list[dict[str, object]] = []
    base = 95.0
    for idx in range(count):
        open_price = base + idx * step
        close_price = open_price + (step * 0.8)
        high_price = max(open_price, close_price) + (step * 0.2)
        low_price = min(open_price, close_price) - (step * 0.2)
        bars.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "time": start_time + timedelta(minutes=idx * minutes),
                "open": round(open_price, 5),
                "high": round(high_price, 5),
                "low": round(low_price, 5),
                "close": round(close_price, 5),
                "tick_volume": 100 + idx,
            }
        )
    return tuple(bars)


class DeploymentTests(unittest.TestCase):
    def test_preflight_dry_run_succeeds_and_releases_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            runtime = DeploymentRuntime(config)
            report = runtime.preflight(
                dry_run=True,
                current_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(report.ok)
            self.assertEqual(report.blocked_reason, "")
            self.assertIsNotNone(report.lock_snapshot)
            self.assertFalse(runtime.lock.is_owned())

    def test_preflight_live_probe_checks_broker_and_clock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root, allow_live_execution=True)
            runtime = DeploymentRuntime(config)
            probe = FakeProbe(
                broker_time_value=datetime(2026, 5, 29, 9, 0, 10, tzinfo=timezone.utc),
            )
            report = runtime.preflight(
                dry_run=False,
                probe=probe,
                current_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(report.ok)
            self.assertTrue(report.broker_ready)
            self.assertEqual(report.clock_health, ClockHealth.OK)
            self.assertFalse(runtime.lock.is_owned())

    def test_lock_ownership_blocks_second_acquire(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "runtime.lock"
            first = SingleInstanceLock(lock_path)
            second = SingleInstanceLock(lock_path)
            first.acquire(owner="first", now_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc))
            with self.assertRaises(ConfigValidationError):
                second.acquire(owner="second", now_utc=datetime(2026, 5, 29, 9, 0, 1, tzinfo=timezone.utc))
            first.release()

    def test_process_is_alive_returns_true_for_current_pid(self) -> None:
        self.assertTrue(deployment_module._process_is_alive(os.getpid()))

    def test_process_is_alive_returns_false_for_non_positive_pid(self) -> None:
        self.assertFalse(deployment_module._process_is_alive(0))
        self.assertFalse(deployment_module._process_is_alive(-1))

    @unittest.skipUnless(os.name == "nt", "Windows-specific probe")
    def test_process_is_alive_maps_windows_liveness_probe_oserror_to_false(self) -> None:
        with patch.object(
            deployment_module,
            "_windows_process_is_alive",
            side_effect=OSError(87, "The parameter is incorrect"),
        ):
            self.assertFalse(deployment_module._process_is_alive(12345))

    @unittest.skipUnless(os.name == "nt", "Windows-specific probe")
    def test_process_is_alive_propagates_fatal_windows_probe_error(self) -> None:
        with patch.object(
            deployment_module,
            "_windows_process_is_alive",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                deployment_module._process_is_alive(12345)

    def test_preflight_reclaims_stale_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            lock_path = config.persistence.lock_path / "tsp_v2.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            stale_payload = {
                "owner": "previous",
                "pid": 99999999,
                "token": "deadbeef",
                "created_at_utc": "2026-05-28T09:00:00+00:00",
                "updated_at_utc": "2026-05-28T09:00:00+00:00",
                "reclaimed": False,
            }
            lock_path.write_text(json.dumps(stale_payload), encoding="utf-8")
            runtime = DeploymentRuntime(config)
            report = runtime.preflight(
                dry_run=True,
                current_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(report.ok)
            self.assertIsNotNone(report.lock_snapshot)
            self.assertTrue(report.lock_snapshot.reclaimed)

    def test_start_persists_metadata_and_shutdown_is_graceful(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            runtime = DeploymentRuntime(config)
            report = runtime.start(
                dry_run=True,
                current_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(report.bootstrap_report.ready_to_resume)
            store = runtime.store
            self.assertIsNotNone(store)
            assert store is not None
            self.assertEqual(store.get_meta("deployment.version"), "V2")
            self.assertEqual(store.get_meta("deployment.schema_version"), "1")
            shutdown = runtime.shutdown(
                reason="graceful_exit",
                current_time_utc=datetime(2026, 5, 29, 9, 5, 0, tzinfo=timezone.utc),
            )
            self.assertFalse(runtime.lock.is_owned())
            self.assertTrue(shutdown.lock_released)

    def test_start_live_activation_runs_one_cycle_through_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root, allow_live_execution=True)
            fake_bridge = FakeLiveBridge(
                broker_time_value=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
            )
            runtime = DeploymentRuntime(config, bridge=fake_bridge)
            report = runtime.start(
                dry_run=False,
                probe=fake_bridge,
                current_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                max_cycles=1,
            )
            self.assertIsNotNone(report.live_report)
            assert report.live_report is not None
            self.assertEqual(report.live_report.cycles_completed, 1)
            self.assertGreaterEqual(report.live_report.last_cycle.execution_count, 0)
            self.assertTrue(fake_bridge.connected)
            shutdown = runtime.shutdown(
                reason="test_complete",
                current_time_utc=datetime(2026, 5, 29, 9, 1, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(shutdown.lock_released)
            self.assertFalse(fake_bridge.connected)

    def test_start_live_activation_reuses_probe_bridge_without_reinitializing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root, allow_live_execution=True)
            fake_bridge = FakeLiveBridge(
                broker_time_value=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
            )
            runtime = DeploymentRuntime(config)
            with (
                patch.object(DeploymentRuntime, "_ensure_bridge", side_effect=AssertionError("_ensure_bridge should not be called when probe bridge is available")),
                patch.object(DeploymentRuntime, "_build_live_probe", return_value=fake_bridge),
            ):
                report = runtime.start(
                    dry_run=False,
                    current_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                    max_cycles=1,
                )
            try:
                self.assertIsNotNone(report.live_report)
                assert report.live_report is not None
                self.assertEqual(report.live_report.cycles_completed, 1)
                self.assertEqual(fake_bridge.connect_calls, 1)
            finally:
                runtime.shutdown(reason="test_complete", current_time_utc=datetime(2026, 5, 29, 9, 1, 0, tzinfo=timezone.utc))

    def test_start_waits_for_snapshot_readiness_before_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root, allow_live_execution=True)
            fake_bridge = FakeLiveBridge(
                broker_time_value=datetime(2026, 5, 29, 9, 0, 30, tzinfo=timezone.utc),
            )
            runtime = DeploymentRuntime(config)
            build_calls: list[datetime] = []
            sleep_calls: list[float] = []

            def fake_build_market_snapshot(
                provider,
                *,
                config,
                symbol,
                cycle_time_utc,
                previous_cycle_time_utc=None,
                build_config=None,
                diagnostics_hook=None,
            ):
                del provider, config, symbol, previous_cycle_time_utc, build_config
                build_calls.append(cycle_time_utc)
                if len(build_calls) == 1:
                    payload = {
                        "stage": "closed_bars_insufficient",
                        "symbol": "XAUUSD",
                        "timeframe": "M5",
                        "cycle_time_utc": cycle_time_utc.isoformat(),
                        "requested_bars": 71,
                        "returned_bars": 71,
                        "closed_bar_count": 70,
                        "minimum_closed_bar_count": 70,
                        "payload_health": "GREEN",
                    }
                    if diagnostics_hook is not None:
                        diagnostics_hook(payload)
                    raise ConfigValidationError("Not enough closed bars for timeframe M5: need at least 70")
                payload = {
                    "stage": "snapshot_ready",
                    "symbol": "XAUUSD",
                    "cycle_time_utc": cycle_time_utc.isoformat(),
                    "requested_bars": {"M1": 40, "M5": 71, "M15": 40, "H1": 40},
                    "returned_bars": {"M1": 40, "M5": 71, "M15": 40, "H1": 40},
                    "closed_bar_count": {"M1": 40, "M5": 70, "M15": 40, "H1": 40},
                    "payload_health": "GREEN",
                }
                if diagnostics_hook is not None:
                    diagnostics_hook(payload)
                return SimpleNamespace(cycle_time_utc=cycle_time_utc, bars_m5=())

            with (
                patch.object(DeploymentRuntime, "_build_live_probe", return_value=fake_bridge),
                patch("tsp_v2.deployment.build_market_snapshot", side_effect=fake_build_market_snapshot),
                patch.object(deployment_module.time, "sleep", side_effect=lambda seconds: sleep_calls.append(float(seconds))),
            ):
                report = runtime.start(
                    dry_run=False,
                    current_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                    max_cycles=0,
            )

            self.assertIsNotNone(report.bootstrap_report)
            self.assertTrue(report.bootstrap_report.ready_to_resume)
            self.assertIsNotNone(report.live_report)
            assert report.live_report is not None
            self.assertEqual(report.live_report.cycles_completed, 0)
            self.assertEqual(fake_bridge.connect_calls, 1)
            self.assertGreaterEqual(len(build_calls), 2)
            self.assertTrue(sleep_calls)
            self.assertGreater(sleep_calls[0], 0.0)

            telemetry_db = sqlite3.connect(config.persistence.sqlite_path)
            telemetry_db.row_factory = sqlite3.Row
            rows = telemetry_db.execute(
                """
                SELECT payload_json
                FROM telemetry_index
                WHERE topic = 'deployment.startup_sync'
                ORDER BY rowid ASC
                """
            ).fetchall()
            telemetry_db.close()
            self.assertGreaterEqual(len(rows), 2)
            first_payload = json.loads(rows[0]["payload_json"])
            last_payload = json.loads(rows[-1]["payload_json"])
            self.assertEqual(first_payload["stage"], "waiting_for_snapshot_readiness")
            self.assertFalse(first_payload["snapshot_ready"])
            self.assertEqual(last_payload["stage"], "snapshot_ready")
            self.assertTrue(last_payload["snapshot_ready"])
            shutdown = runtime.shutdown(
                reason="test_complete",
                current_time_utc=datetime(2026, 5, 29, 9, 1, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(shutdown.lock_released)

    def test_closed_m5_gate_uses_latest_close_and_skips_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root, allow_live_execution=True)
            store = SQLiteRuntimeStore(config.persistence.sqlite_path)
            store.initialize()
            store.set_config_fingerprint(config.fingerprint)
            try:
                runner = LiveRuntimeRunner(
                    config=config,
                    store=store,
                    bridge=SimpleNamespace(),
                    market_adapter=SimpleNamespace(),
                    execution_adapter=SimpleNamespace(
                        registry=SimpleNamespace(entries_by_setup_id={}, entries_by_submission_uuid={})
                    ),
                    bootstrap_report=SimpleNamespace(ready_to_resume=True),
                )
                older = datetime(2026, 5, 29, 9, 5, 0, tzinfo=timezone.utc)
                latest = datetime(2026, 5, 29, 9, 10, 0, tzinfo=timezone.utc)
                snapshot = SimpleNamespace(
                    bars_m5=(
                        {"close_time_utc": older},
                        {"close_time_utc": latest},
                        {"close_time_utc": datetime(2026, 5, 29, 9, 7, 0, tzinfo=timezone.utc)},
                    )
                )

                self.assertEqual(runner._latest_closed_m5_close(snapshot), latest)
                runner.runtime_state.last_processed_m5_close_utc = latest
                self.assertFalse(runner._closed_m5_gate_allows_process(latest))

                telemetry_db = sqlite3.connect(config.persistence.sqlite_path)
                telemetry_db.row_factory = sqlite3.Row
                rows = telemetry_db.execute(
                    """
                    SELECT payload_json
                    FROM telemetry_index
                    WHERE topic = 'deployment.closed_m5_gate'
                    ORDER BY rowid DESC
                    LIMIT 1
                    """
                ).fetchall()
                telemetry_db.close()
                self.assertTrue(rows)
                payload = json.loads(rows[0]["payload_json"])
                self.assertEqual(payload["decision"], "skip")
                self.assertEqual(payload["current_m5_close"], latest.isoformat())
                self.assertEqual(payload["last_processed_m5_close"], latest.isoformat())
            finally:
                store.close()

    def test_run_counts_only_processed_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root, allow_live_execution=True)
            store = SQLiteRuntimeStore(config.persistence.sqlite_path)
            store.initialize()
            store.set_config_fingerprint(config.fingerprint)
            try:
                runner = LiveRuntimeRunner(
                    config=config,
                    store=store,
                    bridge=SimpleNamespace(),
                    market_adapter=SimpleNamespace(),
                    execution_adapter=SimpleNamespace(
                        registry=SimpleNamespace(entries_by_setup_id={}, entries_by_submission_uuid={})
                    ),
                    bootstrap_report=SimpleNamespace(ready_to_resume=True),
                )
                report = LiveCycleReport(
                    cycle_time_utc=datetime(2026, 5, 29, 9, 10, 0, tzinfo=timezone.utc),
                    broker_time_utc=datetime(2026, 5, 29, 9, 10, 0, tzinfo=timezone.utc),
                    governor_state=GovernorState.NORMAL,
                    governor_reason="test",
                    selected_symbols=(),
                    signal_count=0,
                    execution_count=0,
                    execution_results=(),
                    reconciliation_ready=True,
                    market_health=HealthState.GREEN,
                    feed_health=HealthState.GREEN,
                    pace_state=PaceClassification.ON_TRACK,
                )
                with (
                    patch.object(LiveRuntimeRunner, "_run_cycle", side_effect=[None, None, report]),
                    patch("tsp_v2.live_runtime.time.sleep", return_value=None),
                ):
                    runtime_report = runner.run(
                        max_cycles=1,
                        current_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                    )
                self.assertEqual(runtime_report.cycles_completed, 1)
                self.assertIsNotNone(runtime_report.last_cycle)
                self.assertEqual(runtime_report.last_cycle, report)
            finally:
                store.close()

    def test_emit_execution_telemetry_persists_request_response_and_last_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root, allow_live_execution=True)
            store = SQLiteRuntimeStore(config.persistence.sqlite_path)
            store.initialize()
            store.set_config_fingerprint(config.fingerprint)
            try:
                runner = LiveRuntimeRunner(
                    config=config,
                    store=store,
                    bridge=SimpleNamespace(),
                    market_adapter=SimpleNamespace(),
                    execution_adapter=SimpleNamespace(
                        registry=SimpleNamespace(entries_by_setup_id={}, entries_by_submission_uuid={})
                    ),
                    bootstrap_report=SimpleNamespace(ready_to_resume=True),
                )
                result = ExecutionResult(
                    accepted=False,
                    rejected=True,
                    filled=False,
                    partial_fill=False,
                    ticket=None,
                    broker_code="API_HUNG",
                    classification="ESCALATE_KILL_REVIEW",
                    retryable=False,
                    fatal=True,
                    terminal=False,
                    message="order_send returned None | last_error=-2:Unnamed arguments not allowed",
                    submission_uuid="submission-1",
                    setup_id="setup-1",
                    symbol="GBPUSD",
                    request={
                        "action": "TRADE_ACTION_DEAL",
                        "symbol": "GBPUSD",
                        "volume": 0.1,
                        "type": "BUY",
                        "price": 1.32265,
                        "deviation": 20,
                        "comment": "TSP_V2|setup-1|submission-1",
                        "type_time": "ORDER_TIME_GTC",
                        "type_filling": "ORDER_FILLING_RETURN",
                    },
                    response={},
                    diagnostics={
                        "bridge": {
                            "ok": False,
                            "failure_class": "API_HUNG",
                            "response_class": "ESCALATE_KILL_REVIEW",
                            "retryable": False,
                            "fatal": True,
                            "message": "order_send returned None | last_error=-2:Unnamed arguments not allowed",
                            "terminal": False,
                            "broker_ready": True,
                            "terminal_ready": True,
                            "diagnostics": {
                                "request": {"symbol": "GBPUSD"},
                                "response": {},
                                "last_error": {"code": -2, "message": "Unnamed arguments not allowed"},
                            },
                        },
                        "disposition": {},
                    },
                    registry_state=None,
                )
                runner._emit_execution_telemetry(result, datetime(2026, 5, 29, 9, 10, 0, tzinfo=timezone.utc))

                telemetry_db = sqlite3.connect(config.persistence.sqlite_path)
                telemetry_db.row_factory = sqlite3.Row
                rows = telemetry_db.execute(
                    """
                    SELECT payload_json
                    FROM telemetry_index
                    WHERE topic = 'execution_rejected'
                    ORDER BY rowid DESC
                    LIMIT 1
                    """
                ).fetchall()
                telemetry_db.close()
                self.assertTrue(rows)
                payload = json.loads(rows[0]["payload_json"])
                self.assertEqual(payload["request"]["symbol"], "GBPUSD")
                self.assertEqual(payload["response"], {})
                self.assertEqual(payload["bridge_diagnostics"]["last_error"]["code"], -2)
                self.assertEqual(payload["last_error"]["message"], "Unnamed arguments not allowed")
            finally:
                store.close()

    def test_start_emits_market_data_readiness_on_bridge_rates_failure(self) -> None:
        class FailingRatesBridge(FakeLiveBridge):
            def get_rates(self, symbol: str, timeframe: str, count: int) -> tuple[dict[str, object], ...]:
                if timeframe.upper() == "M1":
                    raise MT5BridgeError(
                        MT5BridgeStatus(
                            ok=False,
                            failure_class="CONTRACT_QUERY_FAILURE",
                            response_class="DEGRADE_SYMBOL",
                            retryable=False,
                            fatal=False,
                            message=f"Unable to fetch rates for {symbol.upper()} {timeframe.upper()}",
                            terminal_ready=True,
                            broker_ready=True,
                            diagnostics={
                                "symbol": symbol.upper(),
                                "timeframe": timeframe.upper(),
                                "count": count,
                                "raw_type": None,
                            },
                        )
                    )
                return super().get_rates(symbol, timeframe, count)

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root, allow_live_execution=True)
            fake_bridge = FailingRatesBridge(
                broker_time_value=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
            )
            runtime = DeploymentRuntime(config, bridge=fake_bridge)
            with self.assertRaises(MT5BridgeError):
                runtime.start(
                    dry_run=False,
                    probe=fake_bridge,
                    current_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                    max_cycles=1,
                )
            telemetry_db = sqlite3.connect(config.persistence.sqlite_path)
            telemetry_db.row_factory = sqlite3.Row
            rows = telemetry_db.execute(
                """
                SELECT payload_json
                FROM telemetry_index
                WHERE topic = 'deployment.market_data_readiness'
                ORDER BY rowid DESC
                LIMIT 1
                """
            ).fetchall()
            telemetry_db.close()
            self.assertTrue(rows)
            payload = json.loads(rows[0]["payload_json"])
            self.assertEqual(payload["stage"], "rates_fetch_failed")
            self.assertEqual(payload["symbol"], "XAUUSD")
            self.assertEqual(payload["timeframe"], "M1")
            self.assertEqual(payload["requested_bars"], 40)
            self.assertEqual(payload["returned_bars"], 0)
            self.assertEqual(payload["closed_bar_count"], 0)
            self.assertTrue(payload["rates_none"])

    def test_start_emits_market_data_probe_on_broker_time_failure(self) -> None:
        class BrokerTimeFailingBridge(FakeLiveBridge):
            def get_latest_tick(self, symbol: str) -> dict[str, object]:
                symbol_name = symbol.upper()
                probe = {
                    "symbol": symbol_name,
                    "symbol_info_tick_result": "success",
                    "raw_time_value": None,
                    "raw_time_msc_value": None,
                    "timestamp_valid": False,
                    "retry_count": 20,
                    "tick_time_retry_count": 20,
                    "tick_time_retry_used": True,
                    "stream_used": True,
                    "stream_tick_found": False,
                    "rates_fallback_used": True,
                    "tick_time_fallback_used": True,
                    "tick_time_fallback_source": "rates_m1",
                    "final_time_source": "rates_m1",
                    "failure_stage": "rates_fallback",
                    "failure_reason": f"Unable to fetch rates for {symbol_name} M1",
                }
                self.last_latest_tick_probe = probe
                raise MT5BridgeError(
                    MT5BridgeStatus(
                        ok=False,
                        failure_class="CONTRACT_QUERY_FAILURE",
                        response_class="DEGRADE_SYMBOL",
                        retryable=False,
                        fatal=False,
                        message=f"Unable to fetch rates for {symbol_name} M1",
                        terminal_ready=True,
                        broker_ready=True,
                        diagnostics={
                            "symbol": symbol_name,
                            "timeframe": "M1",
                            "count": 1,
                            "latest_tick_probe": probe,
                        },
                    )
                )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root, allow_live_execution=True)
            fake_bridge = BrokerTimeFailingBridge(
                broker_time_value=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
            )
            runtime = DeploymentRuntime(config, bridge=fake_bridge)
            with self.assertRaises(MT5BridgeError):
                runtime.start(
                    dry_run=False,
                    probe=fake_bridge,
                    current_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                    max_cycles=1,
                )
            telemetry_db = sqlite3.connect(config.persistence.sqlite_path)
            telemetry_db.row_factory = sqlite3.Row
            rows = telemetry_db.execute(
                """
                SELECT payload_json
                FROM telemetry_index
                WHERE topic = 'deployment.market_data_probe'
                ORDER BY rowid DESC
                LIMIT 1
                """
            ).fetchall()
            telemetry_db.close()
            self.assertTrue(rows)
            payload = json.loads(rows[0]["payload_json"])
            self.assertEqual(payload["stage"], "broker_time_probe_failed")
            self.assertEqual(payload["probe_status"], "failed")
            self.assertEqual(payload["symbol"], "XAUUSD")
            self.assertEqual(payload["final_time_source"], "rates_m1")
            self.assertEqual(payload["tick_time_fallback_source"], "rates_m1")
            self.assertTrue(payload["rates_fallback_used"])
            self.assertIn("latest_tick_probe", payload)
            self.assertEqual(payload["latest_tick_probe"]["failure_stage"], "rates_fallback")

    def test_start_blocks_on_fingerprint_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            store = SQLiteRuntimeStore(config.persistence.sqlite_path, wal_enabled=True)
            store.initialize()
            store.set_config_fingerprint("deadbeef")
            store.close()
            runtime = DeploymentRuntime(config)
            with self.assertRaises(ConfigValidationError):
                runtime.start(
                    dry_run=True,
                    current_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                )

    def test_emergency_shutdown_marks_emergency_and_releases_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            runtime = DeploymentRuntime(config)
            runtime.start(
                dry_run=True,
                current_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
            )
            shutdown = runtime.emergency_shutdown(
                reason="broker_instability",
                current_time_utc=datetime(2026, 5, 29, 9, 1, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(shutdown.emergency)
            self.assertTrue(shutdown.lock_released)
            self.assertFalse(runtime.lock.is_owned())


def _load_config(root: Path, *, allow_live_execution: bool = False):
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
        allow_live_execution="true" if allow_live_execution else "false",
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
