"""Live runtime activation for TSP V2."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any, Mapping

from .adapters import MT5Bridge, MT5BridgeError, MT5ExecutionAdapter, MT5MarketAdapter
from .config import AppConfig
from .config_schema import ConfigValidationError
from .enums import Direction, GovernorState, HealthState, PaceClassification, RiskAction
from .execution import ExecutionRegistryBook, validate_execution_intent
from .governor import evaluate_governor
from .models import (
    ExecutionResult,
    GovernorContext,
    GovernorDecision,
    MarketSnapshot,
    PortfolioContext,
    PositionSnapshot,
    RiskContext,
    RiskDecision,
    RuntimeState,
    SignalDecision,
)
from .persistence import RecoveryEventRecord, SQLiteRuntimeStore
from .portfolio import rank_opportunities
from .recovery import BrokerReconciliationReport, BrokerReconciliationRuntime
from .regime import classify_regime
from .risk import evaluate_risk
from .signals import SignalEvaluation, evaluate_signals
from .snapshots import build_market_snapshot


@dataclass(frozen=True, slots=True)
class LiveCycleReport:
    cycle_time_utc: datetime
    broker_time_utc: datetime
    governor_state: GovernorState
    governor_reason: str
    selected_symbols: tuple[str, ...]
    signal_count: int
    execution_count: int
    execution_results: tuple[ExecutionResult, ...]
    reconciliation_ready: bool
    market_health: HealthState
    feed_health: HealthState
    pace_state: PaceClassification


@dataclass(frozen=True, slots=True)
class LiveRuntimeReport:
    started_at_utc: datetime
    stopped_at_utc: datetime
    cycles_completed: int
    max_cycles: int | None
    stopped_reason: str
    recovered: bool
    bootstrap_ready: bool
    last_cycle: LiveCycleReport | None = None


@dataclass(slots=True)
class MT5DeploymentProbe:
    bridge: MT5Bridge
    primary_symbol: str
    symbols: tuple[str, ...] = ()
    supported_symbol_cache: tuple[str, ...] = ()
    connected: bool = False

    def broker_ready(self) -> bool:
        status = self._heartbeat()
        return bool(status.ok)

    def broker_time_utc(self) -> datetime:
        audit = self.clock_source_audit()
        return audit["broker_time_utc"]

    def clock_source_audit(self) -> dict[str, Any]:
        self._ensure_connected()
        return self.bridge.get_latest_tick_audit(self.primary_symbol)

    def supported_symbols(self) -> tuple[str, ...]:
        self._ensure_connected()
        if self.supported_symbol_cache:
            return self.supported_symbol_cache
        symbols: list[str] = []
        candidates = self.symbols or (self.primary_symbol,)
        for symbol in candidates:
            try:
                contract = self.bridge.query_symbol_contract(symbol)
            except Exception:
                continue
            if bool(contract.get("visible", True)):
                symbols.append(symbol.upper())
        self.supported_symbol_cache = tuple(symbols)
        return self.supported_symbol_cache

    def account_snapshot(self) -> Mapping[str, Any]:
        self._ensure_connected()
        return self.bridge.query_account()

    def live_positions(self) -> tuple[Mapping[str, Any], ...]:
        self._ensure_connected()
        return self.bridge.query_positions()

    def close(self) -> None:
        if self.connected:
            self.bridge.disconnect()
            self.connected = False

    def _heartbeat(self):
        self._ensure_connected()
        return self.bridge.heartbeat()

    def _ensure_connected(self) -> None:
        if self.connected:
            return
        status = self.bridge.connect()
        if not status.ok:
            raise ConfigValidationError(f"MT5 probe connection failed: {status.message}")
        self.connected = True


@dataclass(slots=True)
class LiveRuntimeRunner:
    config: AppConfig
    store: SQLiteRuntimeStore
    bridge: MT5Bridge
    market_adapter: MT5MarketAdapter
    execution_adapter: MT5ExecutionAdapter
    bootstrap_report: Any
    runtime_state: RuntimeState = field(init=False)
    previous_cycle_time_utc: datetime | None = None
    last_reconciliation_report: BrokerReconciliationReport | None = None

    def __post_init__(self) -> None:
        self.runtime_state = RuntimeState(
            mode=self.config.bot.mode,
            governor_state=self.config.governor.initial_state,
            started_at_utc=_now_utc(),
        )
        self._restore_runtime_state()

    def run(
        self,
        *,
        max_cycles: int | None = 1,
        current_time_utc: datetime | None = None,
    ) -> LiveRuntimeReport:
        started_at_utc = _ensure_utc(current_time_utc or _now_utc(), field_name="current_time_utc")
        self.runtime_state.started_at_utc = started_at_utc
        bootstrap_ready = bool(getattr(self.bootstrap_report, "ready_to_resume", True))
        self._emit_telemetry(
            "runtime_started",
            {
                "event_time_utc": started_at_utc.isoformat(),
                "mode": self.config.bot.mode.value,
                "profile": self.config.bot.profile.value,
                "bootstrap_ready": bootstrap_ready,
                "max_cycles": max_cycles,
            },
        )
        self._emit_recovered(started_at_utc, bootstrap_ready=bootstrap_ready)

        cycles_completed = 0
        last_cycle: LiveCycleReport | None = None
        stopped_reason = "completed"
        try:
            if not bootstrap_ready:
                raise ConfigValidationError("Bootstrap reconciliation is not ready to resume live runtime")
            while max_cycles is None or cycles_completed < max_cycles:
                cycle_report = self._run_cycle()
                if cycle_report is None:
                    if max_cycles is None or cycles_completed < max_cycles:
                        time.sleep(max(0.0, float(self.config.bot.poll_interval_seconds)))
                    continue
                last_cycle = cycle_report
                cycles_completed += 1
                self._emit_telemetry(
                    "runtime_cycle",
                    {
                        "event_time_utc": cycle_report.cycle_time_utc.isoformat(),
                        "cycles_completed": cycles_completed,
                        "governor_state": cycle_report.governor_state.value,
                        "governor_reason": cycle_report.governor_reason,
                        "selected_symbols": list(cycle_report.selected_symbols),
                        "signal_count": cycle_report.signal_count,
                        "execution_count": cycle_report.execution_count,
                        "reconciliation_ready": cycle_report.reconciliation_ready,
                        "market_health": cycle_report.market_health.value,
                        "feed_health": cycle_report.feed_health.value,
                        "pace_state": cycle_report.pace_state.value,
                    },
                )
                self._store_runtime_state(cycle_report)
                self.previous_cycle_time_utc = cycle_report.cycle_time_utc
                if max_cycles is not None and cycles_completed >= max_cycles:
                    stopped_reason = "max_cycles_reached"
                    break
                if max_cycles is None or cycles_completed < max_cycles:
                    time.sleep(max(0.0, float(self.config.bot.poll_interval_seconds)))
        except KeyboardInterrupt:
            stopped_reason = "keyboard_interrupt"
            raise
        except Exception as exc:
            stopped_reason = f"runtime_error:{exc.__class__.__name__}"
            if isinstance(exc, MT5BridgeError):
                status = exc.status
                diagnostics = dict(status.diagnostics or {})
                self._emit_telemetry(
                    "deployment.market_data_readiness",
                    {
                        "stage": "bridge_error",
                        "symbol": diagnostics.get("symbol"),
                        "timeframe": diagnostics.get("timeframe", "M1"),
                        "cycle_time_utc": _now_utc().isoformat(),
                        "requested_bars": diagnostics.get("count"),
                        "returned_bars": 0,
                        "closed_bar_count": 0,
                        "rates_none": diagnostics.get("raw_type") is None,
                        "payload_health": None,
                        "exception_class": exc.__class__.__name__,
                        "message": str(exc),
                        "exception_diagnostics": diagnostics,
                    },
                )
            self._emit_telemetry(
                "runtime_error",
                {
                    "event_time_utc": _now_utc().isoformat(),
                    "error_class": exc.__class__.__name__,
                    "message": str(exc),
                },
            )
            raise
        finally:
            stopped_at_utc = _now_utc()
            self._emit_telemetry(
                "runtime_stopped",
                {
                    "event_time_utc": stopped_at_utc.isoformat(),
                    "cycles_completed": cycles_completed,
                    "stopped_reason": stopped_reason,
                },
            )
        return LiveRuntimeReport(
            started_at_utc=started_at_utc,
            stopped_at_utc=_now_utc(),
            cycles_completed=cycles_completed,
            max_cycles=max_cycles,
            stopped_reason=stopped_reason,
            recovered=bootstrap_ready,
            bootstrap_ready=bootstrap_ready,
            last_cycle=last_cycle,
        )

    def _run_cycle(self) -> LiveCycleReport | None:
        broker_time = self.market_adapter.get_broker_time()
        market_status = self.market_adapter.market_status(now_utc=broker_time)
        if not market_status.ok:
            raise ConfigValidationError(f"Market adapter unhealthy: {market_status.message}")

        broker_positions = self.bridge.query_positions()
        broker_account = self.bridge.query_account()
        self.last_reconciliation_report = BrokerReconciliationRuntime(self.store).reconcile(
            self.bridge,
            at_utc=broker_time,
            allow_flatten_unresolved=True,
        )

        snapshots: dict[str, MarketSnapshot] = {}
        for symbol in self.config.symbols.allowlist:
            snapshot = build_market_snapshot(
                self.market_adapter,
                config=self.config,
                symbol=symbol,
                cycle_time_utc=broker_time,
                previous_cycle_time_utc=self.previous_cycle_time_utc,
                diagnostics_hook=lambda payload: self._emit_telemetry("deployment.market_data_readiness", payload),
            )
            snapshots[symbol] = snapshot

        primary_symbol = self.config.symbols.allowlist[0]
        primary_snapshot = snapshots[primary_symbol]
        current_m5_close_utc = self._latest_closed_m5_close(primary_snapshot)
        if not self._closed_m5_gate_allows_process(current_m5_close_utc):
            return None

        regimes: dict[str, Any] = {}
        signal_evaluations: list[SignalEvaluation] = []
        for symbol, snapshot in snapshots.items():
            regime = classify_regime(snapshot)
            regimes[symbol] = regime
            evaluation = evaluate_signals(
                snapshot,
                regime,
                governor_state=self.runtime_state.governor_state,
                active_signal_keys=self._active_signal_keys(),
            )
            signal_evaluations.append(evaluation)

        accepted_signals = [evaluation.decision for evaluation in signal_evaluations if evaluation.accepted and evaluation.decision is not None]
        signal_decisions = tuple(accepted_signals)
        selected_signals = rank_opportunities(
            list(signal_decisions),
            context=PortfolioContext(
                active_positions=self._broker_positions_as_snapshots(broker_positions),
                current_time_utc=broker_time,
                execution_health=market_status.health,
                spread_health=_aggregate_health(snapshot.spread_health for snapshot in snapshots.values()),
                latency_health=_aggregate_health(snapshot.latency_health for snapshot in snapshots.values()),
                symbol_cooldown_until_utc={},
            ),
        )

        governor_context = self._governor_context(
            snapshot=primary_snapshot,
            broker_account=broker_account,
            broker_positions=broker_positions,
            market_health=market_status.health,
            selected_signal_count=len(selected_signals),
        )
        governor = evaluate_governor(primary_snapshot, governor_context)
        previous_state = self.runtime_state.governor_state
        self.runtime_state.governor_state = governor.state
        self.runtime_state.last_cycle_time_utc = broker_time
        if previous_state is not governor.state:
            self._emit_telemetry(
                "runtime_governor_transition",
                {
                    "event_time_utc": broker_time.isoformat(),
                    "from_state": previous_state.value,
                    "to_state": governor.state.value,
                    "reason": governor.state_reason,
                },
            )
        self.store.store_governor_state(governor, updated_at_utc=broker_time)

        execution_results: list[ExecutionResult] = []
        for signal in selected_signals:
            snapshot = snapshots[signal.symbol]
            risk = evaluate_risk(
                snapshot,
                signal,
                governor,
                context=self._risk_context(
                    broker_account=broker_account,
                    broker_positions=broker_positions,
                    market_health=market_status.health,
                ),
            )
            if risk.action not in {RiskAction.ENTER, RiskAction.SCALE, RiskAction.PYRAMID} or risk.sized_volume <= 0.0:
                self._emit_telemetry(
                    "execution_intent_rejected",
                    {
                        "event_time_utc": broker_time.isoformat(),
                        "symbol": signal.symbol,
                        "setup_id": signal.setup_id,
                        "risk_action": risk.action.value,
                        "hard_block_reason": risk.hard_block_reason,
                    },
                )
                continue

            validation = validate_execution_intent(
                snapshot,
                signal,
                risk,
                governor,
                registry=self.execution_adapter.registry,
                current_time_utc=broker_time,
                decision_price=(snapshot.tick_bid + snapshot.tick_ask) / 2.0,
            )
            if not validation.accepted or validation.intent is None:
                self._emit_telemetry(
                    "execution_intent_rejected",
                    {
                        "event_time_utc": broker_time.isoformat(),
                        "symbol": signal.symbol,
                        "setup_id": signal.setup_id,
                        "reject_reason": validation.reject_reason,
                    },
                )
                continue

            result = self.execution_adapter.execute(validation.intent, at_utc=broker_time)
            execution_results.append(result)
            self._emit_execution_telemetry(result, broker_time)
            if result.filled or result.partial_fill:
                self.store.store_position(
                    PositionSnapshot(
                        symbol=signal.symbol,
                        direction=signal.direction,
                        setup_id=signal.setup_id,
                        correlation_group=_correlation_group(signal.symbol),
                        risk_pct=float(risk.diagnostics.get("target_risk_pct", risk.sized_volume)),
                        signal_score=signal.score,
                        open_time_utc=broker_time,
                        pyramid_count=0,
                    )
                )

        self._persist_execution_registry()
        reconciliation = BrokerReconciliationRuntime(self.store).reconcile(
            self.bridge,
            at_utc=broker_time,
            allow_flatten_unresolved=True,
        )
        self.last_reconciliation_report = reconciliation
        self.runtime_state.last_processed_m5_close_utc = current_m5_close_utc
        self._store_runtime_snapshot(
            broker_time=broker_time,
            market_status=market_status,
            governor=governor,
            broker_account=broker_account,
            selected_signal_count=len(selected_signals),
            execution_count=len(execution_results),
        )

        return LiveCycleReport(
            cycle_time_utc=broker_time,
            broker_time_utc=broker_time,
            governor_state=governor.state,
            governor_reason=governor.state_reason,
            selected_symbols=tuple(signal.symbol for signal in selected_signals),
            signal_count=len(signal_decisions),
            execution_count=len(execution_results),
            execution_results=tuple(execution_results),
            reconciliation_ready=bool(reconciliation.ready_to_resume),
            market_health=market_status.health,
            feed_health=primary_snapshot.feed_health,
            pace_state=governor.pace_classification,
        )

    def _persist_execution_registry(self) -> None:
        assert self.execution_adapter.registry is not None
        self.store.store_execution_registry(self.execution_adapter.registry.entries_by_submission_uuid.values())

    def _store_runtime_state(
        self,
        cycle_report: LiveCycleReport,
    ) -> None:
        self.store.store_runtime_state(
            {
                "runtime.mode": self.config.bot.mode.value,
                "runtime.governor_state": cycle_report.governor_state.value,
                "runtime.last_cycle_time_utc": cycle_report.cycle_time_utc.isoformat(),
                "runtime.last_processed_m5_close_utc": (
                    self.runtime_state.last_processed_m5_close_utc.isoformat()
                    if self.runtime_state.last_processed_m5_close_utc is not None
                    else None
                ),
                "runtime.last_broker_time_utc": cycle_report.broker_time_utc.isoformat(),
                "runtime.selected_symbols": list(cycle_report.selected_symbols),
                "runtime.execution_count": cycle_report.execution_count,
                "runtime.signal_count": cycle_report.signal_count,
                "runtime.reconciliation_ready": cycle_report.reconciliation_ready,
                "runtime.market_health": cycle_report.market_health.value,
                "runtime.feed_health": cycle_report.feed_health.value,
                "runtime.pace_state": cycle_report.pace_state.value,
            }
        )

    def _store_runtime_snapshot(
        self,
        *,
        broker_time: datetime,
        market_status: Any,
        governor: GovernorDecision,
        broker_account: Mapping[str, Any],
        selected_signal_count: int,
        execution_count: int,
    ) -> None:
        self.store.store_telemetry_index(
            "runtime_cycle",
            {
                "event_time_utc": broker_time.isoformat(),
                "governor_state": governor.state.value,
                "governor_reason": governor.state_reason,
                "selected_signal_count": selected_signal_count,
                "execution_count": execution_count,
                "market_health": market_status.health.value,
                "broker_ready": market_status.ok,
                "broker_equity": float(broker_account.get("equity", 0.0)),
                "broker_balance": float(broker_account.get("balance", broker_account.get("equity", 0.0))),
            },
        )

    def _emit_recovered(self, when: datetime, *, bootstrap_ready: bool) -> None:
        self._emit_telemetry(
            "runtime_recovered",
            {
                "event_time_utc": when.isoformat(),
                "bootstrap_ready": bootstrap_ready,
                "ready_to_resume": bool(getattr(self.bootstrap_report, "ready_to_resume", True)),
            },
        )

    def _emit_execution_telemetry(self, result: ExecutionResult, when: datetime) -> None:
        kind = "submitted"
        if result.filled:
            kind = "filled"
        elif result.partial_fill:
            kind = "filled"
        elif result.rejected:
            kind = "rejected"
        bridge_diagnostics = dict((result.diagnostics or {}).get("bridge", {}).get("diagnostics", {}) or {})
        bridge_payload = dict((result.diagnostics or {}).get("bridge", {}) or {})
        last_error = bridge_diagnostics.get("last_error")
        self.store.store_execution_event(
            submission_uuid=result.submission_uuid,
            event_type=kind,
            payload={
                "kind": kind,
                "accepted": result.accepted,
                "rejected": result.rejected,
                "filled": result.filled,
                "partial_fill": result.partial_fill,
                "ticket": result.ticket,
                "broker_code": result.broker_code,
                "classification": result.classification,
                "retryable": result.retryable,
                "fatal": result.fatal,
                "terminal": result.terminal,
                "message": result.message,
                "event_time_utc": when.isoformat(),
                "request": result.request,
                "response": result.response,
                "bridge_diagnostics": bridge_diagnostics,
                "bridge_status": bridge_payload,
                "last_error": last_error,
            },
        )
        self._emit_telemetry(
            f"execution_{kind}",
            {
                "event_time_utc": when.isoformat(),
                "submission_uuid": result.submission_uuid,
                "setup_id": result.setup_id,
                "symbol": result.symbol,
                "ticket": result.ticket,
                "broker_code": result.broker_code,
                "classification": result.classification,
                "accepted": result.accepted,
                "rejected": result.rejected,
                "filled": result.filled,
                "partial_fill": result.partial_fill,
                "request": result.request,
                "response": result.response,
                "bridge_diagnostics": bridge_diagnostics,
                "bridge_status": bridge_payload,
                "last_error": last_error,
            },
        )

    def _closed_m5_gate_allows_process(self, current_m5_close_utc: datetime) -> bool:
        last_processed = self.runtime_state.last_processed_m5_close_utc
        decision = "process"
        if last_processed is not None and current_m5_close_utc <= last_processed:
            decision = "skip"
        self._emit_telemetry(
            "deployment.closed_m5_gate",
            {
                "cycle_time_utc": current_m5_close_utc.isoformat(),
                "current_m5_close": current_m5_close_utc.isoformat(),
                "last_processed_m5_close": last_processed.isoformat() if last_processed is not None else None,
                "decision": decision,
            },
        )
        return decision == "process"

    def _latest_closed_m5_close(self, snapshot: MarketSnapshot) -> datetime:
        closes = [bar["close_time_utc"] for bar in snapshot.bars_m5]
        if not closes:
            raise ConfigValidationError("M5 snapshot is missing closed bars")
        latest = max(closes)
        if not isinstance(latest, datetime):
            raise ConfigValidationError("M5 snapshot close timestamps are invalid")
        return _ensure_utc(latest, field_name="latest_closed_m5_close_utc")

    def _restore_runtime_state(self) -> None:
        raw_state = self.store.load_runtime_state()
        governor_state = self._runtime_state_value(raw_state, "runtime.governor_state")
        if isinstance(governor_state, str) and governor_state:
            try:
                self.runtime_state.governor_state = GovernorState(governor_state)
            except ValueError:
                pass
        last_cycle_time = self._runtime_state_datetime(raw_state, "runtime.last_cycle_time_utc")
        if last_cycle_time is not None:
            self.runtime_state.last_cycle_time_utc = last_cycle_time
            self.previous_cycle_time_utc = last_cycle_time
        last_processed = self._runtime_state_datetime(raw_state, "runtime.last_processed_m5_close_utc")
        if last_processed is not None:
            self.runtime_state.last_processed_m5_close_utc = last_processed

    def _runtime_state_value(self, raw_state: Mapping[str, str], key: str) -> Any:
        raw_value = raw_state.get(key)
        if raw_value is None:
            return None
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError:
            return raw_value

    def _runtime_state_datetime(self, raw_state: Mapping[str, str], key: str) -> datetime | None:
        value = self._runtime_state_value(raw_state, key)
        if value is None:
            return None
        if isinstance(value, datetime):
            return _ensure_utc(value, field_name=key)
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return _ensure_utc(parsed, field_name=key)

    def _emit_telemetry(self, topic: str, payload: Mapping[str, Any]) -> None:
        self.store.store_telemetry_index(topic, payload)

    def _active_signal_keys(self) -> dict[str, datetime]:
        assert self.execution_adapter.registry is not None
        active: dict[str, datetime] = {}
        current_time = self.previous_cycle_time_utc or _now_utc()
        for setup_id, entry in self.execution_adapter.registry.entries_by_setup_id.items():
            if entry.expires_at_utc is not None and entry.expires_at_utc > current_time:
                active[setup_id] = entry.expires_at_utc
        return active

    def _broker_positions_as_snapshots(self, broker_positions: tuple[Mapping[str, Any], ...]) -> tuple[PositionSnapshot, ...]:
        snapshots: list[PositionSnapshot] = []
        for record in broker_positions:
            symbol = str(record.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            direction = _direction_from_record(record)
            if direction is None:
                continue
            setup_id = str(record.get("setup_id") or record.get("submission_uuid") or record.get("position_ticket") or record.get("ticket") or symbol)
            risk_pct = float(record.get("risk_pct", record.get("margin", 0.0)) or 0.0)
            signal_score = float(record.get("signal_score", 0.0) or 0.0)
            snapshots.append(
                PositionSnapshot(
                    symbol=symbol,
                    direction=direction,
                    setup_id=setup_id,
                    correlation_group=_correlation_group(symbol),
                    risk_pct=risk_pct,
                    signal_score=signal_score,
                    open_time_utc=_parse_optional_datetime(record.get("time") or record.get("open_time_utc") or record.get("time_open")),
                    pyramid_count=int(record.get("pyramid_count", 0) or 0),
                )
            )
        return tuple(snapshots)

    def _governor_context(
        self,
        *,
        snapshot: MarketSnapshot,
        broker_account: Mapping[str, Any],
        broker_positions: tuple[Mapping[str, Any], ...],
        market_health: HealthState,
        selected_signal_count: int,
    ) -> GovernorContext:
        equity = float(broker_account.get("equity", broker_account.get("balance", 100_000.0)) or 100_000.0)
        drawdown_pct = float(broker_account.get("drawdown_pct", 0.0) or 0.0)
        daily_loss_pct = float(broker_account.get("daily_loss_pct", 0.0) or 0.0)
        peak_equity = equity if drawdown_pct <= 0.0 else equity / max(1.0 - (drawdown_pct / 100.0), 1e-8)
        realized_pnl_r = float(
            broker_account.get("realized_pnl_r", broker_account.get("unrealized_r", 0.0)) or 0.0
        )
        elapsed = max(0.0, (snapshot.cycle_time_utc - self.runtime_state.started_at_utc).total_seconds())
        contest_window_seconds = max(1.0, float(self.config.contest.contest_window_minutes) * 60.0)
        signal_density = min(1.0, selected_signal_count / max(len(self.config.symbols.allowlist), 1))
        return GovernorContext(
            contest_elapsed_pct=min(100.0, (elapsed / contest_window_seconds) * 100.0),
            equity=equity,
            peak_equity=peak_equity,
            drawdown_pct=drawdown_pct,
            daily_loss_pct=daily_loss_pct,
            realized_pnl_r=realized_pnl_r,
            signal_density=signal_density,
            execution_health=market_health,
            feed_health=market_health,
            opportunity_starvation_minutes=0.0,
            recovery_momentum=1.0 if getattr(self.bootstrap_report, "ready_to_resume", True) else 0.0,
            profile=self.config.bot.profile,
            ranking_proxy_available=self.config.contest.ranking_proxy_enabled,
            ranking_proxy_pace_ratio=None,
            broker_stable=bool(market_health is HealthState.GREEN),
            recovery_uncertainty=not bool(getattr(self.bootstrap_report, "ready_to_resume", True)),
            execution_anomaly_cluster=False,
            news_clear=not snapshot.news.lockout_active,
            open_positions=self._broker_positions_as_snapshots(broker_positions),
        )

    def _risk_context(
        self,
        *,
        broker_account: Mapping[str, Any],
        broker_positions: tuple[Mapping[str, Any], ...],
        market_health: HealthState,
    ) -> RiskContext:
        equity = float(broker_account.get("equity", broker_account.get("balance", 100_000.0)) or 100_000.0)
        drawdown_pct = float(broker_account.get("drawdown_pct", 0.0) or 0.0)
        daily_loss_pct = float(broker_account.get("daily_loss_pct", 0.0) or 0.0)
        unrealized_r = float(broker_account.get("unrealized_r", 0.0) or 0.0)
        open_positions = self._broker_positions_as_snapshots(broker_positions)
        return RiskContext(
            account_equity=equity,
            daily_start_equity=equity,
            current_drawdown_pct=drawdown_pct,
            current_daily_loss_pct=daily_loss_pct,
            current_unrealized_r=unrealized_r,
            loss_streak=0,
            open_positions=open_positions,
            execution_health=market_health,
            spread_health=market_health,
            latency_health=market_health,
            broker_stable=bool(market_health is HealthState.GREEN),
            recovery_uncertainty=not bool(getattr(self.bootstrap_report, "ready_to_resume", True)),
            execution_anomaly_cluster=False,
            current_portfolio_risk_pct=sum(position.risk_pct for position in open_positions),
            current_symbol_risk_pct=0.0,
        )


def _aggregate_health(values: Any) -> HealthState:
    seen = list(values)
    if any(value is HealthState.RED for value in seen):
        return HealthState.RED
    if any(value is HealthState.YELLOW for value in seen):
        return HealthState.YELLOW
    return HealthState.GREEN


def _direction_from_record(record: Mapping[str, Any]) -> Direction | None:
    value = record.get("direction") or record.get("type")
    if isinstance(value, Direction):
        return value
    if isinstance(value, str) and value.strip():
        raw = value.strip().upper()
        if raw in {"BUY", "LONG"}:
            return Direction.LONG
        if raw in {"SELL", "SHORT"}:
            return Direction.SHORT
        try:
            return Direction(raw)
        except ValueError:
            return None
    if isinstance(value, int) and not isinstance(value, bool):
        if value == 0:
            return Direction.LONG
        if value == 1:
            return Direction.SHORT
    return None


def _correlation_group(symbol: str) -> str:
    canonical = symbol.upper()
    if canonical in {"GBPUSD", "EURUSD"}:
        return "GBPUSD_EURUSD"
    if canonical == "GBPJPY" or (canonical.startswith("GBP") and canonical.endswith("JPY")):
        return "GBPJPY_COMBOS"
    return canonical


def _parse_optional_datetime(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _ensure_utc(raw, field_name="datetime")
    try:
        value = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return _ensure_utc(value, field_name="datetime")


def _ensure_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ConfigValidationError(f"{field_name} must be timezone-aware UTC datetime")
    return value.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)
