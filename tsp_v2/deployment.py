"""Deployment runtime for TSP V2."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol
from uuid import uuid4

from .adapters import MT5Bridge, MT5BridgeError, MT5ExecutionAdapter, MT5MarketAdapter
from .clock import evaluate_clock_state, is_execution_blocked
from .config import AppConfig
from .config_schema import ConfigValidationError
from .enums import ClockHealth, ExecutionRegistryState, RuntimeMode
from .execution import ExecutionRegistryBook
from .live_runtime import LiveRuntimeReport, LiveRuntimeRunner, MT5DeploymentProbe
from .news import build_news_snapshot
from .orchestrator import TSPV2Orchestrator
from .persistence import RecoveryEventRecord, SQLiteRuntimeStore, SCHEMA_VERSION
from .snapshots import TIMEFRAME_MINUTES, build_market_snapshot


DEPLOYMENT_VERSION = "V2"
DEFAULT_LOCK_FILENAME = "tsp_v2.lock"
DEFAULT_STALE_LOCK_SECONDS = 12 * 60 * 60
STARTUP_SYNC_BUFFER_SECONDS = 5
STARTUP_SYNC_TIMEOUT_SECONDS = 15 * 60


class DeploymentProbe(Protocol):
    def broker_ready(self) -> bool: ...

    def broker_time_utc(self) -> datetime: ...

    def supported_symbols(self) -> tuple[str, ...]: ...

    def account_snapshot(self) -> Mapping[str, Any]: ...

    def live_positions(self) -> tuple[Mapping[str, Any], ...]: ...


@dataclass(frozen=True, slots=True)
class DeploymentLockSnapshot:
    path: Path
    owner: str
    pid: int
    token: str
    created_at_utc: datetime
    updated_at_utc: datetime
    reclaimed: bool


@dataclass(frozen=True, slots=True)
class DeploymentPreflightReport:
    ok: bool
    blocked_reason: str
    diagnostics: tuple[str, ...]
    warnings: tuple[str, ...]
    mode: str
    profile: str
    config_fingerprint: str
    schema_version: str
    startup_time_utc: datetime
    lock_snapshot: DeploymentLockSnapshot | None
    writable_paths: tuple[str, ...]
    broker_ready: bool
    clock_health: ClockHealth | None
    news_state: str
    supported_symbols: tuple[str, ...]
    dry_run: bool


@dataclass(frozen=True, slots=True)
class DeploymentMetadata:
    version: str
    schema_version: str
    config_fingerprint: str
    startup_time_utc: datetime
    mode: str
    profile: str
    dry_run: bool


@dataclass(frozen=True, slots=True)
class DeploymentStartReport:
    preflight: DeploymentPreflightReport
    bootstrap_report: Any
    metadata: DeploymentMetadata
    live_report: LiveRuntimeReport | None = None


@dataclass(frozen=True, slots=True)
class DeploymentShutdownReport:
    shutdown_time_utc: datetime
    reason: str
    emergency: bool
    lock_released: bool
    store_closed: bool


@dataclass(slots=True)
class SingleInstanceLock:
    path: Path
    stale_after_seconds: int = DEFAULT_STALE_LOCK_SECONDS
    snapshot: DeploymentLockSnapshot | None = None

    def acquire(self, *, owner: str, now_utc: datetime | None = None) -> DeploymentLockSnapshot:
        current_time = _ensure_utc(now_utc or _now_utc(), field_name="now_utc")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        reclaimed = False
        if self.path.exists():
            existing = self._load_snapshot()
            if existing is not None and not self._can_reclaim(existing, current_time):
                raise ConfigValidationError(
                    f"Runtime lock already owned by pid={existing.pid} token={existing.token}"
                )
            reclaimed = True
            self._remove_file()
        snapshot = DeploymentLockSnapshot(
            path=self.path,
            owner=owner,
            pid=os.getpid(),
            token=uuid4().hex,
            created_at_utc=current_time,
            updated_at_utc=current_time,
            reclaimed=reclaimed,
        )
        self._write_snapshot(snapshot)
        self.snapshot = snapshot
        return snapshot

    def refresh(self, *, now_utc: datetime | None = None) -> DeploymentLockSnapshot:
        if self.snapshot is None:
            raise ConfigValidationError("Runtime lock is not owned")
        current_time = _ensure_utc(now_utc or _now_utc(), field_name="now_utc")
        refreshed = DeploymentLockSnapshot(
            path=self.snapshot.path,
            owner=self.snapshot.owner,
            pid=self.snapshot.pid,
            token=self.snapshot.token,
            created_at_utc=self.snapshot.created_at_utc,
            updated_at_utc=current_time,
            reclaimed=self.snapshot.reclaimed,
        )
        self._write_snapshot(refreshed)
        self.snapshot = refreshed
        return refreshed

    def release(self) -> None:
        if self.snapshot is None:
            return
        on_disk = self._load_snapshot()
        if on_disk is not None and on_disk.token == self.snapshot.token:
            self._remove_file()
        self.snapshot = None

    def is_owned(self) -> bool:
        return self.snapshot is not None and self.path.exists()

    def _can_reclaim(self, existing: DeploymentLockSnapshot, now_utc: datetime) -> bool:
        del now_utc
        return not _process_is_alive(existing.pid)

    def _load_snapshot(self) -> DeploymentLockSnapshot | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        try:
            return DeploymentLockSnapshot(
                path=self.path,
                owner=str(payload["owner"]),
                pid=int(payload["pid"]),
                token=str(payload["token"]),
                created_at_utc=_parse_utc(str(payload["created_at_utc"])),
                updated_at_utc=_parse_utc(str(payload["updated_at_utc"])),
                reclaimed=bool(payload.get("reclaimed", False)),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _write_snapshot(self, snapshot: DeploymentLockSnapshot) -> None:
        payload = {
            "owner": snapshot.owner,
            "pid": snapshot.pid,
            "token": snapshot.token,
            "created_at_utc": snapshot.created_at_utc.isoformat(),
            "updated_at_utc": snapshot.updated_at_utc.isoformat(),
            "reclaimed": snapshot.reclaimed,
        }
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)

    def _remove_file(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


@dataclass(slots=True)
class DeploymentRuntime:
    config: AppConfig
    store: SQLiteRuntimeStore | None = None
    orchestrator: TSPV2Orchestrator | None = None
    bridge: MT5Bridge | None = None
    market_adapter: MT5MarketAdapter | None = None
    execution_adapter: MT5ExecutionAdapter | None = None
    lock: SingleInstanceLock | None = None
    preflight_report: DeploymentPreflightReport | None = None
    start_report: DeploymentStartReport | None = None
    live_report: LiveRuntimeReport | None = None
    metadata: DeploymentMetadata | None = None
    startup_time_utc: datetime | None = None

    def __post_init__(self) -> None:
        lock_file = self.config.persistence.lock_path / DEFAULT_LOCK_FILENAME
        self.lock = self.lock or SingleInstanceLock(lock_file)

    def preflight(
        self,
        *,
        dry_run: bool = False,
        probe: DeploymentProbe | None = None,
        current_time_utc: datetime | None = None,
        retain_lock: bool = False,
    ) -> DeploymentPreflightReport:
        startup_time_utc = _ensure_utc(current_time_utc or _now_utc(), field_name="current_time_utc")
        store = self._ensure_store()
        lock_snapshot: DeploymentLockSnapshot | None = None
        diagnostics: list[str] = []
        warnings: list[str] = []
        writable_paths = self._validate_writable_paths()
        broker_ready = False
        clock_health: ClockHealth | None = None
        clock_state = None
        clock_source_audit: dict[str, Any] | None = None
        supported_symbols: tuple[str, ...] = ()
        news_state = "UNKNOWN"
        blocked_reason = ""
        try:
            lock_snapshot = self._acquire_lock(owner=self._lock_owner_label(), now_utc=startup_time_utc)
            self._validate_schema_and_fingerprint(store=store)
            news_snapshot = build_news_snapshot(
                cycle_time_utc=startup_time_utc,
                config=self.config,
                symbol=self._primary_symbol(),
            )
            news_state = news_snapshot.provider_state.value
            if news_snapshot.provider_state.value == "UNAVAILABLE":
                blocked_reason = "news_provider_unavailable"
            elif self.config.bot.mode in {RuntimeMode.FORWARD_TEST, RuntimeMode.CONTEST} and news_snapshot.provider_state.value == "STALE":
                warnings.append("news_provider_stale")

            if not dry_run and blocked_reason == "":
                if not self.config.deployment.allow_live_execution:
                    blocked_reason = "live_execution_disabled"
                elif probe is None:
                    blocked_reason = "broker_probe_missing"
                else:
                    broker_ready = bool(probe.broker_ready())
                    if not broker_ready:
                        blocked_reason = "broker_unavailable"
                    else:
                        supported_symbols = tuple(sorted(symbol.upper() for symbol in probe.supported_symbols()))
                        missing_symbols = tuple(
                            symbol
                            for symbol in self.config.symbols.allowlist
                            if symbol.upper() not in set(supported_symbols)
                        )
                        if missing_symbols:
                            blocked_reason = f"unsupported_symbols:{','.join(missing_symbols)}"
                        else:
                            try:
                                if hasattr(probe, "clock_source_audit"):
                                    clock_source_audit = dict(probe.clock_source_audit())  # type: ignore[assignment]
                                    broker_time_utc = _ensure_utc(
                                        clock_source_audit.get("broker_time_utc", clock_source_audit["normalized_timestamp"]),
                                        field_name="clock_source_audit.broker_time_utc",
                                    )
                                else:
                                    broker_time_utc = probe.broker_time_utc()
                                    clock_source_audit = {
                                        "symbol": self._primary_symbol(),
                                        "broker_time_utc": broker_time_utc.isoformat(),
                                    }
                            except MT5BridgeError as exc:
                                if exc.status.failure_class in {"SYMBOL_UNAVAILABLE", "CONTRACT_QUERY_FAILURE"}:
                                    warnings.append("clock_source_audit_unavailable")
                                    probe.account_snapshot()
                                    probe.live_positions()
                                    broker_ready = True
                                else:
                                    raise
                            else:
                                clock_state = evaluate_clock_state(
                                    broker_time=broker_time_utc,
                                    local_time_utc=startup_time_utc,
                                )
                                clock_health = clock_state.health
                                diagnostics.extend(
                                    [
                                        f"clock_health={clock_state.health.value}",
                                        f"broker_time_utc={clock_state.broker_time_utc.isoformat()}",
                                        f"local_time_utc={clock_state.local_time_utc.isoformat()}",
                                        f"skew_seconds={clock_state.skew_seconds:.6f}",
                                    ]
                                )
                                if clock_state.diagnostic_flags:
                                    diagnostics.append(
                                        "clock_flags=" + ",".join(clock_state.diagnostic_flags)
                                    )
                                if is_execution_blocked(clock_state):
                                    blocked_reason = f"clock_{clock_state.health.value.lower()}"
                                else:
                                    probe.account_snapshot()
                                    probe.live_positions()
                                    broker_ready = True
            elif dry_run:
                warnings.append("live_checks_skipped")
                broker_ready = True

            ok = blocked_reason == ""
            report = DeploymentPreflightReport(
                ok=ok,
                blocked_reason=blocked_reason,
                diagnostics=tuple(diagnostics),
                warnings=tuple(warnings),
                mode=self.config.bot.mode.value,
                profile=self.config.bot.profile.value,
                config_fingerprint=self.config.fingerprint,
                schema_version=SCHEMA_VERSION,
                startup_time_utc=startup_time_utc,
                lock_snapshot=lock_snapshot,
                writable_paths=tuple(writable_paths),
                broker_ready=broker_ready,
                clock_health=clock_health,
                news_state=news_state,
                supported_symbols=supported_symbols,
                dry_run=dry_run,
            )
            store.store_telemetry_index(
                "deployment.preflight",
                {
                    "ok": report.ok,
                    "blocked_reason": report.blocked_reason,
                    "mode": report.mode,
                    "profile": report.profile,
                    "config_fingerprint": report.config_fingerprint,
                    "schema_version": report.schema_version,
                    "dry_run": report.dry_run,
                    "broker_ready": report.broker_ready,
                    "clock_health": report.clock_health.value if report.clock_health is not None else None,
                    "news_state": report.news_state,
                    "warnings": list(report.warnings),
                    "diagnostics": list(report.diagnostics),
                },
            )
            if clock_state is not None:
                store.store_telemetry_index(
                    "deployment.clock_diagnostics",
                    {
                        "clock_health": clock_state.health.value,
                        "broker_time_utc": clock_state.broker_time_utc.isoformat(),
                        "local_time_utc": clock_state.local_time_utc.isoformat(),
                        "skew_seconds": round(clock_state.skew_seconds, 6),
                        "backward_jump_seconds": round(clock_state.backward_jump_seconds, 6),
                        "diagnostic_flags": list(clock_state.diagnostic_flags),
                        "blocked_reason": report.blocked_reason,
                    },
                )
            if clock_source_audit is not None:
                store.store_telemetry_index(
                    "deployment.clock_source_audit",
                    {
                        **clock_source_audit,
                        "local_time_utc": startup_time_utc.isoformat(),
                        "skew_seconds": round(clock_state.skew_seconds if clock_state is not None else 0.0, 6),
                        "clock_health": clock_state.health.value if clock_state is not None else None,
                        "blocked_reason": report.blocked_reason,
                    },
                )
            self.preflight_report = report
            return report
        finally:
            if not retain_lock:
                self._release_lock()
                store.close()

    def start(
        self,
        *,
        dry_run: bool = False,
        probe: DeploymentProbe | None = None,
        current_time_utc: datetime | None = None,
        max_cycles: int | None = 1,
    ) -> DeploymentStartReport:
        probe_to_close: MT5DeploymentProbe | None = None
        live_report: LiveRuntimeReport | None = None
        bootstrap_report: Any | None = None
        metadata: DeploymentMetadata | None = None
        if not dry_run and probe is None:
            probe_to_close = self._build_live_probe()
            probe = probe_to_close
        try:
            preflight = self.preflight(
                dry_run=dry_run,
                probe=probe,
                current_time_utc=current_time_utc,
                retain_lock=True,
            )
        except Exception:
            if probe_to_close is not None:
                probe_to_close.close()
            self.shutdown(reason="startup_failure", emergency=True, current_time_utc=current_time_utc)
            raise
        if not preflight.ok:
            if probe_to_close is not None:
                probe_to_close.close()
            self.shutdown(reason=preflight.blocked_reason or "preflight_blocked", emergency=True)
            raise ConfigValidationError(f"Deployment preflight blocked: {preflight.blocked_reason}")

        store = self._ensure_store()
        startup_time_utc = preflight.startup_time_utc
        try:
            orchestrator = self._ensure_orchestrator()
            if dry_run:
                metadata = DeploymentMetadata(
                    version=DEPLOYMENT_VERSION,
                    schema_version=SCHEMA_VERSION,
                    config_fingerprint=self.config.fingerprint,
                    startup_time_utc=startup_time_utc,
                    mode=self.config.bot.mode.value,
                    profile=self.config.bot.profile.value,
                    dry_run=dry_run,
                )
                self._persist_startup_metadata(metadata=metadata)
                bootstrap_report = orchestrator.bootstrap(
                    broker_positions=(),
                    current_time_utc=startup_time_utc,
                    lock_owned=True,
                )
            else:
                if probe_to_close is not None:
                    bridge = probe_to_close.bridge
                else:
                    bridge = self._ensure_bridge()
                if not bool(getattr(bridge, "connected", False)):
                    connect_status = bridge.connect()
                    if not connect_status.ok:
                        raise ConfigValidationError(f"MT5 live connection failed: {connect_status.message}")
                market_adapter = self._ensure_market_adapter(bridge)
                execution_adapter = self._ensure_execution_adapter(bridge, store)
                startup_time_utc = self._wait_for_startup_snapshot_readiness(
                    store=store,
                    market_adapter=market_adapter,
                    current_time_utc=startup_time_utc,
                )
                metadata = DeploymentMetadata(
                    version=DEPLOYMENT_VERSION,
                    schema_version=SCHEMA_VERSION,
                    config_fingerprint=self.config.fingerprint,
                    startup_time_utc=startup_time_utc,
                    mode=self.config.bot.mode.value,
                    profile=self.config.bot.profile.value,
                    dry_run=dry_run,
                )
                self._persist_startup_metadata(metadata=metadata)
                bootstrap_report = orchestrator.bootstrap(
                    broker_positions=(),
                    broker_truth_provider=bridge,
                    current_time_utc=startup_time_utc,
                    lock_owned=True,
                )
                self._sync_execution_registry_from_store(store)
            store.store_recovery_event(
                RecoveryEventRecord(
                    event_time_utc=startup_time_utc,
                    stage="deployment_start",
                    outcome="ready",
                    payload_json=_json_dumps(
                        {
                            "version": metadata.version,
                            "schema_version": metadata.schema_version,
                            "config_fingerprint": metadata.config_fingerprint,
                            "mode": metadata.mode,
                            "profile": metadata.profile,
                            "dry_run": metadata.dry_run,
                        }
                    ),
                )
            )
            if not dry_run and not bootstrap_report.ready_to_resume:
                raise ConfigValidationError("Broker reconciliation is not ready to resume live runtime")
            if not dry_run:
                runner = LiveRuntimeRunner(
                    config=self.config,
                    store=store,
                    bridge=bridge,
                    market_adapter=market_adapter,
                    execution_adapter=execution_adapter,
                    bootstrap_report=bootstrap_report,
                )
                live_report = runner.run(max_cycles=max_cycles, current_time_utc=startup_time_utc)
                self.live_report = live_report
            if probe_to_close is not None:
                probe_to_close.close()
        except Exception:
            if probe_to_close is not None:
                probe_to_close.close()
            self.shutdown(reason="startup_failure", emergency=True, current_time_utc=startup_time_utc)
            raise
        assert metadata is not None
        assert bootstrap_report is not None
        start_report = DeploymentStartReport(
            preflight=preflight,
            bootstrap_report=bootstrap_report,
            metadata=metadata,
            live_report=live_report,
        )
        store.store_telemetry_index(
            "deployment.startup",
            {
                "version": metadata.version,
                "schema_version": metadata.schema_version,
                "config_fingerprint": metadata.config_fingerprint,
                "startup_time_utc": metadata.startup_time_utc.isoformat(),
                "mode": metadata.mode,
                "profile": metadata.profile,
                "dry_run": metadata.dry_run,
                "ready_to_resume": bootstrap_report.ready_to_resume,
                "live_report_cycles": start_report.live_report.cycles_completed if start_report.live_report is not None else 0,
                "live_report_reason": start_report.live_report.stopped_reason if start_report.live_report is not None else "dry_run",
            },
        )
        self.metadata = metadata
        self.startup_time_utc = startup_time_utc
        self.start_report = start_report
        return start_report

    def shutdown(
        self,
        *,
        reason: str = "normal_shutdown",
        emergency: bool = False,
        current_time_utc: datetime | None = None,
    ) -> DeploymentShutdownReport:
        shutdown_time_utc = _ensure_utc(current_time_utc or _now_utc(), field_name="current_time_utc")
        store = self.store
        lock_released = False
        store_closed = False
        if store is not None and getattr(store, "_connection", None) is not None:
            store.store_runtime_state(
                {
                    "deployment.shutdown_reason": reason,
                    "deployment.emergency": emergency,
                    "deployment.shutdown_time_utc": shutdown_time_utc.isoformat(),
                }
            )
            store.store_recovery_event(
                RecoveryEventRecord(
                    event_time_utc=shutdown_time_utc,
                    stage="deployment_shutdown",
                    outcome="emergency" if emergency else "graceful",
                    payload_json=_json_dumps(
                        {
                            "reason": reason,
                            "emergency": emergency,
                            "version": DEPLOYMENT_VERSION,
                            "schema_version": SCHEMA_VERSION,
                        }
                    ),
                )
            )
            store.store_telemetry_index(
                "deployment.shutdown",
                {
                    "reason": reason,
                    "emergency": emergency,
                    "shutdown_time_utc": shutdown_time_utc.isoformat(),
                },
            )
            store.close()
            store_closed = True
        if self.bridge is not None and getattr(self.bridge, "connected", False):
            try:
                self.bridge.disconnect()
            except Exception:
                pass
        if self.lock is not None and self.lock.is_owned():
            self.lock.release()
            lock_released = True
        return DeploymentShutdownReport(
            shutdown_time_utc=shutdown_time_utc,
            reason=reason,
            emergency=emergency,
            lock_released=lock_released,
            store_closed=store_closed,
        )

    def emergency_shutdown(
        self,
        *,
        reason: str,
        current_time_utc: datetime | None = None,
    ) -> DeploymentShutdownReport:
        return self.shutdown(reason=reason, emergency=True, current_time_utc=current_time_utc)

    def _ensure_store(self) -> SQLiteRuntimeStore:
        if self.store is None:
            self.store = SQLiteRuntimeStore(
                self.config.persistence.sqlite_path,
                wal_enabled=self.config.persistence.wal_enabled,
            )
            self.store.initialize()
        return self.store

    def _ensure_orchestrator(self) -> TSPV2Orchestrator:
        if self.orchestrator is None:
            self.orchestrator = TSPV2Orchestrator(self.config, store=self.store)
        return self.orchestrator

    def _ensure_bridge(self) -> MT5Bridge:
        if self.bridge is None:
            self.bridge = MT5Bridge(
                terminal_path=self.config.secrets.mt5_terminal_path,
                login=self.config.secrets.mt5_login,
                password=self.config.secrets.mt5_password,
                server=self.config.secrets.mt5_server,
            )
        return self.bridge

    def _ensure_market_adapter(self, bridge: MT5Bridge) -> MT5MarketAdapter:
        if self.market_adapter is None:
            self.market_adapter = MT5MarketAdapter(
                bridge=bridge,
                primary_symbol=self._primary_symbol(),
            )
        return self.market_adapter

    def _ensure_execution_adapter(self, bridge: MT5Bridge, store: SQLiteRuntimeStore) -> MT5ExecutionAdapter:
        if self.execution_adapter is None:
            registry = ExecutionRegistryBook()
            self._populate_registry_from_store(registry, store)
            self.execution_adapter = MT5ExecutionAdapter(bridge=bridge, registry=registry)
        return self.execution_adapter

    def _build_live_probe(self) -> MT5DeploymentProbe:
        return MT5DeploymentProbe(
            bridge=MT5Bridge(
                terminal_path=self.config.secrets.mt5_terminal_path,
                login=self.config.secrets.mt5_login,
                password=self.config.secrets.mt5_password,
                server=self.config.secrets.mt5_server,
            ),
            primary_symbol=self._primary_symbol(),
            symbols=self.config.symbols.allowlist,
        )

    def _wait_for_startup_snapshot_readiness(
        self,
        *,
        store: SQLiteRuntimeStore,
        market_adapter: MT5MarketAdapter,
        current_time_utc: datetime,
    ) -> datetime:
        del current_time_utc
        deadline = _now_utc() + timedelta(seconds=STARTUP_SYNC_TIMEOUT_SECONDS)
        primary_symbol = self._primary_symbol()
        last_diagnostics: dict[str, Any] | None = None
        while True:
            broker_time: datetime | None = None
            try:
                broker_time = _ensure_utc(market_adapter.get_broker_time(), field_name="broker_time_utc")
            except MT5BridgeError as exc:
                self._emit_market_data_probe_telemetry(
                    store,
                    market_adapter=market_adapter,
                    stage="broker_time_probe_failed",
                    error=exc,
                )
                raise

            def diagnostics_hook(payload: dict[str, Any]) -> None:
                nonlocal last_diagnostics
                last_diagnostics = dict(payload)
                store.store_telemetry_index("deployment.market_data_readiness", payload)

            try:
                build_market_snapshot(
                    market_adapter,
                    config=self.config,
                    symbol=primary_symbol,
                    cycle_time_utc=broker_time,
                    previous_cycle_time_utc=None,
                    diagnostics_hook=diagnostics_hook,
                )
            except ConfigValidationError as exc:
                stage = str(last_diagnostics.get("stage")) if last_diagnostics is not None else ""
                if stage == "closed_bars_insufficient" and last_diagnostics is not None:
                    retry_after_seconds = self._startup_sync_retry_after_seconds(
                        broker_time=broker_time,
                        timeframe=str(last_diagnostics.get("timeframe", "M5")),
                    )
                    self._emit_telemetry(
                        store,
                        "deployment.startup_sync",
                        {
                            "stage": "waiting_for_snapshot_readiness",
                            "broker_time": broker_time.isoformat(),
                            "snapshot_ready": False,
                            "timeframe": last_diagnostics.get("timeframe"),
                            "closed_bar_count": last_diagnostics.get("closed_bar_count"),
                            "minimum_closed_bar_count": last_diagnostics.get("minimum_closed_bar_count"),
                            "next_retry_after_seconds": retry_after_seconds,
                        },
                    )
                    if _now_utc() >= deadline:
                        self._emit_telemetry(
                            store,
                            "deployment.startup_sync",
                            {
                                "stage": "startup_sync_timeout",
                                "broker_time": broker_time.isoformat(),
                                "snapshot_ready": False,
                                "timeframe": last_diagnostics.get("timeframe"),
                                "closed_bar_count": last_diagnostics.get("closed_bar_count"),
                                "minimum_closed_bar_count": last_diagnostics.get("minimum_closed_bar_count"),
                                "next_retry_after_seconds": retry_after_seconds,
                            },
                        )
                        raise ConfigValidationError("Startup synchronization timed out") from exc
                    time.sleep(max(0.0, float(retry_after_seconds)))
                    continue
                if stage in {"rates_fetch_failed", "payload_rejected"}:
                    self._emit_telemetry(
                        store,
                        "deployment.startup_sync",
                        {
                            "stage": "startup_sync_failed",
                            "broker_time": broker_time.isoformat(),
                            "snapshot_ready": False,
                            "timeframe": last_diagnostics.get("timeframe") if last_diagnostics is not None else None,
                            "closed_bar_count": last_diagnostics.get("closed_bar_count") if last_diagnostics is not None else None,
                            "minimum_closed_bar_count": last_diagnostics.get("minimum_closed_bar_count") if last_diagnostics is not None else None,
                            "next_retry_after_seconds": 0,
                        },
                    )
                    raise
                raise
            except MT5BridgeError as exc:
                self._emit_market_data_probe_telemetry(
                    store,
                    market_adapter=market_adapter,
                    stage="snapshot_probe_failed",
                    broker_time=broker_time,
                    error=exc,
                )
                raise
            self._emit_telemetry(
                store,
                "deployment.startup_sync",
                {
                    "stage": "snapshot_ready",
                    "broker_time": broker_time.isoformat(),
                    "snapshot_ready": True,
                    "timeframe": last_diagnostics.get("timeframe") if last_diagnostics is not None else None,
                    "closed_bar_count": last_diagnostics.get("closed_bar_count") if last_diagnostics is not None else None,
                    "minimum_closed_bar_count": last_diagnostics.get("minimum_closed_bar_count") if last_diagnostics is not None else None,
                    "next_retry_after_seconds": 0,
                },
            )
            return broker_time

    def _startup_sync_retry_after_seconds(self, *, broker_time: datetime, timeframe: str) -> int:
        timeframe_name = timeframe.upper()
        minutes = TIMEFRAME_MINUTES.get(timeframe_name)
        if minutes is None:
            return max(STARTUP_SYNC_BUFFER_SECONDS, int(self.config.bot.poll_interval_seconds))
        normalized = broker_time.replace(second=0, microsecond=0)
        boundary_minute = (normalized.minute // minutes) * minutes
        boundary = normalized.replace(minute=boundary_minute)
        next_close = boundary + timedelta(minutes=minutes)
        target = next_close + timedelta(seconds=STARTUP_SYNC_BUFFER_SECONDS)
        seconds = int((target - broker_time).total_seconds())
        return max(STARTUP_SYNC_BUFFER_SECONDS, seconds)

    def _emit_telemetry(self, store: SQLiteRuntimeStore, topic: str, payload: Mapping[str, Any]) -> None:
        store.store_telemetry_index(topic, payload)

    def _emit_market_data_probe_telemetry(
        self,
        store: SQLiteRuntimeStore,
        *,
        market_adapter: MT5MarketAdapter,
        stage: str,
        broker_time: datetime | None = None,
        error: MT5BridgeError | None = None,
    ) -> None:
        bridge = market_adapter.bridge
        probe = dict(getattr(bridge, "last_latest_tick_probe", {}) or {})
        bridge_error_summary: dict[str, Any] | None = None
        if error is not None:
            diagnostics = error.status.diagnostics
            bridge_error_summary = {
                key: diagnostics[key]
                for key in (
                    "symbol",
                    "timeframe",
                    "count",
                    "recovery_connect_path",
                    "market_data_probe_attempted",
                    "market_data_usable",
                    "recovery_fully_usable",
                    "pre_recovery_last_error",
                    "post_recovery_last_error",
                )
                if key in diagnostics
            }
        payload: dict[str, Any] = {
            "stage": stage,
            "probe_status": "failed" if error is not None else "success",
            "symbol": probe.get("symbol", self._primary_symbol()),
            "broker_time_utc": broker_time.isoformat() if broker_time is not None else probe.get("broker_time_utc"),
            "symbol_info_tick_result": probe.get("symbol_info_tick_result"),
            "raw_time_value": probe.get("raw_time_value"),
            "raw_time_msc_value": probe.get("raw_time_msc_value"),
            "timestamp_valid": probe.get("timestamp_valid"),
            "retry_count": probe.get("retry_count"),
            "tick_time_retry_count": probe.get("tick_time_retry_count"),
            "tick_time_retry_used": probe.get("tick_time_retry_used"),
            "stream_used": probe.get("stream_used"),
            "stream_tick_found": probe.get("stream_tick_found"),
            "rates_fallback_used": probe.get("rates_fallback_used"),
            "tick_time_fallback_used": probe.get("tick_time_fallback_used"),
            "tick_time_fallback_source": probe.get("tick_time_fallback_source"),
            "final_time_source": probe.get("final_time_source"),
            "failure_stage": probe.get("failure_stage"),
            "failure_reason": probe.get("failure_reason"),
            "bridge_error_message": str(error) if error is not None else None,
            "latest_tick_probe": probe or None,
        }
        if error is not None:
            payload["bridge_error_failure_class"] = error.status.failure_class
            payload["bridge_error_response_class"] = error.status.response_class
            payload["bridge_error_retryable"] = error.status.retryable
            payload["bridge_error_fatal"] = error.status.fatal
            if bridge_error_summary:
                payload["bridge_error_summary"] = bridge_error_summary
        clean_payload = {key: value for key, value in payload.items() if value is not None}
        self._emit_telemetry(store, "deployment.market_data_probe", clean_payload)

    def _sync_execution_registry_from_store(self, store: SQLiteRuntimeStore) -> None:
        if self.execution_adapter is None or self.execution_adapter.registry is None:
            return
        registry = ExecutionRegistryBook()
        self._populate_registry_from_store(registry, store)
        self.execution_adapter.registry = registry

    def _populate_registry_from_store(self, registry: ExecutionRegistryBook, store: SQLiteRuntimeStore) -> None:
        for entry in store.load_execution_registry():
            registry.entries_by_setup_id[entry.setup_id] = entry
            registry.entries_by_submission_uuid[entry.submission_uuid] = entry
            if entry.expires_at_utc is not None and entry.state not in {
                ExecutionRegistryState.EXPIRED,
                ExecutionRegistryState.CANCELLED,
            }:
                registry.symbol_locks_until_utc[entry.symbol.upper()] = entry.expires_at_utc

    def _acquire_lock(self, *, owner: str, now_utc: datetime) -> DeploymentLockSnapshot:
        assert self.lock is not None
        return self.lock.acquire(owner=owner, now_utc=now_utc)

    def _release_lock(self) -> None:
        if self.lock is not None:
            self.lock.release()

    def _persist_startup_metadata(self, *, metadata: DeploymentMetadata) -> None:
        store = self._ensure_store()
        store.set_meta("deployment.version", metadata.version)
        store.set_meta("deployment.schema_version", metadata.schema_version)
        store.set_meta("deployment.config_fingerprint", metadata.config_fingerprint)
        store.set_meta("deployment.startup_time_utc", metadata.startup_time_utc.isoformat())
        store.set_meta("deployment.mode", metadata.mode)
        store.set_meta("deployment.profile", metadata.profile)
        store.set_meta("deployment.dry_run", "1" if metadata.dry_run else "0")
        store.store_runtime_state(
            {
                "deployment.version": metadata.version,
                "deployment.schema_version": metadata.schema_version,
                "deployment.config_fingerprint": metadata.config_fingerprint,
                "deployment.startup_time_utc": metadata.startup_time_utc.isoformat(),
                "deployment.mode": metadata.mode,
                "deployment.profile": metadata.profile,
                "deployment.dry_run": metadata.dry_run,
            }
        )

    def _validate_schema_and_fingerprint(self, *, store: SQLiteRuntimeStore) -> None:
        current_schema = store.get_schema_version()
        if current_schema is not None and current_schema != SCHEMA_VERSION:
            raise ConfigValidationError(
                f"Schema version mismatch: expected {SCHEMA_VERSION}, found {current_schema}"
            )
        current_fingerprint = store.get_config_fingerprint()
        if current_fingerprint is not None and current_fingerprint != self.config.fingerprint:
            raise ConfigValidationError(
                f"Config fingerprint mismatch: expected {self.config.fingerprint}, found {current_fingerprint}"
            )
        store.set_config_fingerprint(self.config.fingerprint)

    def _validate_writable_paths(self) -> tuple[str, ...]:
        paths = (
            self.config.deployment.runtime_root,
            self.config.deployment.log_root,
            self.config.deployment.report_root,
            self.config.persistence.lock_path,
            self.config.persistence.sqlite_path.parent,
        )
        validated: list[str] = []
        for path in paths:
            _assert_writable_path(path)
            validated.append(str(path))
        return tuple(validated)

    def _primary_symbol(self) -> str:
        if not self.config.symbols.allowlist:
            raise ConfigValidationError("symbols.allowlist must not be empty")
        return self.config.symbols.allowlist[0]

    def _lock_owner_label(self) -> str:
        return f"{DEPLOYMENT_VERSION}:{self.config.bot.mode.value}:{self.config.bot.profile.value}"


def run_preflight(
    config: AppConfig,
    *,
    dry_run: bool = False,
    probe: DeploymentProbe | None = None,
    current_time_utc: datetime | None = None,
) -> DeploymentPreflightReport:
    runtime = DeploymentRuntime(config)
    return runtime.preflight(
        dry_run=dry_run,
        probe=probe,
        current_time_utc=current_time_utc,
        retain_lock=False,
    )


def run_startup(
    config: AppConfig,
    *,
    dry_run: bool = False,
    probe: DeploymentProbe | None = None,
    current_time_utc: datetime | None = None,
) -> DeploymentStartReport:
    runtime = DeploymentRuntime(config)
    return runtime.start(
        dry_run=dry_run,
        probe=probe,
        current_time_utc=current_time_utc,
    )


def run_shutdown(
    config: AppConfig,
    *,
    reason: str = "normal_shutdown",
    emergency: bool = False,
    current_time_utc: datetime | None = None,
) -> DeploymentShutdownReport:
    runtime = DeploymentRuntime(config)
    return runtime.shutdown(
        reason=reason,
        emergency=emergency,
        current_time_utc=current_time_utc,
    )


def _assert_writable_path(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe_path = path / ".tsp_v2_write_probe"
    try:
        probe_path.write_text("ok", encoding="utf-8")
    except OSError as exc:
        raise ConfigValidationError(f"Path is not writable: {path}") from exc
    finally:
        try:
            probe_path.unlink()
        except FileNotFoundError:
            pass


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _ensure_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ConfigValidationError(f"{field_name} must be timezone-aware UTC datetime")
    return value.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_utc(raw: str) -> datetime:
    return datetime.fromisoformat(raw).astimezone(timezone.utc)


def _json_dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
