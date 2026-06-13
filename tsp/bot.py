"""Main loop orchestration for TSP V1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol

from .competition import (
    apply_governor_bias,
    build_competition_context,
    evaluate_governor,
)
from .config import AppConfig
from .data_pipeline import (
    MarketDataAdapter,
    SnapshotBuildConfig,
    SymbolContract,
    build_market_snapshot,
    build_symbol_contract,
)
from .execution import BrokerExecutionAdapter, ExecutionRegistry, execute_order
from .position_manager import LayerMutation, evaluate_lifecycle, recover_orphans
from .persistence import SQLitePersistence
from .regime import classify_regime
from .risk import (
    evaluate_aggression_transition,
    evaluate_emergency_exit,
    evaluate_risk,
)
from .signals import evaluate_signals
from .state import (
    AggressionState,
    CompetitionContext,
    Direction,
    LayerState,
    Module,
    PositionState,
    Regime,
    RegimeResult,
    RuntimeState,
    SignalScore,
    TradePhase,
)


class TSPAdapter(MarketDataAdapter, BrokerExecutionAdapter, Protocol):
    """Combined adapter contract required by the orchestrator."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ProcessBarResult:
    executed: bool
    execution_status: str | None
    signal_generated: bool
    regime: Regime
    governor_state: str | None
    processed_new_bar: bool
    duplicate_bar_skip: bool
    bar_timestamp: datetime | None = None
    regime_confidence: float | None = None
    regime_conflict_note: str = ""
    regime_raw_scores: dict[str, float] = field(default_factory=dict)
    regime_diagnostics: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class TSPBot:
    """Production-grade orchestration shell for phased TSP runtime."""

    config: AppConfig
    adapter: TSPAdapter | None = None
    snapshot_config: SnapshotBuildConfig = field(default_factory=SnapshotBuildConfig)
    runtime: RuntimeState | None = None
    contract: SymbolContract | None = None
    registry: ExecutionRegistry | None = None
    persistence: SQLitePersistence | None = None
    last_bar_time: datetime | None = None

    def bootstrap(self) -> None:
        """Initialize runtime state, contract, context, and registry."""
        if self.adapter is None:
            return

        if self.runtime is not None and self.contract is not None and self.registry is not None:
            return

        now = self.adapter.get_server_time()
        current_session = self._session_for_bootstrap(now)
        start_equity = float(self.adapter.get_equity())
        self.persistence = SQLitePersistence(self.config.bot.db_path) if self.config.bot.db_path is not None else None
        if self.persistence is not None:
            self.persistence.initialize()
        self.contract = build_symbol_contract(
            self.config.supported_symbol,
            self.adapter.get_symbol_info(self.config.supported_symbol),
        )
        competition_ctx = build_competition_context(
            cfg=self.config.competition,
            start_equity=start_equity,
            starting_date=now.date(),
            current_session=current_session,
            now=now,
        )
        restored_position = PositionState()
        persisted_fingerprint: str | None = None
        persisted_registry_entries = ()
        snapshot = None
        if self.persistence is not None:
            persisted = self.persistence.load_bootstrap_state(now=now)
            snapshot = persisted.runtime
            self.last_bar_time = snapshot.last_bar_time
            competition_ctx = persisted.competition_ctx or competition_ctx
            restored_position = persisted.position
            persisted_fingerprint = persisted.config_fingerprint
            persisted_registry_entries = persisted.registry_entries
            if persisted_fingerprint is not None and persisted_fingerprint != self.config.fingerprint:
                raise RuntimeError(
                    "Persisted config fingerprint does not match current config. "
                    "Refuse startup to prevent config drift."
                )
        self.runtime = RuntimeState(
            symbol=self.config.supported_symbol,
            magic=self.config.bot.magic_number,
            starting_equity=start_equity,
            start_time=now,
            aggression=AggressionState.NORMAL,
            position=restored_position,
            equity_current=start_equity,
            equity_peak=start_equity,
            daily_start_equity=start_equity,
            competition_ctx=competition_ctx,
            risk_params=self.config.risk,
            last_broker_day=now.date(),
        )
        self.registry = ExecutionRegistry(self.config.execution.dedup_ttl_seconds)
        self.registry.restore(list(persisted_registry_entries), now)
        self._reconcile_broker_reality(now)
        if self.persistence is not None:
            assert snapshot is not None
            self.runtime.kill_switch_active = snapshot.kill_switch_active
            self.runtime.kill_reason = snapshot.kill_reason
            self.runtime.consecutive_bar_errors = snapshot.consecutive_bar_errors
            self.persistence.save_config_fingerprint(self.config.fingerprint)
        self._recover_startup_orphans()

    def process_bar(self) -> ProcessBarResult:
        """Process one closed M1 bar according to the locked orchestration order."""
        self.bootstrap()
        if self.adapter is None or self.runtime is None or self.contract is None or self.registry is None:
            raise RuntimeError("Bot is not fully initialized")

        runtime = self.runtime
        if runtime.kill_switch_active:
            return ProcessBarResult(
                executed=False,
                execution_status=None,
                signal_generated=False,
                regime=runtime.regime.regime,
                governor_state=runtime.competition_ctx.governor_state.name if runtime.competition_ctx else None,
                processed_new_bar=False,
                duplicate_bar_skip=False,
            )

        now = self.adapter.get_server_time()
        snap = build_market_snapshot(
            self.adapter,
            symbol=self.config.supported_symbol,
            cfg=self.snapshot_config,
            server_time=now,
        )
        if self.last_bar_time is not None and snap.timestamp <= self.last_bar_time:
            return ProcessBarResult(
                executed=False,
                execution_status=None,
                signal_generated=False,
                regime=runtime.regime.regime,
                governor_state=runtime.competition_ctx.governor_state.name if runtime.competition_ctx else None,
                processed_new_bar=False,
                duplicate_bar_skip=True,
                bar_timestamp=snap.timestamp,
                regime_confidence=runtime.regime.confidence,
                regime_conflict_note=runtime.regime.conflict_note,
                regime_raw_scores=dict(runtime.regime.raw_scores),
                regime_diagnostics=dict(runtime.regime.diagnostics),
            )

        runtime.snap = snap
        self._update_equity(now)
        self._update_unrealized()
        self._handle_session_boundary(now)
        self._handle_day_boundary(now)
        self._apply_position_phase_transitions()
        self._apply_cooldown_progression()

        regime = classify_regime(snap, self.config.regime)
        runtime.regime = regime

        aggression_transition = evaluate_aggression_transition(runtime)
        runtime.aggression = aggression_transition.new_state
        if aggression_transition.activate_kill:
            runtime.kill_switch_active = True
            runtime.kill_reason = aggression_transition.kill_reason

        governor = evaluate_governor(runtime, regime, self.config.competition, now=now)
        if runtime.competition_ctx is not None:
            runtime.competition_ctx = CompetitionContext(
                total_days=runtime.competition_ctx.total_days,
                start_equity=runtime.competition_ctx.start_equity,
                starting_date=runtime.competition_ctx.starting_date,
                total_pnl_r=runtime.competition_ctx.total_pnl_r,
                daily_pnl_r=runtime.competition_ctx.daily_pnl_r,
                session_pnl_r=runtime.competition_ctx.session_pnl_r,
                session_loss_count=runtime.competition_ctx.session_loss_count,
                session_risk_committed_r=runtime.competition_ctx.session_risk_committed_r,
                current_session=runtime.competition_ctx.current_session,
                governor_state=governor.governor_state,
                days_elapsed=runtime.competition_ctx.days_elapsed,
                updated_at=now,
            )

        runtime.spread_elevated_bars = (
            runtime.spread_elevated_bars + 1
            if snap.spread_baseline > 0 and (snap.spread_current / snap.spread_baseline) > 3.5
            else 0
        )

        lifecycle = None
        if runtime.position.layer_count > 0:
            lifecycle = evaluate_lifecycle(self.adapter, runtime, self.config.lifecycle, self.contract)
            self._apply_layer_mutations(lifecycle.mutations)
            if lifecycle.signal_kill:
                runtime.kill_switch_active = True
                runtime.kill_reason = lifecycle.kill_reason

        emergency = evaluate_emergency_exit(
            runtime,
            snap,
            spread_persist_bars=runtime.spread_elevated_bars,
        )
        if emergency.should_exit:
            runtime.kill_switch_active = True
            runtime.kill_reason = emergency.reason

        effective_aggression = apply_governor_bias(
            runtime.aggression,
            governor.aggression_bias,
            runtime.kill_switch_active,
        )

        signal: SignalScore | None = None
        if not governor.session_pause:
            signal = evaluate_signals(
                snap,
                regime,
                effective_aggression,
                runtime,
                self.config.signal,
            )

        execution_status: str | None = None
        executed = False
        risk_decision = None
        execution = None
        if signal is not None:
            runtime.last_signal = signal
            runtime.last_signal_age_bars = 0
            prior_aggression = runtime.aggression
            runtime.aggression = effective_aggression
            risk_decision = evaluate_risk(signal, snap, runtime, self.contract)
            runtime.aggression = prior_aggression
            if (
                risk_decision.action in {"ENTER", "PYRAMID"}
                and runtime.competition_ctx is not None
                and runtime.competition_ctx.session_risk_committed_r + risk_decision.r_percent
                <= governor.session_risk_budget_r
            ):
                execution = execute_order(
                    self.adapter,
                    self.registry,
                    signal=signal,
                    decision=risk_decision,
                    snap=snap,
                    runtime=runtime,
                    regime=regime.regime,
                    contract=self.contract,
                    cfg=self.config.execution,
                )
                execution_status = execution.status.value
                executed = execution.status.value in {"FILLED", "PARTIAL_FILL", "FILLED_UNVERIFIED"}
                if executed:
                    self._onboard_execution(
                        signal,
                        execution.fill_price or risk_decision.entry_price,
                        execution.fill_lot or risk_decision.lot_size,
                        execution.ticket,
                    )
                    if runtime.competition_ctx is not None:
                        runtime.competition_ctx = CompetitionContext(
                            total_days=runtime.competition_ctx.total_days,
                            start_equity=runtime.competition_ctx.start_equity,
                            starting_date=runtime.competition_ctx.starting_date,
                            total_pnl_r=runtime.competition_ctx.total_pnl_r,
                            daily_pnl_r=runtime.competition_ctx.daily_pnl_r,
                            session_pnl_r=runtime.competition_ctx.session_pnl_r,
                            session_loss_count=runtime.competition_ctx.session_loss_count,
                            session_risk_committed_r=runtime.competition_ctx.session_risk_committed_r + risk_decision.r_percent,
                            current_session=runtime.competition_ctx.current_session,
                            governor_state=runtime.competition_ctx.governor_state,
                            days_elapsed=runtime.competition_ctx.days_elapsed,
                            updated_at=now,
                        )
            elif signal is not None and execution_status is None:
                execution_status = "BUDGET_BLOCKED" if runtime.competition_ctx is not None else "BLOCKED"
        else:
            runtime.last_signal_age_bars += 1

        self._increment_trade_counters()
        self.last_bar_time = snap.timestamp
        runtime.consecutive_bar_errors = 0
        self._persist_successful_bar(
            snap_timestamp=snap.timestamp,
            regime=regime,
            signal=signal,
            risk_decision=risk_decision,
            execution=execution,
            lifecycle=lifecycle,
            governor=governor,
        )

        return ProcessBarResult(
            executed=executed,
            execution_status=execution_status,
            signal_generated=signal is not None,
            regime=regime.regime,
            governor_state=governor.governor_state.name,
            processed_new_bar=True,
            duplicate_bar_skip=False,
            bar_timestamp=snap.timestamp,
            regime_confidence=regime.confidence,
            regime_conflict_note=regime.conflict_note,
            regime_raw_scores=dict(regime.raw_scores),
            regime_diagnostics=dict(regime.diagnostics),
        )

    def _session_for_bootstrap(self, now: datetime) -> str:
        hour = now.astimezone(timezone.utc).hour
        if 0 <= hour < 7:
            return "ASIA"
        if 7 <= hour < 12:
            return "LONDON"
        if 12 <= hour < 16:
            return "OVERLAP"
        if 16 <= hour < 21:
            return "NY"
        return "DEAD"

    def _update_equity(self, now: datetime) -> None:
        assert self.runtime is not None
        equity = float(self.adapter.get_equity())  # type: ignore[union-attr]
        self.runtime.equity_current = equity
        self.runtime.equity_peak = max(self.runtime.equity_peak, equity)
        if self.runtime.competition_ctx is not None:
            self.runtime.competition_ctx = CompetitionContext(
                total_days=self.runtime.competition_ctx.total_days,
                start_equity=self.runtime.competition_ctx.start_equity,
                starting_date=self.runtime.competition_ctx.starting_date,
                total_pnl_r=self.runtime.competition_ctx.total_pnl_r,
                daily_pnl_r=self.runtime.competition_ctx.daily_pnl_r,
                session_pnl_r=self.runtime.competition_ctx.session_pnl_r,
                session_loss_count=self.runtime.competition_ctx.session_loss_count,
                session_risk_committed_r=self.runtime.competition_ctx.session_risk_committed_r,
                current_session=self.runtime.competition_ctx.current_session,
                governor_state=self.runtime.competition_ctx.governor_state,
                days_elapsed=self.runtime.competition_ctx.days_elapsed,
                updated_at=now,
            )

    def _update_unrealized(self) -> None:
        assert self.runtime is not None and self.runtime.snap is not None
        pnl_r = 0.0
        for layer in self.runtime.position.layers:
            current_price = self.runtime.snap.bid if layer.direction == Direction.LONG else self.runtime.snap.ask
            pnl_r += layer.unrealized_r(current_price)
        self.runtime.position.update_unrealized(pnl_r)

    def _handle_session_boundary(self, now: datetime) -> None:
        assert self.runtime is not None
        current_session = self._session_for_bootstrap(now)
        if self.runtime.competition_ctx is None:
            return
        if current_session != self.runtime.competition_ctx.current_session:
            self.runtime.competition_ctx = build_competition_context(
                cfg=self.config.competition,
                start_equity=self.runtime.competition_ctx.start_equity,
                starting_date=self.runtime.competition_ctx.starting_date,
                current_session=current_session,
                now=now,
            )

    def _handle_day_boundary(self, now: datetime) -> None:
        assert self.runtime is not None
        broker_day = now.date()
        if self.runtime.last_broker_day is None or broker_day != self.runtime.last_broker_day:
            self.runtime.daily_start_equity = self.runtime.equity_current
            self.runtime.daily_pnl_r = 0.0
            self.runtime.total_trades_today = 0
            self.runtime.consecutive_wins = 0
            self.runtime.consecutive_losses = 0
            self.runtime.last_broker_day = broker_day

    def _apply_position_phase_transitions(self) -> None:
        assert self.runtime is not None
        if self.runtime.position.layer_count == 0 and self.runtime.position.phase in {TradePhase.ENTERED, TradePhase.PYRAMIDED, TradePhase.EXITING}:
            exit_direction = self.runtime.position.direction if self.runtime.position.direction != Direction.FLAT else Direction.LONG
            self.runtime.position.transition_to_cooldown(exit_direction)

    def _apply_cooldown_progression(self) -> None:
        assert self.runtime is not None
        if self.runtime.position.phase != TradePhase.COOLDOWN:
            return
        self.runtime.position.bars_since_exit += 1
        if self.runtime.position.bars_since_exit >= self.runtime.risk_params.reentry_min_bars:
            self.runtime.position.transition_to_idle()

    def _apply_layer_mutations(self, mutations: tuple[LayerMutation, ...]) -> None:
        assert self.runtime is not None
        for mutation in mutations:
            for layer in self.runtime.position.layers:
                if layer.ticket != mutation.ticket:
                    continue
                if mutation.new_sl_price is not None:
                    layer.sl_price = mutation.new_sl_price
                if mutation.new_tp_price is not None:
                    layer.tp_price = mutation.new_tp_price
                if mutation.new_lot_size is not None:
                    layer.lot_size = mutation.new_lot_size
                if mutation.partial_taken is not None:
                    layer.partial_taken = mutation.partial_taken
                if mutation.tp_attach_attempts is not None:
                    layer.tp_attach_attempts = mutation.tp_attach_attempts

    def _onboard_execution(
        self,
        signal: SignalScore,
        fill_price: float,
        fill_lot: float,
        ticket: int | None,
    ) -> None:
        assert self.runtime is not None
        layer = LayerState(
            ticket=ticket or int(_utcnow().timestamp() * 1000),
            direction=signal.direction,
            entry_price=fill_price,
            sl_price=signal.invalidation_anchor,
            tp_price=None,
            lot_size=fill_lot,
            r_risk=0.0,
            initial_r_distance=abs(fill_price - signal.invalidation_anchor),
            open_time=_utcnow(),
            layer_index=self.runtime.position.layer_count,
            module=signal.module,
            setup_id=signal.setup_id,
        )
        self.runtime.position.add_layer(layer, signal)

    def _increment_trade_counters(self) -> None:
        assert self.runtime is not None
        if self.runtime.position.layer_count > 0:
            for layer in self.runtime.position.layers:
                layer.bars_in_trade += 1

    def _recover_startup_orphans(self) -> None:
        assert self.runtime is not None
        positions = self.adapter.get_all_positions(self.runtime.magic) if self.adapter is not None else []
        result = recover_orphans(
            positions,
            known_tickets={layer.ticket for layer in self.runtime.position.layers},
            bot_cfg=self.config.bot,
            lifecycle_cfg=self.config.lifecycle,
        )
        if self.persistence is not None:
            self.persistence.log_lifecycle(result)

    def _reconcile_broker_reality(self, now: datetime) -> None:
        assert self.runtime is not None
        if self.adapter is None:
            return
        broker_positions = self.adapter.get_all_positions(self.runtime.magic)
        broker_tickets = {
            int(position.get("ticket", 0) or 0)
            for position in broker_positions
            if int(position.get("ticket", 0) or 0) > 0
        }
        if not broker_tickets:
            self.runtime.position = PositionState()
            return
        persisted_layers = [
            layer for layer in self.runtime.position.layers if layer.ticket in broker_tickets
        ]
        self.runtime.position = self._rebuild_position_state(persisted_layers)
        if self.registry is not None:
            self.registry.prune(now)

    def _rebuild_position_state(self, layers: list[LayerState]) -> PositionState:
        rebuilt = PositionState(layers=list(layers))
        if not layers:
            return rebuilt
        rebuilt.direction = layers[0].direction
        rebuilt.module = layers[0].module
        rebuilt.phase = TradePhase.PYRAMIDED if len(layers) > 1 else TradePhase.ENTERED
        return rebuilt

    def _persist_successful_bar(
        self,
        *,
        snap_timestamp: datetime,
        regime: RegimeResult,
        signal: SignalScore | None,
        risk_decision,
        execution,
        lifecycle,
        governor,
    ) -> None:
        if self.persistence is None or self.runtime is None or self.registry is None:
            return
        self.persistence.persist_bar_cycle(
            runtime=self.runtime,
            last_bar_time=self.last_bar_time,
            regime=regime,
            snap_timestamp=snap_timestamp,
            governor=governor,
            signal=signal,
            risk_decision=risk_decision,
            execution=execution,
            lifecycle=lifecycle,
            registry=self.registry,
            config_fingerprint=self.config.fingerprint,
        )
