"""Decision engine utama."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import logging
import time
from typing import Any

from mt5_bot.config import Settings
from mt5_bot.execution import ExecutionEngine, ExecutionPlan
from mt5_bot.indicators import calculate_atr, calculate_ma7, calculate_rsi, candle_stats
from mt5_bot.mt5_client import MT5Client, SymbolConstraints
from mt5_bot.risk import build_entry_volume, build_tp_feasibility
from mt5_bot.state import (
    BotPersistentStateSnapshot,
    BotState,
    DirectionLockState,
    FreshnessState,
    PersistedDirectionLock,
    PositionRuntimeState,
    ProgressExitCounterfactualCase,
    StrategyNearMissSample,
    load_persistent_state,
    save_persistent_state,
)


@dataclass(slots=True)
class SetupDecision:
    signal: str | None
    score: int
    blockers: list[str] = field(default_factory=list)
    context: dict[str, str] = field(default_factory=dict)
    effective_tp_distance: float = 0.0
    m5_atr: float = 0.0
    buy_score: int = 0
    sell_score: int = 0
    buy_location_reason: str = ""
    sell_location_reason: str = ""
    buy_follow_label: str = ""
    sell_follow_label: str = ""
    htf_bias: str = ""
    reference_price: float = 0.0
    tp_feasible: bool = False


class MT5TradingBot:
    def __init__(self, settings: Settings, client: MT5Client, logger: logging.Logger) -> None:
        self.settings = settings
        self.client = client
        self.logger = logger
        self.state = BotState()
        self.execution = ExecutionEngine(client=client, logger=logger)
        self._persistent_state_path = Path.cwd() / "runtime" / f"mt5_{self.settings.symbol}_state.json"
        self._operator_ack_path = Path.cwd() / "runtime" / f"mt5_{self.settings.symbol}_ack.json"

    def run_forever(self) -> None:
        self.client.initialize()
        account = self.client.get_account_info()
        constraints = self.client.get_symbol_constraints(self.settings.symbol)
        self.logger.info(
            "Blueprint runner aktif | symbol=%s | asset_mode=%s",
            self.settings.symbol,
            self.settings.asset_mode,
        )
        self.logger.info(
            "Session attached | login=%s server=%s equity=%.2f balance=%.2f",
            getattr(account, "login", "n/a"),
            getattr(account, "server", "n/a"),
            float(getattr(account, "equity", 0.0) or 0.0),
            float(getattr(account, "balance", 0.0) or 0.0),
        )
        self.logger.info(
            "Symbol constraints | symbol=%s digits=%s point=%s min_lot=%.2f step=%.2f "
            "spread_points=%s stop_level=%s min_stop_distance=%.5f",
            constraints.symbol,
            constraints.digits,
            constraints.point,
            constraints.volume_min,
            constraints.volume_step,
            constraints.spread_points,
            constraints.trade_stops_level,
            constraints.min_stop_distance,
        )
        self.logger.info(
            "Decision engine tahap kedua aktif: state machine, freshness, regime lock, time-exit."
        )

        bootstrap_m5_bars = self._normalize_rates(
            self.client.get_rates(self.settings.symbol, "M5", self.settings.bars_fetch_count)
        )
        self._initialize_session_state(
            bootstrap_m5_bars=bootstrap_m5_bars,
            constraints=constraints,
            account_equity=float(getattr(account, "equity", 0.0) or 0.0),
        )

        try:
            while True:
                m1_bars = self._normalize_rates(
                    self.client.get_rates(self.settings.symbol, "M1", self.settings.bars_fetch_count)
                )
                m5_bars = self._normalize_rates(
                    self.client.get_rates(self.settings.symbol, "M5", self.settings.bars_fetch_count)
                )
                h1_bars = self._normalize_rates(
                    self.client.get_rates(self.settings.symbol, "H1", self.settings.bars_fetch_count)
                )
                h4_bars = self._normalize_rates(
                    self.client.get_rates(self.settings.symbol, "H4", self.settings.bars_fetch_count)
                )
                current_htf_bias = self._compute_htf_bias(h1_bars, h4_bars, emit_log=False)
                account = self.client.get_account_info()
                account_equity = float(getattr(account, "equity", 0.0) or 0.0)
                self._process_operator_ack(account_equity=account_equity)
                self._refresh_drawdown_state(account_equity=account_equity)

                self._sync_runtime_positions(constraints, m5_bars=m5_bars)
                self._manage_live_positions(
                    m5_bars=m5_bars,
                    constraints=constraints,
                    current_htf_bias=current_htf_bias,
                )
                self._enforce_hard_drawdown_guard()

                current_m5_bar_time = m5_bars[-2]["time"]
                if self.state.last_processed_m5_bar_time == current_m5_bar_time:
                    time.sleep(self.settings.poll_interval_seconds)
                    continue

                self.state.last_processed_m5_bar_time = current_m5_bar_time
                self.logger.info(
                    "M5 candle closed | bar_time=%s close=%.5f",
                    current_m5_bar_time.isoformat(),
                    m5_bars[-2]["close"],
                )
                current_htf_bias = self._compute_htf_bias(h1_bars, h4_bars, emit_log=True)
                decision = self.build_setup(
                    m1_bars=m1_bars,
                    m5_bars=m5_bars,
                    h1_bars=h1_bars,
                    h4_bars=h4_bars,
                    constraints=constraints,
                    htf_bias=current_htf_bias,
                )
                self._handle_decision(decision, constraints)
                time.sleep(self.settings.poll_interval_seconds)
        except KeyboardInterrupt:
            self._emit_strategy_telemetry_summary()
            self.logger.info("Bot stopped by user")

    def _initialize_session_state(
        self,
        *,
        bootstrap_m5_bars: list[dict[str, float]],
        constraints: SymbolConstraints,
        account_equity: float,
    ) -> None:
        now_utc = datetime.now(timezone.utc)
        self.state.session_started_at = now_utc
        self.state.session_started_monotonic = time.monotonic()
        self.state.startup_reconciled_at = now_utc
        self.state.current_trading_day = now_utc.date()
        persistent_loaded = self._restore_persistent_state(account_equity=account_equity)
        self._process_operator_ack(account_equity=account_equity)
        if self.state.daily_baseline_equity is None:
            self.state.daily_baseline_equity = account_equity
        if self.state.session_peak_equity is None:
            self.state.session_peak_equity = account_equity
        else:
            self.state.session_peak_equity = max(self.state.session_peak_equity, account_equity)
        if len(bootstrap_m5_bars) >= 2:
            self.state.last_processed_m5_bar_time = bootstrap_m5_bars[-2]["time"]
        self._sync_runtime_positions(constraints, m5_bars=bootstrap_m5_bars)

        active_positions = self._get_live_bot_positions(self.settings.symbol)
        self.logger.info(
            "Session state initialized | startup_reconciled_at=%s active_positions=%s last_m5_anchor=%s daily_baseline_equity=%.2f persistent_loaded=%s",
            now_utc.isoformat(),
            len(active_positions),
            self.state.last_processed_m5_bar_time.isoformat() if self.state.last_processed_m5_bar_time else "n/a",
            self.state.daily_baseline_equity or account_equity,
            persistent_loaded,
        )
        if len(active_positions) > self.settings.max_positions:
            self.logger.warning(
                "Startup exposure breach | active_positions=%s configured_max_positions=%s",
                len(active_positions),
                self.settings.max_positions,
            )
        if self.state.manual_ack_required:
            self.logger.error(
                "KILL_SWITCH_ACTIVE | reason=%s manual_ack_required=true ack_file=%s",
                self.state.manual_ack_reason or self.state.trading_disabled_reason or "operator_ack_required",
                self._operator_ack_path,
            )
        self._save_persistent_state()

    def _restore_persistent_state(self, *, account_equity: float) -> bool:
        try:
            snapshot = load_persistent_state(self._persistent_state_path)
        except Exception as exc:
            self.logger.warning("Persistent state load failed | path=%s error=%s", self._persistent_state_path, exc)
            return False

        if snapshot is None:
            return False
        if snapshot.version != 1:
            self.logger.warning("Persistent state version mismatch | found=%s expected=1", snapshot.version)
            return False
        if snapshot.symbol != self.settings.symbol or snapshot.magic_number != self.settings.magic_number:
            self.logger.warning(
                "Persistent state ownership mismatch | symbol=%s magic=%s expected_symbol=%s expected_magic=%s",
                snapshot.symbol,
                snapshot.magic_number,
                self.settings.symbol,
                self.settings.magic_number,
            )
            return False

        current_day_key = self.state.current_trading_day.isoformat() if self.state.current_trading_day else None
        self.state.session_peak_equity = max(snapshot.session_peak_equity or account_equity, account_equity)
        self.state.last_execution_at = snapshot.last_entry_timestamp
        self.state.last_execution_monotonic = None
        self.state.trading_disabled = snapshot.trading_disabled
        self.state.trading_disabled_reason = snapshot.trading_disabled_reason
        self.state.manual_ack_required = snapshot.manual_ack_required
        self.state.manual_ack_reason = snapshot.manual_ack_reason
        self.state.manual_ack_timestamp = snapshot.manual_ack_timestamp
        if self.state.trading_disabled and not self.state.trading_disabled_reason:
            self.state.trading_disabled_reason = "persisted_without_reason"
            self.logger.warning("Persistent trading_disabled loaded without reason | fallback=persisted_without_reason")
        if self.state.manual_ack_required and not self.state.manual_ack_reason:
            self.state.manual_ack_reason = self.state.trading_disabled_reason or "persisted_ack_without_reason"
            self.logger.warning("Persistent manual_ack_required loaded without reason | fallback=%s", self.state.manual_ack_reason)

        if snapshot.trading_day_key == current_day_key:
            self.state.daily_baseline_equity = snapshot.daily_baseline_equity or account_equity
            self.state.soft_drawdown_tripped_today = snapshot.soft_drawdown_tripped_today
            self.state.entry_pause_until = snapshot.entry_pause_until
            self.state.entry_pause_deadline_monotonic = None
            self.state.entry_pause_reason = snapshot.entry_pause_reason
            self.state.consecutive_losses = snapshot.consecutive_losses
            self.state.manual_close_pause_until = snapshot.manual_close_pause_until
            self.state.manual_close_pause_deadline_monotonic = None
        else:
            self.state.daily_baseline_equity = account_equity
            self.state.soft_drawdown_tripped_today = False
            self.state.entry_pause_until = None
            self.state.entry_pause_deadline_monotonic = None
            self.state.entry_pause_reason = None
            self.state.consecutive_losses = 0
            self.state.manual_close_pause_until = None
            self.state.manual_close_pause_deadline_monotonic = None

        restored_locks: dict[str, DirectionLockState] = {}
        for direction, lock in snapshot.direction_locks.items():
            if direction not in {"BUY", "SELL"}:
                self.logger.warning("Persistent direction lock discarded | invalid_direction=%s", direction)
                continue
            restored_locks[direction] = DirectionLockState(
                direction=lock.direction,
                htf_bias=lock.htf_bias,
                ref_high=lock.ref_high,
                ref_low=lock.ref_low,
                timestamp=lock.timestamp,
                buffer=lock.buffer,
                monotonic_started_at=None,
            )
        self.state.direction_locks = restored_locks
        return True


    def _increment_counter(self, counter: dict[str, int], key: str) -> None:
        counter[key] = counter.get(key, 0) + 1

    @staticmethod
    def _categorize_blocker(blocker: str) -> str:
        if blocker.startswith("htf_bias:"):
            return "htf_hold_blocks"
        if blocker.startswith("tp_feasibility:"):
            return "tp_feasibility_blocks"
        if blocker.startswith("location:"):
            return "location_blocks"
        if blocker.startswith("freshness:"):
            return "freshness_blocks"
        if blocker.startswith("direction_lock:"):
            return "direction_lock_blocks"
        if blocker.startswith("manual_ack_required:"):
            return "manual_ack_blocks"
        if blocker.startswith(("hard_drawdown_guard:", "entry_pause:", "position_cap:", "manual_close_cooldown:")):
            return "risk_guard_blocks"
        if blocker.startswith(("trigger:", "follow:")):
            return "quality_blocks"
        return "other_blocks"

    def _record_strategy_blockers(self, decision: SetupDecision) -> None:
        if not decision.blockers:
            return
        telemetry = self.state.strategy_telemetry
        telemetry.blocked_decisions += 1
        primary_bucket = self._categorize_blocker(decision.blockers[0])
        self._increment_counter(telemetry.primary_block_counts, primary_bucket)
        for blocker in decision.blockers:
            self._increment_counter(telemetry.block_counts, self._categorize_blocker(blocker))
        self._record_near_miss_sample(decision)

    def _record_near_miss_sample(self, decision: SetupDecision) -> None:
        if decision.signal is not None:
            return
        candidate_score = max(decision.buy_score, decision.sell_score)
        if candidate_score < self.settings.strategy_near_miss_score_min:
            return

        if decision.sell_score > decision.buy_score:
            side_candidate = "SELL"
        elif decision.buy_score > decision.sell_score:
            side_candidate = "BUY"
        elif decision.htf_bias in {"BUY", "SELL"}:
            side_candidate = decision.htf_bias
        else:
            side_candidate = "SELL"

        candidate_location = decision.sell_location_reason if side_candidate == "SELL" else decision.buy_location_reason
        telemetry = self.state.strategy_telemetry
        if len(telemetry.near_miss_samples) >= self.settings.strategy_near_miss_sample_limit:
            telemetry.near_miss_samples.pop(0)
        sample = StrategyNearMissSample(
            timestamp=self.state.last_processed_m5_bar_time or datetime.now(timezone.utc),
            symbol=self.settings.symbol,
            side_candidate=side_candidate,
            buy_score=decision.buy_score,
            sell_score=decision.sell_score,
            blockers=tuple(decision.blockers),
            primary_blocker=decision.blockers[0],
            htf_bias=decision.htf_bias,
            candidate_location=candidate_location,
            tp_feasible=decision.tp_feasible,
            entry_price_reference=decision.reference_price,
        )
        telemetry.near_miss_samples.append(sample)
        telemetry.near_miss_recorded += 1
        self.logger.info(
            'event="near_miss_sampled" timestamp=%s side_candidate=%s buy_score=%s sell_score=%s primary_blocker=%s htf_bias=%s candidate_location=%s tp_feasible=%s ref_price=%.5f',
            sample.timestamp.isoformat(),
            sample.side_candidate,
            sample.buy_score,
            sample.sell_score,
            sample.primary_blocker,
            sample.htf_bias,
            sample.candidate_location,
            sample.tp_feasible,
            sample.entry_price_reference,
        )

    def _update_near_miss_outcomes(self, *, m5_bars: list[dict[str, float]]) -> None:
        samples = self.state.strategy_telemetry.near_miss_samples
        if not samples:
            return
        closed_bars = m5_bars[:-1]
        if not closed_bars:
            return
        latest_closed_time = closed_bars[-1]["time"]
        for sample in samples:
            for horizon_minutes, suffix in ((15, "15m"), (30, "30m"), (60, "60m")):
                close_attr = f"close_{suffix}"
                if getattr(sample, close_attr) is not None:
                    continue
                horizon_end = sample.timestamp + timedelta(minutes=horizon_minutes)
                if latest_closed_time < horizon_end:
                    continue
                window_bars = [bar for bar in closed_bars if sample.timestamp < bar["time"] <= horizon_end]
                if not window_bars:
                    continue
                highs = [float(bar["high"]) for bar in window_bars]
                lows = [float(bar["low"]) for bar in window_bars]
                close_price = float(window_bars[-1]["close"])
                if sample.side_candidate == "SELL":
                    mfe = max(0.0, sample.entry_price_reference - min(lows))
                    mae = max(0.0, max(highs) - sample.entry_price_reference)
                else:
                    mfe = max(0.0, max(highs) - sample.entry_price_reference)
                    mae = max(0.0, sample.entry_price_reference - min(lows))
                setattr(sample, f"mfe_{suffix}", mfe)
                setattr(sample, f"mae_{suffix}", mae)
                setattr(sample, close_attr, close_price)
                if suffix == "15m":
                    self.state.strategy_telemetry.near_miss_completed_15m += 1
                elif suffix == "30m":
                    self.state.strategy_telemetry.near_miss_completed_30m += 1
                else:
                    self.state.strategy_telemetry.near_miss_completed_60m += 1
                self.logger.info(
                    'event="near_miss_outcome" timestamp=%s side_candidate=%s horizon=%s close=%.5f mfe=%.5f mae=%.5f primary_blocker=%s',
                    sample.timestamp.isoformat(),
                    sample.side_candidate,
                    suffix,
                    close_price,
                    mfe,
                    mae,
                    sample.primary_blocker,
                )


    def _record_progress_exit_counterfactual(
        self,
        *,
        runtime: PositionRuntimeState,
        exit_price: float,
        progress: float,
        required_progress: float,
        closed_bars_since_entry: int,
        direction_lock_reason: str | None,
    ) -> None:
        telemetry = self.state.strategy_telemetry
        if len(telemetry.progress_exit_cases) >= self.settings.progress_exit_counterfactual_limit:
            telemetry.progress_exit_cases.pop(0)
        realized_pnl = exit_price - runtime.entry_price if runtime.side == "BUY" else runtime.entry_price - exit_price
        case = ProgressExitCounterfactualCase(
            ticket=runtime.ticket,
            symbol=self.settings.symbol,
            side=runtime.side,
            entry_price=runtime.entry_price,
            exit_price=exit_price,
            realized_pnl=realized_pnl,
            exit_timestamp=datetime.now(timezone.utc),
            progress=progress,
            required_progress=required_progress,
            closed_bars_since_entry=closed_bars_since_entry,
            original_tp_price=runtime.tp_price,
            effective_tp_distance=runtime.effective_tp_distance,
            direction_lock_active_after_exit=runtime.side in self.state.direction_locks,
            direction_lock_side=runtime.side if runtime.side in self.state.direction_locks else None,
            direction_lock_reason=direction_lock_reason,
        )
        telemetry.progress_exit_cases.append(case)
        telemetry.progress_exit_counterfactual_started += 1
        self.logger.info(
            'event="progress_exit_counterfactual_started" ticket=%s symbol=%s side=%s entry_price=%.5f exit_price=%.5f realized_pnl=%.5f exit_timestamp=%s progress=%.5f required_progress=%.5f closed_bars_since_entry=%s direction_lock_active_after_exit=%s direction_lock_side=%s direction_lock_reason=%s',
            case.ticket,
            case.symbol,
            case.side,
            case.entry_price,
            case.exit_price,
            case.realized_pnl,
            case.exit_timestamp.isoformat(),
            case.progress,
            case.required_progress,
            case.closed_bars_since_entry,
            case.direction_lock_active_after_exit,
            case.direction_lock_side or 'none',
            case.direction_lock_reason or 'none',
        )

    def _update_progress_exit_counterfactuals(self, *, m5_bars: list[dict[str, float]]) -> None:
        cases = self.state.strategy_telemetry.progress_exit_cases
        if not cases:
            return
        closed_bars = m5_bars[:-1]
        if not closed_bars:
            return
        latest_closed_time = closed_bars[-1]["time"]
        for case in cases:
            if case.completed:
                continue
            horizon_end = case.exit_timestamp + timedelta(minutes=60)
            if latest_closed_time < horizon_end:
                continue

            window_15m = [bar for bar in closed_bars if case.exit_timestamp < bar["time"] <= case.exit_timestamp + timedelta(minutes=15)]
            window_30m = [bar for bar in closed_bars if case.exit_timestamp < bar["time"] <= case.exit_timestamp + timedelta(minutes=30)]
            window_60m = [bar for bar in closed_bars if case.exit_timestamp < bar["time"] <= horizon_end]
            if not window_60m:
                continue

            def apply_window(window_bars: list[dict[str, float]], suffix: str) -> None:
                if not window_bars:
                    return
                highs = [float(bar["high"]) for bar in window_bars]
                lows = [float(bar["low"]) for bar in window_bars]
                close_price = float(window_bars[-1]["close"])
                if case.side == "SELL":
                    mfe = max(0.0, case.exit_price - min(lows))
                    mae = max(0.0, max(highs) - case.exit_price)
                else:
                    mfe = max(0.0, max(highs) - case.exit_price)
                    mae = max(0.0, case.exit_price - min(lows))
                setattr(case, f"mfe_{suffix}", mfe)
                setattr(case, f"mae_{suffix}", mae)
                setattr(case, f"close_{suffix}", close_price)

            apply_window(window_15m, "15m")
            apply_window(window_30m, "30m")
            apply_window(window_60m, "60m")

            if case.side == "SELL":
                for bar in window_60m:
                    if float(bar["low"]) <= case.original_tp_price:
                        case.original_tp_hit_within_60m = True
                        case.original_tp_hit_at = bar["time"]
                        break
                else:
                    case.original_tp_hit_within_60m = False
            else:
                for bar in window_60m:
                    if float(bar["high"]) >= case.original_tp_price:
                        case.original_tp_hit_within_60m = True
                        case.original_tp_hit_at = bar["time"]
                        break
                else:
                    case.original_tp_hit_within_60m = False

            case.completed = True
            self.state.strategy_telemetry.progress_exit_counterfactual_completed += 1
            if case.original_tp_hit_within_60m:
                self.state.strategy_telemetry.progress_exit_counterfactual_tp_hits_60m += 1
            self.logger.info(
                'event="progress_exit_counterfactual_outcome" ticket=%s symbol=%s side=%s 15m_mfe=%.5f 15m_mae=%.5f 30m_mfe=%.5f 30m_mae=%.5f 60m_mfe=%.5f 60m_mae=%.5f original_tp_hit_within_60m=%s original_tp_hit_at=%s direction_lock_active_after_exit=%s direction_lock_side=%s direction_lock_reason=%s',
                case.ticket,
                case.symbol,
                case.side,
                case.mfe_15m or 0.0,
                case.mae_15m or 0.0,
                case.mfe_30m or 0.0,
                case.mae_30m or 0.0,
                case.mfe_60m or 0.0,
                case.mae_60m or 0.0,
                case.original_tp_hit_within_60m,
                case.original_tp_hit_at.isoformat() if case.original_tp_hit_at else 'none',
                case.direction_lock_active_after_exit,
                case.direction_lock_side or 'none',
                case.direction_lock_reason or 'none',
            )

    def _emit_strategy_telemetry_summary(self) -> None:
        telemetry = self.state.strategy_telemetry
        block_counts = ",".join(f"{key}:{value}" for key, value in sorted(telemetry.block_counts.items())) or "none"
        primary_counts = ",".join(f"{key}:{value}" for key, value in sorted(telemetry.primary_block_counts.items())) or "none"
        self.logger.info(
            "STRATEGY TELEMETRY SUMMARY | executed_trades=%s blocked_decisions=%s near_miss_recorded=%s near_miss_completed_15m=%s near_miss_completed_30m=%s near_miss_completed_60m=%s progress_exit_cf_started=%s progress_exit_cf_completed=%s progress_exit_cf_tp_hits_60m=%s primary_blockers=%s all_blockers=%s",
            telemetry.executed_trades,
            telemetry.blocked_decisions,
            telemetry.near_miss_recorded,
            telemetry.near_miss_completed_15m,
            telemetry.near_miss_completed_30m,
            telemetry.near_miss_completed_60m,
            telemetry.progress_exit_counterfactual_started,
            telemetry.progress_exit_counterfactual_completed,
            telemetry.progress_exit_counterfactual_tp_hits_60m,
            primary_counts,
            block_counts,
        )

    def _save_persistent_state(self) -> None:
        snapshot = BotPersistentStateSnapshot(
            version=1,
            symbol=self.settings.symbol,
            magic_number=self.settings.magic_number,
            trading_day_key=self.state.current_trading_day.isoformat() if self.state.current_trading_day else None,
            updated_at=datetime.now(timezone.utc),
            trading_disabled=self.state.trading_disabled,
            trading_disabled_reason=self.state.trading_disabled_reason,
            manual_ack_required=self.state.manual_ack_required,
            manual_ack_reason=self.state.manual_ack_reason,
            manual_ack_timestamp=self.state.manual_ack_timestamp,
            daily_baseline_equity=self.state.daily_baseline_equity,
            session_peak_equity=self.state.session_peak_equity,
            last_entry_timestamp=self.state.last_execution_at,
            manual_close_pause_until=self.state.manual_close_pause_until,
            soft_drawdown_tripped_today=self.state.soft_drawdown_tripped_today,
            entry_pause_until=self.state.entry_pause_until,
            entry_pause_reason=self.state.entry_pause_reason,
            consecutive_losses=self.state.consecutive_losses,
            direction_locks={
                direction: PersistedDirectionLock(
                    direction=lock.direction,
                    htf_bias=lock.htf_bias,
                    ref_high=lock.ref_high,
                    ref_low=lock.ref_low,
                    timestamp=lock.timestamp,
                    buffer=lock.buffer,
                )
                for direction, lock in self.state.direction_locks.items()
            },
        )
        try:
            save_persistent_state(self._persistent_state_path, snapshot)
        except Exception as exc:
            self.logger.warning("Persistent state save failed | path=%s error=%s", self._persistent_state_path, exc)



    def _seconds_since(self, *, wall_clock_at: datetime | None, monotonic_at: float | None) -> float | None:
        if monotonic_at is not None:
            return max(0.0, time.monotonic() - monotonic_at)
        if wall_clock_at is None:
            return None
        return max(0.0, (datetime.now(timezone.utc) - wall_clock_at).total_seconds())

    def _is_deadline_active(self, *, wall_clock_deadline: datetime | None, monotonic_deadline: float | None) -> bool:
        if monotonic_deadline is not None:
            return time.monotonic() < monotonic_deadline
        if wall_clock_deadline is None:
            return False
        return datetime.now(timezone.utc) < wall_clock_deadline

    def _process_operator_ack(self, *, account_equity: float) -> None:
        if not self.state.manual_ack_required:
            return
        if not self._operator_ack_path.exists():
            return

        try:
            raw = self._operator_ack_path.read_text(encoding="utf-8")
            payload = __import__("json").loads(raw)
        except Exception as exc:
            self.logger.warning(
                "Operator ack file invalid | path=%s error=%s",
                self._operator_ack_path,
                exc,
            )
            return

        if not isinstance(payload, dict):
            self.logger.warning("Operator ack file rejected | path=%s reason=payload_not_object", self._operator_ack_path)
            return
        if not bool(payload.get("acknowledge", False)):
            self.logger.warning("Operator ack file rejected | path=%s reason=acknowledge_false", self._operator_ack_path)
            return
        if str(payload.get("symbol", self.settings.symbol)) != self.settings.symbol:
            self.logger.warning(
                "Operator ack file rejected | path=%s reason=symbol_mismatch value=%s expected=%s",
                self._operator_ack_path,
                payload.get("symbol"),
                self.settings.symbol,
            )
            return
        if int(payload.get("magic_number", self.settings.magic_number)) != self.settings.magic_number:
            self.logger.warning(
                "Operator ack file rejected | path=%s reason=magic_mismatch value=%s expected=%s",
                self._operator_ack_path,
                payload.get("magic_number"),
                self.settings.magic_number,
            )
            return

        operator_reason = str(payload.get("reason", "operator_acknowledged"))
        now_utc = datetime.now(timezone.utc)
        previous_disable_reason = self.state.trading_disabled_reason or self.state.manual_ack_reason or "hard_drawdown_guard"
        self.state.trading_disabled = False
        self.state.trading_disabled_reason = None
        self.state.manual_ack_required = False
        self.state.manual_ack_reason = None
        self.state.manual_ack_timestamp = now_utc
        self.state.session_peak_equity = max(account_equity, 1e-9)
        self._save_persistent_state()

        try:
            self._operator_ack_path.unlink()
        except OSError as exc:
            self.logger.warning("Operator ack cleanup failed | path=%s error=%s", self._operator_ack_path, exc)

        self.logger.warning(
            "Operator acknowledge accepted | previous_reason=%s operator_reason=%s session_peak_reset=%.2f ack_timestamp=%s",
            previous_disable_reason,
            operator_reason,
            self.state.session_peak_equity,
            now_utc.isoformat(),
        )

    def _handle_decision(self, decision: SetupDecision, constraints: SymbolConstraints) -> None:
        if decision.signal is None:
            self._record_strategy_blockers(decision)
            self.logger.info(
                "No valid setup | blockers=%s",
                ",".join(decision.blockers) if decision.blockers else "none",
            )
            return

        self.logger.info(
            "Valid setup detected | signal=%s score=%s context=%s",
            decision.signal,
            decision.score,
            decision.context,
        )
        if not self.settings.enable_order_execution:
            self.logger.info(
                "Execution disabled by config | signal=%s effective_tp=%.5f",
                decision.signal,
                decision.effective_tp_distance,
            )
            return

        live_positions = self._get_live_bot_positions(self.settings.symbol)
        if self.state.manual_ack_required:
            self.logger.warning(
                "[ENTRY BLOCK] reason=manual_ack_required state=%s ack_file=%s",
                self.state.manual_ack_reason or self.state.trading_disabled_reason or "active",
                self._operator_ack_path,
            )
            return
        if self.state.trading_disabled:
            self.logger.warning(
                "[ENTRY BLOCK] reason=hard_drawdown_guard state=%s",
                self.state.trading_disabled_reason or "active",
            )
            return
        if self._is_deadline_active(
            wall_clock_deadline=self.state.entry_pause_until,
            monotonic_deadline=self.state.entry_pause_deadline_monotonic,
        ):
            self.logger.warning(
                "[ENTRY BLOCK] reason=entry_pause until=%s detail=%s",
                self.state.entry_pause_until.isoformat(),
                self.state.entry_pause_reason or "active",
            )
            return
        if len(live_positions) >= self.settings.max_positions:
            self.logger.warning(
                "[ENTRY BLOCK] reason=position_cap current=%s max=%s",
                len(live_positions),
                self.settings.max_positions,
            )
            return
        if (
            self.state.last_execution_signal == decision.signal
            and self.state.last_execution_m5_bar_time == self.state.last_processed_m5_bar_time
        ):
            self.logger.warning(
                "[ENTRY BLOCK] reason=duplicate_signal_same_m5 signal=%s bar_time=%s",
                decision.signal,
                self.state.last_processed_m5_bar_time.isoformat() if self.state.last_processed_m5_bar_time else "n/a",
            )
            return
        if self.state.session_started_at is not None:
            seconds_since_start = self._seconds_since(
                wall_clock_at=self.state.session_started_at,
                monotonic_at=self.state.session_started_monotonic,
            ) or 0.0
            if seconds_since_start < self.settings.min_entry_interval_seconds:
                self.logger.info(
                    "[ENTRY BLOCK] reason=startup_entry_guard elapsed=%s required=%s",
                    int(seconds_since_start),
                    self.settings.min_entry_interval_seconds,
                )
                return
        if self.state.last_execution_at is not None:
            elapsed = self._seconds_since(
                wall_clock_at=self.state.last_execution_at,
                monotonic_at=self.state.last_execution_monotonic,
            ) or 0.0
            if elapsed < self.settings.min_seconds_between_entries:
                self.logger.warning(
                    "[ENTRY BLOCK] reason=anti_burst elapsed=%s required=%s",
                    int(elapsed),
                    self.settings.min_seconds_between_entries,
                )
                return

        account = self.client.get_account_info()
        tick = self.client.get_latest_tick(self.settings.symbol)
        estimated_entry = float(tick.ask if decision.signal == "BUY" else tick.bid)
        same_side_positions = [pos for pos in live_positions if self._position_side(pos) == decision.signal]
        opposite_positions = [pos for pos in live_positions if self._position_side(pos) != decision.signal]

        if opposite_positions:
            self.logger.warning(
                "[ENTRY BLOCK] reason=opposite_side_active direction=%s opposite_count=%s",
                decision.signal,
                len(opposite_positions),
            )
            return
        if self.settings.layer_count <= 1 and same_side_positions:
            self.logger.info("Execution skipped | same bot position still active")
            return
        if len(same_side_positions) >= self.settings.max_layers_per_direction:
            self.logger.warning(
                "[ENTRY BLOCK] reason=max_layers_direction direction=%s layers=%s limit=%s",
                decision.signal,
                len(same_side_positions),
                self.settings.max_layers_per_direction,
            )
            return
        if same_side_positions:
            nearest_distance = min(abs(float(pos.price_open) - estimated_entry) for pos in same_side_positions)
            spacing_required = decision.m5_atr * self.settings.layer_spacing_atr_mult
            if nearest_distance < spacing_required:
                self.logger.warning(
                    "[ENTRY BLOCK] reason=atr_spacing distance=%.5f required=%.5f",
                    nearest_distance,
                    spacing_required,
                )
                return
        estimated_loss_per_lot = self.client.estimate_loss_per_lot(
            symbol=self.settings.symbol,
            side=decision.signal,
            entry_price=estimated_entry,
            distance=decision.effective_tp_distance,
        )
        volume = build_entry_volume(
            equity=float(getattr(account, "equity", 0.0) or 0.0),
            total_setup_risk_pct=self.settings.total_setup_risk_pct,
            estimated_loss_per_lot=estimated_loss_per_lot,
            constraints=constraints,
            max_lot_per_order=self.settings.max_lot_per_order,
        )
        plan = ExecutionPlan(
            symbol=self.settings.symbol,
            side=decision.signal,
            volume=volume,
            target_tp_distance=decision.effective_tp_distance,
            effective_tp_distance=decision.effective_tp_distance,
        )
        if same_side_positions:
            self.logger.info(
                "[LAYER ENTRY] direction=%s spacing_ok=true risk_mode=fixed_pct layer=%s",
                decision.signal,
                len(same_side_positions) + 1,
            )
        receipt = self.execution.execute_entry_plan(plan)
        if not receipt.ok:
            self.logger.warning("Execution failed | reason=%s", receipt.message)
            if receipt.order_result.position_ticket is not None:
                ticket = receipt.order_result.position_ticket
                self._register_runtime_position(
                    ticket=ticket,
                    side=decision.signal,
                    entry_price=float(receipt.order_result.fill_price or estimated_entry),
                    tp_price=0.0,
                    effective_tp_distance=decision.effective_tp_distance,
                    protection_verified=False,
                    recovery_mode="unprotected_entry",
                    recovery_last_error=receipt.message,
                    entry_m5_anchor=self.state.last_processed_m5_bar_time,
                )
                runtime = self.state.positions[ticket]
                if receipt.protection_result is not None and receipt.protection_result.failure_class is not None and receipt.protection_result.failure_class.value == "non_retryable":
                    runtime.recovery_mode = "unprotected_entry_invalid_after_fill"
                    runtime.recovery_escalated = True
                    self._activate_manual_ack_kill_switch(
                        reason=f"invalid_after_fill:{ticket}:{receipt.protection_result.message}"
                    )
                self.logger.error(
                    "UNPROTECTED LIVE POSITION DETECTED | ticket=%s side=%s reason=%s recovery=emergency_close",
                    ticket,
                    decision.signal,
                    receipt.message,
                )
                self._mark_execution_event(decision.signal)
                self._save_persistent_state()
            return

        if receipt.order_result.position_ticket is not None:
            ticket = receipt.order_result.position_ticket
            self._register_runtime_position(
                ticket=ticket,
                side=decision.signal,
                entry_price=float(receipt.order_result.fill_price or estimated_entry),
                tp_price=float(receipt.protection_result.tp_attached or 0.0) if receipt.protection_result else 0.0,
                effective_tp_distance=decision.effective_tp_distance,
                protection_verified=True,
                entry_m5_anchor=self.state.last_processed_m5_bar_time,
            )
            self._mark_execution_event(decision.signal)
            self.state.strategy_telemetry.executed_trades += 1
            self._save_persistent_state()

    def _refresh_drawdown_state(self, *, account_equity: float) -> None:
        now_local_date = datetime.now(timezone.utc).date()
        changed = False
        if self.state.current_trading_day != now_local_date:
            self.state.current_trading_day = now_local_date
            self.state.daily_baseline_equity = account_equity
            self.state.consecutive_losses = 0
            self.state.entry_pause_until = None
            self.state.entry_pause_deadline_monotonic = None
            self.state.entry_pause_reason = None
            self.state.soft_drawdown_tripped_today = False
            changed = True
            self.logger.info(
                "Drawdown day reset | trading_day=%s daily_baseline_equity=%.2f",
                now_local_date.isoformat(),
                account_equity,
            )

        self._process_operator_ack(account_equity=account_equity)
        if self.state.daily_baseline_equity is None:
            self.state.daily_baseline_equity = account_equity
            changed = True
        if self.state.session_peak_equity is None:
            self.state.session_peak_equity = account_equity
            changed = True
        else:
            new_peak = max(self.state.session_peak_equity, account_equity)
            if new_peak != self.state.session_peak_equity:
                self.state.session_peak_equity = new_peak
                changed = True

        daily_baseline = max(self.state.daily_baseline_equity or account_equity, 1e-9)
        session_peak = max(self.state.session_peak_equity or account_equity, 1e-9)
        daily_dd_pct = max(0.0, ((daily_baseline - account_equity) / daily_baseline) * 100.0)
        hard_dd_pct = max(0.0, ((session_peak - account_equity) / session_peak) * 100.0)

        if hard_dd_pct >= self.settings.equity_drawdown_hard_pct and not self.state.trading_disabled:
            self.state.trading_disabled = True
            self.state.trading_disabled_reason = (
                f"hard_drawdown:{hard_dd_pct:.2f}%>=threshold:{self.settings.equity_drawdown_hard_pct:.2f}%"
            )
            self.state.manual_ack_required = True
            self.state.manual_ack_reason = self.state.trading_disabled_reason
            self.state.manual_ack_timestamp = None
            changed = True
            self.logger.error(
                "Hard drawdown guard triggered | equity=%.2f peak=%.2f dd_pct=%.2f threshold=%.2f",
                account_equity,
                session_peak,
                hard_dd_pct,
                self.settings.equity_drawdown_hard_pct,
            )
            self.logger.error(
                "KILL_SWITCH_ACTIVE | reason=%s manual_ack_required=true ack_file=%s",
                self.state.manual_ack_reason,
                self._operator_ack_path,
            )

        if daily_dd_pct >= self.settings.daily_drawdown_soft_pct and not self.state.soft_drawdown_tripped_today:
            self.state.soft_drawdown_tripped_today = True
            self.state.entry_pause_reason = (
                f"soft_drawdown:{daily_dd_pct:.2f}%>=threshold:{self.settings.daily_drawdown_soft_pct:.2f}%"
            )
            changed = True
            self.logger.warning(
                "Soft drawdown pause activated | equity=%.2f baseline=%.2f dd_pct=%.2f threshold=%.2f",
                account_equity,
                daily_baseline,
                daily_dd_pct,
                self.settings.daily_drawdown_soft_pct,
            )
        elif self.state.soft_drawdown_tripped_today and not self.state.entry_pause_reason:
            self.state.entry_pause_reason = "soft_drawdown:persisted_today"
            changed = True

        if changed:
            self._save_persistent_state()

    def _enforce_hard_drawdown_guard(self) -> None:
        if not self.state.trading_disabled:
            return

        live_positions = self._get_live_bot_positions(self.settings.symbol)
        if not live_positions:
            return

        for pos in live_positions:
            ticket = int(pos.ticket)
            runtime = self.state.positions.get(ticket)
            if runtime is not None:
                runtime.expected_close = True
                runtime.close_reason = self.state.trading_disabled_reason or "hard_drawdown_guard"
            close_result = self.client.close_position(ticket=ticket, comment="hard_drawdown_guard")
            if close_result.ok:
                self.logger.error(
                    "Hard drawdown flatten | ticket=%s symbol=%s reason=%s",
                    ticket,
                    self.settings.symbol,
                    self.state.trading_disabled_reason or "hard_drawdown_guard",
                )

    def _register_loss_streak(self) -> None:
        self.state.consecutive_losses += 1
        if self.state.consecutive_losses >= self.settings.consecutive_loss_limit:
            pause_until = datetime.now(timezone.utc) + timedelta(minutes=self.settings.consecutive_loss_pause_minutes)
            self.state.entry_pause_until = pause_until
            self.state.entry_pause_deadline_monotonic = time.monotonic() + (self.settings.consecutive_loss_pause_minutes * 60)
            self.state.entry_pause_reason = (
                f"loss_streak:{self.state.consecutive_losses}_pause_{self.settings.consecutive_loss_pause_minutes}m"
            )
            self.logger.warning(
                "Loss streak pause activated | consecutive_losses=%s pause_until=%s",
                self.state.consecutive_losses,
                pause_until.isoformat(),
            )
        self._save_persistent_state()

    def _reset_loss_streak(self) -> None:
        if self.state.consecutive_losses != 0:
            self.logger.info("Loss streak reset | previous_consecutive_losses=%s", self.state.consecutive_losses)
        self.state.consecutive_losses = 0
        if self.state.entry_pause_reason and self.state.entry_pause_reason.startswith("loss_streak:"):
            self.state.entry_pause_reason = None
            self.state.entry_pause_until = None
            self.state.entry_pause_deadline_monotonic = None
        self._save_persistent_state()

    def build_setup(
        self,
        *,
        m1_bars: list[dict[str, float]],
        m5_bars: list[dict[str, float]],
        h1_bars: list[dict[str, float]],
        h4_bars: list[dict[str, float]],
        constraints: SymbolConstraints,
        htf_bias: str | None = None,
    ) -> SetupDecision:
        if htf_bias is None:
            htf_bias = self._compute_htf_bias(h1_bars, h4_bars)
        self._refresh_direction_locks(htf_bias=htf_bias, m5_bars=m5_bars, constraints=constraints)
        self._refresh_freshness_states(m5_bars=m5_bars, constraints=constraints)
        self._update_near_miss_outcomes(m5_bars=m5_bars)
        self._update_progress_exit_counterfactuals(m5_bars=m5_bars)

        m5_atr = calculate_atr(m5_bars[:-1], period=14)
        feasibility = build_tp_feasibility(
            atr=m5_atr,
            broker_min_tp_distance=constraints.min_stop_distance,
            tp_atr_mult=self.settings.tp_atr_mult,
            broker_buffer=self.settings.tp_broker_buffer,
            feasibility_buffer=self.settings.tp_feasibility_buffer,
        )

        last_m1 = m1_bars[-2]
        m1_atr = calculate_atr(m1_bars[:-1], period=14)
        buy_score, buy_reason = self._continuation_score(side="BUY", bars=m1_bars, atr=m1_atr)
        sell_score, sell_reason = self._continuation_score(side="SELL", bars=m1_bars, atr=m1_atr)
        buy_location_ok, buy_location_reason = self._location_quality(side="BUY", bars=m5_bars, atr=m5_atr)
        sell_location_ok, sell_location_reason = self._location_quality(side="SELL", bars=m5_bars, atr=m5_atr)
        buy_follow_score, buy_follow_label = self._follow_through_forecast(side="BUY", bars=m1_bars, htf_bias=htf_bias)
        sell_follow_score, sell_follow_label = self._follow_through_forecast(side="SELL", bars=m1_bars, htf_bias=htf_bias)

        context = {
            "htf_bias": htf_bias,
            "m5_atr": f"{m5_atr:.5f}",
            "effective_tp": f"{feasibility.effective_tp_distance:.5f}",
            "required_tp_progress": f"{feasibility.required_progress_distance:.5f}",
            "buy_score": f"{buy_score}/7",
            "sell_score": f"{sell_score}/7",
            "buy_follow": f"{buy_follow_label}:{buy_follow_score}/5",
            "sell_follow": f"{sell_follow_label}:{sell_follow_score}/5",
            "m1_close": f"{last_m1['close']:.5f}",
        }

        blockers: list[str] = []
        active_positions = self._get_live_bot_positions(self.settings.symbol)
        if self.state.manual_ack_required:
            blockers.append(f"manual_ack_required:{self.state.manual_ack_reason or self.state.trading_disabled_reason or 'active'}")
        if self.state.trading_disabled:
            blockers.append(f"hard_drawdown_guard:{self.state.trading_disabled_reason or 'active'}")
        if self._is_deadline_active(
            wall_clock_deadline=self.state.entry_pause_until,
            monotonic_deadline=self.state.entry_pause_deadline_monotonic,
        ):
            blockers.append(
                f"entry_pause:active_until_{self.state.entry_pause_until.isoformat()}"
            )
        elif self.state.entry_pause_reason:
            blockers.append(f"entry_pause:{self.state.entry_pause_reason}")
        if len(active_positions) >= self.settings.max_positions:
            blockers.append(f"position_cap:active_{len(active_positions)}_max_{self.settings.max_positions}")
        if self._is_deadline_active(
            wall_clock_deadline=self.state.manual_close_pause_until,
            monotonic_deadline=self.state.manual_close_pause_deadline_monotonic,
        ):
            blockers.append(
                f"manual_close_cooldown:active_until_{self.state.manual_close_pause_until.isoformat()}"
            )
        if htf_bias == "HOLD":
            blockers.append("htf_bias:HOLD_no_trade")
        if not feasibility.feasible:
            blockers.append(
                f"tp_feasibility:m5_atr_{m5_atr:.5f}_below_required_tp_progress_{feasibility.required_progress_distance:.5f}"
            )

        signal = None
        score = 0

        if htf_bias == "BUY":
            score = buy_score
            blockers.extend(self._entry_side_blockers("BUY", active_positions))
            if buy_score < 4:
                blockers.append("trigger:buy_score_below_4")
            if not buy_location_ok:
                blockers.append(f"location:{buy_location_reason}")
            if buy_follow_label == "LOW":
                blockers.append(f"follow:low({buy_follow_score}/5)")
            if (
                feasibility.feasible
                and buy_score >= 4
                and buy_location_ok
                and buy_follow_label != "LOW"
                and not any(b.startswith(("direction_lock:", "freshness:", "same_direction_active", "manual_close_cooldown:", "manual_ack_required:", "hard_drawdown_guard:", "entry_pause:")) for b in blockers)
            ):
                signal = "BUY"
        elif htf_bias == "SELL":
            score = sell_score
            blockers.extend(self._entry_side_blockers("SELL", active_positions))
            if sell_score < 4:
                blockers.append("trigger:sell_score_below_4")
            if not sell_location_ok:
                blockers.append(f"location:{sell_location_reason}")
            if sell_follow_label == "LOW":
                blockers.append(f"follow:low({sell_follow_score}/5)")
            if (
                feasibility.feasible
                and sell_score >= 4
                and sell_location_ok
                and sell_follow_label != "LOW"
                and not any(b.startswith(("direction_lock:", "freshness:", "same_direction_active", "manual_close_cooldown:", "manual_ack_required:", "hard_drawdown_guard:", "entry_pause:")) for b in blockers)
            ):
                signal = "SELL"

        self.logger.info(
            "SETUP DEBUG | htf_bias=%s buy(score=%s reason=%s loc=%s follow=%s) "
            "sell(score=%s reason=%s loc=%s follow=%s) effective_tp=%s",
            htf_bias,
            buy_score,
            buy_reason,
            buy_location_reason,
            f"{buy_follow_label}:{buy_follow_score}/5",
            sell_score,
            sell_reason,
            sell_location_reason,
            f"{sell_follow_label}:{sell_follow_score}/5",
            context["effective_tp"],
        )

        return SetupDecision(
            signal=signal,
            score=score,
            blockers=blockers,
            context=context,
            effective_tp_distance=feasibility.effective_tp_distance,
            m5_atr=m5_atr,
            buy_score=buy_score,
            sell_score=sell_score,
            buy_location_reason=buy_location_reason,
            sell_location_reason=sell_location_reason,
            buy_follow_label=buy_follow_label,
            sell_follow_label=sell_follow_label,
            htf_bias=htf_bias,
            reference_price=float(last_m1["close"]),
            tp_feasible=feasibility.feasible,
        )

    def _manage_live_positions(
        self,
        *,
        m5_bars: list[dict[str, float]],
        constraints: SymbolConstraints,
        current_htf_bias: str,
    ) -> None:
        live_positions = {int(pos.ticket): pos for pos in self._get_live_bot_positions(self.settings.symbol)}

        for ticket, runtime in list(self.state.positions.items()):
            live_position = live_positions.get(ticket)
            if live_position is None:
                self._handle_missing_position(ticket, runtime)
                continue

            if not runtime.protection_verified:
                live_tp = float(getattr(live_position, "tp", 0.0) or 0.0)
                if runtime.tp_price > 0 and live_tp > 0 and abs(live_tp - runtime.tp_price) <= max(constraints.point, 1e-9):
                    runtime.protection_verified = True
                    runtime.recovery_mode = None
                    runtime.recovery_attempts = 0
                    runtime.recovery_next_retry_at = None
                    runtime.recovery_next_retry_monotonic = None
                    runtime.recovery_last_error = None
                    self.logger.warning(
                        "Protection recovery confirmed | ticket=%s tp=%.5f",
                        ticket,
                        live_tp,
                    )
                else:
                    self._manage_unprotected_position(runtime=runtime, live_position=live_position)
                    continue

            tick = self.client.get_latest_tick(self.settings.symbol)
            if runtime.side == "BUY":
                current_distance = float(tick.bid) - runtime.entry_price
                favorable = max(0.0, current_distance)
            else:
                current_distance = runtime.entry_price - float(tick.ask)
                favorable = max(0.0, current_distance)
            runtime.max_favorable_distance = max(runtime.max_favorable_distance, favorable)

            if not runtime.be_applied:
                self._maybe_apply_break_even(
                    runtime=runtime,
                    live_position=live_position,
                    constraints=constraints,
                    current_distance=current_distance,
                )

            entry_anchor_m5 = self._ensure_runtime_entry_m5_anchor(runtime=runtime, m5_bars=m5_bars)
            if entry_anchor_m5 is None:
                continue

            closed_bars_since_entry = self._count_closed_m5_bars_since_anchor(
                anchor=entry_anchor_m5,
                m5_bars=m5_bars,
            )
            if closed_bars_since_entry < 2:
                continue

            required_progress = runtime.effective_tp_distance * 0.5
            entry_area_tolerance = max(constraints.point * 2, runtime.effective_tp_distance * 0.10)
            self.logger.info(
                'event="progress_exit_evaluation" ticket=%s entry_anchor_m5=%s closed_bars_since_entry=%s required_bars=2 progress=%.5f required_progress=%.5f current=%.5f',
                ticket,
                entry_anchor_m5.isoformat(),
                closed_bars_since_entry,
                runtime.max_favorable_distance,
                required_progress,
                current_distance,
            )

            if runtime.max_favorable_distance < required_progress:
                if self._attempt_runtime_close(
                    runtime=runtime,
                    ticket=ticket,
                    reason="progress_below_50pct_after_2_m5",
                    comment="progress_below_50pct_after_2_m5",
                    escalation_reason_prefix="position_close_recovery_failed",
                ):
                    self.logger.info(
                        'event="progress_exit_triggered" ticket=%s reason=%s entry_anchor_m5=%s closed_bars_since_entry=%s progress=%.5f required=%.5f current=%.5f',
                        ticket,
                        runtime.close_reason,
                        entry_anchor_m5.isoformat(),
                        closed_bars_since_entry,
                        runtime.max_favorable_distance,
                        required_progress,
                        current_distance,
                    )
                    self.logger.info(
                        "BOT POSITION time exit | ticket=%s reason=%s progress=%.5f required=%.5f current=%.5f",
                        ticket,
                        runtime.close_reason,
                        runtime.max_favorable_distance,
                        required_progress,
                        current_distance,
                    )
                    self._lock_direction_from_failure(runtime.side, m5_bars, current_htf_bias)
                    self._record_progress_exit_counterfactual(
                        runtime=runtime,
                        exit_price=float(tick.ask if runtime.side == "SELL" else tick.bid),
                        progress=runtime.max_favorable_distance,
                        required_progress=required_progress,
                        closed_bars_since_entry=closed_bars_since_entry,
                        direction_lock_reason="progress_below_50pct_after_2_m5",
                    )
                continue

            if abs(current_distance) <= entry_area_tolerance:
                if self._attempt_runtime_close(
                    runtime=runtime,
                    ticket=ticket,
                    reason="back_to_entry_area_after_2_m5",
                    comment="back_to_entry_area_after_2_m5",
                    escalation_reason_prefix="position_close_recovery_failed",
                ):
                    self.logger.info(
                        "BOT POSITION time exit | ticket=%s reason=%s current=%.5f tolerance=%.5f",
                        ticket,
                        runtime.close_reason,
                        current_distance,
                        entry_area_tolerance,
                    )
                    self._lock_direction_from_failure(runtime.side, m5_bars, current_htf_bias)

    def _register_runtime_position(
        self,
        *,
        ticket: int,
        side: str,
        entry_price: float,
        tp_price: float,
        effective_tp_distance: float,
        protection_verified: bool,
        entry_m5_anchor: datetime | None = None,
        recovery_mode: str | None = None,
        recovery_last_error: str | None = None,
    ) -> None:
        now_utc = datetime.now(timezone.utc)
        self.state.positions[ticket] = PositionRuntimeState(
            ticket=ticket,
            side=side,
            opened_at=now_utc,
            entry_price=entry_price,
            tp_price=tp_price,
            effective_tp_distance=effective_tp_distance,
            entry_m5_anchor=entry_m5_anchor,
            protection_verified=protection_verified,
            recovery_mode=recovery_mode,
            recovery_last_error=recovery_last_error,
        )
        self._arm_freshness_state(side)

    def _ensure_runtime_entry_m5_anchor(
        self,
        *,
        runtime: PositionRuntimeState,
        m5_bars: list[dict[str, float]],
    ) -> datetime | None:
        if runtime.entry_m5_anchor is not None:
            return runtime.entry_m5_anchor

        runtime.entry_m5_anchor = self._infer_entry_m5_anchor(
            opened_at=runtime.opened_at,
            m5_bars=m5_bars,
        )
        if runtime.entry_m5_anchor is None:
            self.logger.warning(
                'event="progress_exit_anchor_missing" ticket=%s opened_at=%s fallback=skip_progress_exit',
                runtime.ticket,
                runtime.opened_at.isoformat(),
            )
            return None

        self.logger.info(
            'event="entry_m5_anchor_inferred" ticket=%s entry_anchor_m5=%s opened_at=%s',
            runtime.ticket,
            runtime.entry_m5_anchor.isoformat(),
            runtime.opened_at.isoformat(),
        )
        return runtime.entry_m5_anchor

    def _infer_entry_m5_anchor(
        self,
        *,
        opened_at: datetime,
        m5_bars: list[dict[str, float]],
    ) -> datetime | None:
        closed_bars = m5_bars[:-1]
        candidates = [bar["time"] for bar in closed_bars if bar["time"] <= opened_at]
        if candidates:
            return candidates[-1]
        return self.state.last_processed_m5_bar_time

    @staticmethod
    def _count_closed_m5_bars_since_anchor(
        *,
        anchor: datetime,
        m5_bars: list[dict[str, float]],
    ) -> int:
        return sum(1 for bar in m5_bars[:-1] if bar["time"] > anchor)

    def _mark_execution_event(self, signal: str) -> None:
        now_utc = datetime.now(timezone.utc)
        self.state.last_execution_signal = signal
        self.state.last_execution_m5_bar_time = self.state.last_processed_m5_bar_time
        self.state.last_execution_at = now_utc
        self.state.last_execution_monotonic = time.monotonic()

    def _schedule_runtime_retry(self, runtime: PositionRuntimeState, *, delay_seconds: int, error_message: str) -> None:
        now_utc = datetime.now(timezone.utc)
        safe_delay = max(1, delay_seconds)
        runtime.recovery_last_error = error_message
        runtime.recovery_next_retry_at = now_utc + timedelta(seconds=safe_delay)
        runtime.recovery_next_retry_monotonic = time.monotonic() + safe_delay

    def _close_retry_backoff_seconds(self, attempt_number: int) -> int:
        schedule = self.settings.position_close_retry_backoff_schedule_seconds
        if not schedule:
            return 30
        index = max(0, min(attempt_number - 1, len(schedule) - 1))
        return max(1, int(schedule[index]))

    def _activate_manual_ack_kill_switch(self, *, reason: str) -> None:
        self.state.trading_disabled = True
        self.state.trading_disabled_reason = reason
        self.state.manual_ack_required = True
        self.state.manual_ack_reason = reason
        self._save_persistent_state()
        self.logger.error(
            "KILL_SWITCH_ACTIVE | reason=%s manual_ack_required=true ack_file=%s",
            reason,
            self._operator_ack_path,
        )

    def _attempt_runtime_close(
        self,
        *,
        runtime: PositionRuntimeState,
        ticket: int,
        reason: str,
        comment: str,
        escalation_reason_prefix: str,
    ) -> bool:
        if runtime.expected_close:
            return False
        if runtime.recovery_escalated:
            return False
        if runtime.recovery_next_retry_at is not None and self._is_deadline_active(
            wall_clock_deadline=runtime.recovery_next_retry_at,
            monotonic_deadline=runtime.recovery_next_retry_monotonic,
        ):
            return False

        close_result = self.client.close_position(ticket=ticket, comment=comment)
        if close_result.ok:
            runtime.expected_close = True
            runtime.close_reason = reason
            runtime.recovery_mode = "close_sent"
            runtime.recovery_attempts = 0
            runtime.recovery_next_retry_at = None
            runtime.recovery_next_retry_monotonic = None
            runtime.recovery_last_error = None
            return True

        runtime.close_reason = reason
        runtime.recovery_mode = "close_pending"
        runtime.recovery_attempts += 1
        runtime.recovery_last_error = close_result.comment
        limit = max(1, self.settings.position_close_retry_limit)
        if runtime.recovery_attempts >= limit:
            runtime.recovery_escalated = True
            escalation_reason = f"{escalation_reason_prefix}:{ticket}:{reason}"
            self._activate_manual_ack_kill_switch(reason=escalation_reason)
            self.logger.error(
                'event="kill_switch_escalated" ticket=%s reason=%s attempts_exhausted=%s last_retcode=%s last_comment=%s symbol=%s manual_ack_required=%s trading_disabled=%s',
                ticket,
                escalation_reason,
                runtime.recovery_attempts,
                close_result.retcode,
                close_result.comment,
                close_result.symbol,
                self.state.manual_ack_required,
                self.state.trading_disabled,
            )
            return False

        self._schedule_runtime_retry(
            runtime,
            delay_seconds=self._close_retry_backoff_seconds(runtime.recovery_attempts),
            error_message=close_result.comment,
        )
        self.logger.error(
            'event="emergency_close_failed" ticket=%s attempt=%s/%s retcode=%s classification=%s comment=%s position_side=%s close_side=%s price=%.5f filling=%s symbol=%s retryable=%s next_retry_at=%s',
            ticket,
            runtime.recovery_attempts,
            limit,
            close_result.retcode,
            close_result.failure_class.value if close_result.failure_class else "unknown",
            close_result.comment,
            close_result.position_side,
            close_result.close_side,
            close_result.price,
            close_result.filling_mode,
            close_result.symbol,
            close_result.retryable,
            runtime.recovery_next_retry_at.isoformat() if runtime.recovery_next_retry_at else "n/a",
        )
        self.logger.warning(
            'event="emergency_close_retry_scheduled" ticket=%s attempt=%s/%s next_retry_at=%s backoff_seconds=%s reason=%s',
            ticket,
            runtime.recovery_attempts,
            limit,
            runtime.recovery_next_retry_at.isoformat() if runtime.recovery_next_retry_at else "n/a",
            self._close_retry_backoff_seconds(runtime.recovery_attempts),
            reason,
        )
        return False

    def _manage_unprotected_position(self, *, runtime: PositionRuntimeState, live_position: Any) -> None:
        if runtime.recovery_escalated:
            return
        ticket = int(live_position.ticket)
        if runtime.recovery_next_retry_at is not None and self._is_deadline_active(
            wall_clock_deadline=runtime.recovery_next_retry_at,
            monotonic_deadline=runtime.recovery_next_retry_monotonic,
        ):
            return
        if self._attempt_runtime_close(
            runtime=runtime,
            ticket=ticket,
            reason="unprotected_entry_recovery",
            comment="unprotected_entry_recovery",
            escalation_reason_prefix="unprotected_entry_recovery_failed",
        ):
            self.logger.error(
                'event="emergency_close_sent" ticket=%s side=%s recovery_mode=%s',
                ticket,
                runtime.side,
                runtime.recovery_mode,
            )

    def _handle_missing_position(self, ticket: int, runtime: PositionRuntimeState) -> None:
        if runtime.expected_close:
            self.logger.info(
                "BOT POSITION exit confirmed | ticket=%s reason=%s",
                ticket,
                runtime.close_reason or "expected_bot_close",
            )
            if runtime.close_reason in {"progress_below_50pct_after_2_m5", "back_to_entry_area_after_2_m5"}:
                self._register_loss_streak()
            self.state.positions.pop(ticket, None)
            self._save_persistent_state()
            return

        tp_hit = runtime.max_favorable_distance >= runtime.effective_tp_distance * 0.98
        if tp_hit:
            self.logger.info(
                "Broker TP close detected | ticket=%s side=%s",
                ticket,
                runtime.side,
            )
            self._reset_loss_streak()
            self.state.positions.pop(ticket, None)
            self._save_persistent_state()
            return

        self.state.manual_close_pause_until = datetime.now(timezone.utc) + timedelta(
            seconds=self.settings.manual_close_cooldown_seconds
        )
        self.state.manual_close_pause_deadline_monotonic = time.monotonic() + self.settings.manual_close_cooldown_seconds
        self.logger.warning(
            "Manual/external close detected | ticket=%s side=%s cooldown_until=%s",
            ticket,
            runtime.side,
            self.state.manual_close_pause_until.isoformat(),
        )
        self.state.positions.pop(ticket, None)
        self._save_persistent_state()

    def _sync_runtime_positions(self, constraints: SymbolConstraints, *, m5_bars: list[dict[str, float]]) -> None:
        live_positions = self._get_live_bot_positions(self.settings.symbol)
        live_tickets = {int(pos.ticket) for pos in live_positions}

        for position in live_positions:
            ticket = int(position.ticket)
            if ticket in self.state.positions:
                continue
            opened_at = datetime.fromtimestamp(int(position.time), tz=timezone.utc)
            entry_price = float(position.price_open)
            tp_price = float(getattr(position, "tp", 0.0) or 0.0)
            effective_tp_distance = abs(tp_price - entry_price) if tp_price > 0 else max(
                constraints.min_stop_distance * self.settings.tp_broker_buffer,
                constraints.point,
            )
            side = "BUY" if int(position.type) == self.client.mt5.ORDER_TYPE_BUY else "SELL"
            protection_verified = tp_price > 0
            if not protection_verified:
                self.logger.warning(
                    'event="startup_unprotected_position_detected" ticket=%s symbol=%s side=%s has_tp=%s has_sl=%s recovery_mode=%s',
                    ticket,
                    self.settings.symbol,
                    side,
                    False,
                    float(getattr(position, "sl", 0.0) or 0.0) > 0,
                    True,
                )
            self.state.positions[ticket] = PositionRuntimeState(
                ticket=ticket,
                side=side,
                opened_at=opened_at,
                entry_price=entry_price,
                tp_price=tp_price,
                effective_tp_distance=effective_tp_distance,
                entry_m5_anchor=self._infer_entry_m5_anchor(opened_at=opened_at, m5_bars=m5_bars),
                be_applied=float(getattr(position, "sl", 0.0) or 0.0) > 0,
                protection_verified=protection_verified,
                recovery_mode=None if protection_verified else "unprotected_entry",
                recovery_last_error=None if protection_verified else "startup_detected_position_without_tp",
            )

        missing_tracked = [ticket for ticket in self.state.positions if ticket not in live_tickets]
        for ticket in missing_tracked:
            self._handle_missing_position(ticket, self.state.positions[ticket])

    def _entry_side_blockers(self, side: str, active_positions: list[Any]) -> list[str]:
        blockers: list[str] = []
        if self._is_direction_locked(side):
            blockers.append(f"direction_lock:locked_{side.lower()}_regime")
        same_side_count = sum(1 for pos in active_positions if self._position_side(pos) == side)
        opposite_side_count = sum(1 for pos in active_positions if self._position_side(pos) != side)
        if opposite_side_count > 0:
            blockers.append("layering:opposite_side_active")
        elif self.settings.layer_count <= 1 and same_side_count > 0:
            blockers.append("same_direction_active")
        elif same_side_count >= self.settings.max_layers_per_direction:
            blockers.append(f"max_layers_direction:{side.lower()}_{same_side_count}/{self.settings.max_layers_per_direction}")
        freshness = self.state.freshness.get(side)
        if freshness and freshness.armed:
            if not freshness.reset_detected:
                blockers.append(f"freshness:await_{side.lower()}_reset_reclaim_m5_ma7")
            else:
                blockers.append(f"freshness:await_{side.lower()}_reclaim_after_reset")
        return blockers

    def _refresh_direction_locks(
        self,
        *,
        htf_bias: str,
        m5_bars: list[dict[str, float]],
        constraints: SymbolConstraints,
    ) -> None:
        if not self.state.direction_locks:
            return

        now_utc = datetime.now(timezone.utc)
        m5_close = m5_bars[-2]["close"]
        failsafe_seconds = self.settings.lock_failsafe_minutes * 60
        for direction, lock in list(self.state.direction_locks.items()):
            lock_elapsed = self._seconds_since(
                wall_clock_at=lock.timestamp,
                monotonic_at=lock.monotonic_started_at,
            ) or 0.0
            if lock_elapsed >= failsafe_seconds:
                self.logger.warning(
                    "[UNLOCK] %s unlocked | reason=unlock_failsafe_timeout locked_at=%s timeout_min=%s",
                    direction,
                    lock.timestamp.isoformat(),
                    self.settings.lock_failsafe_minutes,
                )
                self.state.direction_locks.pop(direction, None)
                self._save_persistent_state()
                continue

            if direction == "BUY":
                if htf_bias == "SELL":
                    self.logger.info("[UNLOCK] BUY unlocked | reason=htf_opposite")
                    self.state.direction_locks.pop(direction, None)
                    self._save_persistent_state()
                    self._save_persistent_state()
                    self._save_persistent_state()
                    continue
                if m5_close < lock.ref_low - lock.buffer:
                    self.logger.info("[UNLOCK] BUY unlocked | reason=unlock_structure_break")
                    self.state.direction_locks.pop(direction, None)
            elif direction == "SELL":
                if htf_bias == "BUY":
                    self.logger.info("[UNLOCK] SELL unlocked | reason=htf_opposite")
                    self.state.direction_locks.pop(direction, None)
                    self._save_persistent_state()
                    continue
                if m5_close > lock.ref_high + lock.buffer:
                    self.logger.info("[UNLOCK] SELL unlocked | reason=unlock_structure_break")
                    self.state.direction_locks.pop(direction, None)

    def _refresh_freshness_states(
        self,
        *,
        m5_bars: list[dict[str, float]],
        constraints: SymbolConstraints,
    ) -> None:
        if not self.state.freshness:
            return

        current_bar = m5_bars[-2]
        closes = [bar["close"] for bar in m5_bars[:-1]]
        ma7 = calculate_ma7(closes)
        buffer = max(self._pips_to_price(self.settings.freshness_buffer_pips, constraints), constraints.spread_price * self.settings.lock_buffer_spread_factor)

        for direction, state in self.state.freshness.items():
            if not state.armed:
                continue

            if state.reset_detected and state.reset_bar_time is not None:
                bars_since_reset = [
                    bar for bar in m5_bars[:-1]
                    if bar["time"].replace(tzinfo=timezone.utc) > state.reset_bar_time
                ]
                if len(bars_since_reset) > self.settings.freshness_reset_expiry_bars:
                    state.reset_detected = False
                    state.reset_bar_time = None

            if not state.reset_detected:
                if direction == "BUY" and current_bar["close"] <= ma7 + buffer:
                    state.reset_detected = True
                    state.reset_bar_time = current_bar["time"].replace(tzinfo=timezone.utc)
                    self.logger.info(
                        "[FRESHNESS] reset detected | dir=BUY | bar=%s",
                        state.reset_bar_time.isoformat(),
                    )
                elif direction == "SELL" and current_bar["close"] >= ma7 - buffer:
                    state.reset_detected = True
                    state.reset_bar_time = current_bar["time"].replace(tzinfo=timezone.utc)
                    self.logger.info(
                        "[FRESHNESS] reset detected | dir=SELL | bar=%s",
                        state.reset_bar_time.isoformat(),
                    )
                continue

            if state.reset_bar_time is None or current_bar["time"].replace(tzinfo=timezone.utc) <= state.reset_bar_time:
                continue

            if direction == "BUY" and current_bar["close"] > ma7:
                state.armed = False
                state.reset_detected = False
                state.reset_bar_time = None
                self.logger.info(
                    "[FRESHNESS] reclaim confirmed | dir=BUY | bar=%s",
                    current_bar["time"].replace(tzinfo=timezone.utc).isoformat(),
                )
            elif direction == "SELL" and current_bar["close"] < ma7:
                state.armed = False
                state.reset_detected = False
                state.reset_bar_time = None
                self.logger.info(
                    "[FRESHNESS] reclaim confirmed | dir=SELL | bar=%s",
                    current_bar["time"].replace(tzinfo=timezone.utc).isoformat(),
                )

    def _lock_direction_from_failure(
        self,
        direction: str,
        m5_bars: list[dict[str, float]],
        htf_bias: str,
    ) -> None:
        if direction in self.state.direction_locks:
            return
        closes = [bar["close"] for bar in m5_bars[:-1]]
        lookback_bars = m5_bars[-(self.settings.lock_structure_lookback + 1):-1]
        ref_high = max(bar["high"] for bar in lookback_bars)
        ref_low = min(bar["low"] for bar in lookback_bars)
        constraints = self.client.get_symbol_constraints(self.settings.symbol)
        buffer = max(
            self._pips_to_price(self.settings.lock_buffer_pips, constraints),
            constraints.spread_price * self.settings.lock_buffer_spread_factor,
        )
        self.state.direction_locks[direction] = DirectionLockState(
            direction=direction,
            htf_bias=htf_bias,
            ref_high=ref_high,
            ref_low=ref_low,
            timestamp=datetime.now(timezone.utc),
            buffer=buffer,
            monotonic_started_at=time.monotonic(),
        )
        self.logger.info(
            "[LOCK] %s locked | ref_high=%.5f ref_low=%.5f buffer=%.5f",
            direction,
            ref_high,
            ref_low,
            buffer,
        )
        self._save_persistent_state()

    def _arm_freshness_state(self, direction: str) -> None:
        state = self.state.freshness.get(direction)
        if state is None:
            state = FreshnessState(direction=direction)
            self.state.freshness[direction] = state
        state.armed = True
        state.reset_detected = False
        state.reset_bar_time = None

    def _is_direction_locked(self, side: str) -> bool:
        return side in self.state.direction_locks

    @staticmethod
    def _position_side(position: Any) -> str:
        return "BUY" if int(position.type) == 0 else "SELL"

    @staticmethod
    def _has_same_direction_active(side: str, active_positions: list[Any]) -> bool:
        for pos in active_positions:
            pos_side = MT5TradingBot._position_side(pos)
            if pos_side == side:
                return True
        return False

    def _get_live_bot_positions(self, symbol: str) -> list[Any]:
        positions = self.client.positions_get(symbol=symbol)
        return [
            pos for pos in positions
            if int(getattr(pos, "magic", -1)) == self.settings.magic_number
        ]

    def _compute_htf_bias(
        self,
        h1_bars: list[dict[str, float]],
        h4_bars: list[dict[str, float]],
        *,
        emit_log: bool,
    ) -> str:
        h1_closes = [bar["close"] for bar in h1_bars[:-1]]
        h4_closes = [bar["close"] for bar in h4_bars[:-1]]
        h1_close = h1_closes[-1]
        h4_close = h4_closes[-1]
        h1_ma7 = calculate_ma7(h1_closes)
        h4_ma7 = calculate_ma7(h4_closes)
        h1_rsi = calculate_rsi(h1_closes)
        h4_rsi = calculate_rsi(h4_closes)

        if emit_log:
            self.logger.info(
                "HTF DEBUG | H1 close=%.5f ma7=%.5f rsi=%.2f | H4 close=%.5f ma7=%.5f rsi=%.2f",
                h1_close,
                h1_ma7,
                h1_rsi,
                h4_close,
                h4_ma7,
                h4_rsi,
            )

        if h1_close > h1_ma7 and h4_close > h4_ma7 and h1_rsi >= 50 and h4_rsi >= 50:
            return "BUY"
        if h1_close < h1_ma7 and h4_close < h4_ma7 and h1_rsi <= 50 and h4_rsi <= 50:
            return "SELL"
        return "HOLD"

    def _continuation_score(
        self,
        *,
        side: str,
        bars: list[dict[str, float]],
        atr: float,
    ) -> tuple[int, str]:
        current = bars[-2]
        previous = bars[-3]
        older = bars[-4]
        stats = candle_stats(current)
        ma7 = calculate_ma7([bar["close"] for bar in bars[:-1]])
        close = current["close"]
        high = current["high"]
        low = current["low"]

        if side == "BUY":
            checks = {
                "structure": close > previous["close"] and low >= older["low"],
                "body_strength": stats.body >= stats.range_size * 0.45,
                "close_near_extreme": (high - close) <= stats.range_size * 0.25,
                "wick_quality": stats.upper_wick <= max(stats.body * 1.2, atr * 0.20),
                "atr_expansion": stats.range_size >= atr * 0.80,
                "no_extreme_rejection": stats.upper_wick <= stats.body * 1.5 if stats.body > 0 else False,
                "not_overextended": close <= ma7 + (atr * 1.5),
            }
        else:
            checks = {
                "structure": close < previous["close"] and high <= older["high"],
                "body_strength": stats.body >= stats.range_size * 0.45,
                "close_near_extreme": (close - low) <= stats.range_size * 0.25,
                "wick_quality": stats.lower_wick <= max(stats.body * 1.2, atr * 0.20),
                "atr_expansion": stats.range_size >= atr * 0.80,
                "no_extreme_rejection": stats.lower_wick <= stats.body * 1.5 if stats.body > 0 else False,
                "not_overextended": close >= ma7 - (atr * 1.5),
            }

        score = sum(1 for passed in checks.values() if passed)
        reason = ",".join(name for name, passed in checks.items() if passed)
        return score, reason or "none"

    def _location_quality(
        self,
        *,
        side: str,
        bars: list[dict[str, float]],
        atr: float,
    ) -> tuple[bool, str]:
        current = bars[-2]
        closes = [bar["close"] for bar in bars[:-1]]
        ma7 = calculate_ma7(closes)
        stats = candle_stats(current)
        close = current["close"]
        open_ = current["open"]
        buffer = max(atr * 0.10, 1e-6)

        if side == "BUY":
            if close <= ma7:
                return False, "close_below_ma7"
            if open_ < ma7 < close and stats.body >= stats.range_size * 0.60:
                return False, "body_crosses_ma7"
            distance = close - ma7
            if distance <= buffer and stats.lower_wick < max(stats.body * 0.5, atr * 0.10):
                return False, "too_close_to_ma7_without_rejection"
            return True, "ok"

        if close >= ma7:
            return False, "close_above_ma7"
        if open_ > ma7 > close and stats.body >= stats.range_size * 0.60:
            return False, "body_crosses_ma7"
        distance = ma7 - close
        if distance <= buffer and stats.upper_wick < max(stats.body * 0.5, atr * 0.10):
            return False, "too_close_to_ma7_without_rejection"
        return True, "ok"

    def _follow_through_forecast(
        self,
        *,
        side: str,
        bars: list[dict[str, float]],
        htf_bias: str,
    ) -> tuple[int, str]:
        current = bars[-2]
        closes = [bar["close"] for bar in bars[:-1]]
        ma7_now = calculate_ma7(closes)
        ma7_prev = calculate_ma7(closes[:-1])
        stats = candle_stats(current)
        score = 0

        if stats.body >= stats.range_size * 0.50:
            score += 1

        if side == "BUY":
            if (current["high"] - current["close"]) <= stats.range_size * 0.25:
                score += 1
            if stats.upper_wick <= max(stats.body, 1e-9):
                score += 1
            if ma7_now >= ma7_prev:
                score += 1
            if htf_bias == "BUY":
                score += 1
        else:
            if (current["close"] - current["low"]) <= stats.range_size * 0.25:
                score += 1
            if stats.lower_wick <= max(stats.body, 1e-9):
                score += 1
            if ma7_now <= ma7_prev:
                score += 1
            if htf_bias == "SELL":
                score += 1

        if score >= 4:
            return score, "HIGH"
        if score == 3:
            return score, "MEDIUM"
        return score, "LOW"

    @staticmethod
    def _normalize_rates(rates) -> list[dict[str, float]]:
        bars: list[dict[str, float]] = []
        for row in rates:
            bars.append(
                {
                    "time": datetime.fromtimestamp(int(row["time"]), tz=timezone.utc),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "tick_volume": float(row["tick_volume"]),
                }
            )
        return bars

    @staticmethod
    def _pips_to_price(pips: float, constraints: SymbolConstraints) -> float:
        pip_size = constraints.point * 10 if constraints.digits in {3, 5} else constraints.point
        return pips * pip_size

    def _maybe_apply_break_even(
        self,
        *,
        runtime: PositionRuntimeState,
        live_position,
        constraints: SymbolConstraints,
        current_distance: float,
    ) -> None:
        trigger_distance = runtime.effective_tp_distance * 0.80
        if runtime.max_favorable_distance < trigger_distance:
            return

        entry_buffer = max(constraints.point * 2, runtime.effective_tp_distance * 0.05)
        if runtime.side == "BUY":
            new_sl = runtime.entry_price + entry_buffer
            distance_to_bid = float(self.client.get_latest_tick(self.settings.symbol).bid) - new_sl
        else:
            new_sl = runtime.entry_price - entry_buffer
            distance_to_bid = new_sl - float(self.client.get_latest_tick(self.settings.symbol).ask)

        min_required = max(constraints.min_stop_distance, constraints.spread_price)
        if distance_to_bid < min_required:
            self.logger.info(
                "BOT POSITION BE delayed | ticket=%s side=%s current=%.5f new_sl=%.5f min_required=%.5f",
                runtime.ticket,
                runtime.side,
                current_distance,
                new_sl,
                min_required,
            )
            return

        result = self.client.modify_position_protection(
            symbol=self.settings.symbol,
            position_ticket=runtime.ticket,
            sl=new_sl,
            tp=runtime.tp_price,
            comment="SET_BREAK_EVEN",
        )
        if result.ok:
            runtime.be_applied = True


