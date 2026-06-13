"""Forward-test deployment tooling for TSP V1."""

from __future__ import annotations

from argparse import ArgumentParser
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import time
from types import SimpleNamespace
from typing import Any

from mt5_bot.config import Settings
from mt5_bot.mt5_client import MT5Client, TradeFailureClass

from .bot import TSPBot
from .config import AppConfig, load_config
from .data_pipeline import SnapshotBuildConfig, build_market_snapshot


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on", "y"}


@dataclass(frozen=True, slots=True)
class DeploymentSummary:
    mode: str
    started_at: str
    ended_at: str
    dry_run: bool
    iterations: int
    bars_processed: int
    signals_generated: int
    executions_attempted: int
    executions_filled: int
    last_execution_status: str | None
    log_file: str
    report_file: str


@dataclass(frozen=True, slots=True)
class BrokerClockProfile:
    raw_server_time: datetime
    normalized_server_time: datetime
    offset_hours: int
    residual_seconds: float


class SingleInstanceLock:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._fd: int | None = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(
                f"Single-instance guard active: lock file already exists at {self.lock_path}"
            ) from exc
        payload = json.dumps(
            {"pid": os.getpid(), "created_at": _utcnow().isoformat()},
            sort_keys=True,
        ).encode("utf-8")
        os.write(self._fd, payload)

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        with suppress(FileNotFoundError):
            self.lock_path.unlink()

    def __enter__(self) -> SingleInstanceLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class DeploymentGuardrails:
    MAX_CLOCK_SKEW_SECONDS = 300

    @classmethod
    def validate(
        cls,
        *,
        config: AppConfig,
        server_time: datetime,
        symbol_info: Any,
    ) -> None:
        cls._validate_symbol(config)
        cls._validate_paths(config)
        cls._validate_credentials(config)
        cls._validate_clock(server_time)
        cls._validate_symbol_contract(symbol_info)

    @staticmethod
    def _validate_symbol(config: AppConfig) -> None:
        if config.supported_symbol != "XAUUSD":
            raise RuntimeError("Deployment guardrail rejected non-XAUUSD symbol")

    @staticmethod
    def _validate_paths(config: AppConfig) -> None:
        if config.bot.db_path is None or config.bot.log_dir is None or config.bot.state_dir is None:
            raise RuntimeError("Deployment paths must be configured before forward-test startup")
        for path in (config.bot.log_dir, config.bot.state_dir, config.bot.db_path.parent):
            path.mkdir(parents=True, exist_ok=True)
        if config.bot.db_path.suffix.lower() not in {".sqlite3", ".db"}:
            raise RuntimeError(f"DB path must end in .sqlite3 or .db: {config.bot.db_path}")
        if config.bot.db_path.is_dir():
            raise RuntimeError(f"DB path points to a directory: {config.bot.db_path}")

    @staticmethod
    def _validate_credentials(config: AppConfig) -> None:
        if not config.credentials.login.strip():
            raise RuntimeError("TSP_MT5_LOGIN must be set for forward deployment")
        if not config.credentials.password.strip():
            raise RuntimeError("TSP_MT5_PASSWORD must be set for forward deployment")
        if not config.credentials.server.strip():
            raise RuntimeError("TSP_MT5_SERVER must be set for forward deployment")
        if config.credentials.terminal_path and not Path(config.credentials.terminal_path).exists():
            raise RuntimeError(
                f"TSP_MT5_TERMINAL_PATH does not exist: {config.credentials.terminal_path}"
            )

    @classmethod
    def _validate_clock(cls, server_time: datetime) -> None:
        local = _utcnow()
        skew = abs((local - server_time).total_seconds())
        if skew > cls.MAX_CLOCK_SKEW_SECONDS:
            raise RuntimeError(
                f"Clock sanity check failed: MT5 server/local skew {skew:.1f}s exceeds {cls.MAX_CLOCK_SKEW_SECONDS}s"
            )

    @staticmethod
    def _validate_symbol_contract(symbol_info: Any) -> None:
        digits = int(getattr(symbol_info, "digits", 0) or 0)
        point = float(getattr(symbol_info, "point", 0.0) or 0.0)
        tick_size = float(getattr(symbol_info, "trade_tick_size", point) or 0.0)
        volume_step = float(getattr(symbol_info, "volume_step", 0.0) or 0.0)
        if digits <= 0:
            raise RuntimeError("Broker symbol contract invalid: digits must be positive")
        if point <= 0.0 or tick_size <= 0.0:
            raise RuntimeError("Broker symbol contract invalid: point/tick_size must be positive")
        if volume_step <= 0.0:
            raise RuntimeError("Broker symbol contract invalid: volume_step must be positive")


class TSPMT5Adapter:
    def __init__(
        self,
        *,
        client: MT5Client,
        config: AppConfig,
        execute_orders: bool,
        clock_profile: BrokerClockProfile,
    ) -> None:
        self.client = client
        self.config = config
        self.execute_orders = execute_orders
        self.clock_profile = clock_profile

    def get_rates(self, symbol: str, timeframe: str, count: int):
        raw_rates = self.client.get_rates(symbol, timeframe, count)
        normalized: list[dict[str, Any]] = []
        for record in raw_rates:
            normalized.append(
                {
                    "time": self._normalize_timestamp(float(record["time"])),
                    "open": float(record["open"]),
                    "high": float(record["high"]),
                    "low": float(record["low"]),
                    "close": float(record["close"]),
                    "tick_volume": float(record["tick_volume"]),
                }
            )
        return normalized

    def get_latest_tick(self, symbol: str):
        tick = self.client.get_latest_tick(symbol)
        tick_dict = {
            name: getattr(tick, name)
            for name in dir(tick)
            if not name.startswith("_") and not callable(getattr(tick, name))
        }
        if "time" in tick_dict:
            tick_dict["time"] = self._normalize_timestamp(float(tick_dict["time"])).timestamp()
        if "time_msc" in tick_dict:
            normalized_dt = self._normalize_timestamp(float(tick_dict["time_msc"]) / 1000.0)
            tick_dict["time_msc"] = int(normalized_dt.timestamp() * 1000)
        return SimpleNamespace(**tick_dict)

    def get_symbol_info(self, symbol: str):
        return self.client.get_symbol_info(symbol)

    def get_server_time(self) -> datetime:
        tick = self.client.get_latest_tick(self.config.supported_symbol)
        tick_time = getattr(tick, "time", None)
        if tick_time is None:
            return self.clock_profile.normalized_server_time
        return self._normalize_timestamp(float(tick_time))

    def get_equity(self) -> float:
        return float(self.client.get_account_info().equity)

    def send_market_order(
        self,
        symbol: str,
        action: str,
        volume: float,
        sl: float,
        tp: float | None,
        comment: str,
        magic: int,
    ) -> dict[str, Any]:
        del magic
        if not self.execute_orders:
            return {
                "retcode": 10030,
                "order": None,
                "deal": None,
                "price": None,
                "volume": None,
            }
        result = self.client.send_market_order(symbol=symbol, side=action, volume=volume, comment=comment)
        response = {
            "retcode": result.retcode,
            "order": result.order_ticket,
            "deal": result.position_ticket,
            "price": result.fill_price,
            "volume": result.filled_volume,
        }
        position_ticket = result.position_ticket
        if result.ok and position_ticket is not None and (sl > 0.0 or (tp is not None and tp > 0.0)):
            protection = self.client.modify_position_protection(
                symbol=symbol,
                position_ticket=position_ticket,
                sl=sl,
                tp=tp or 0.0,
                comment="SET_PROTECTION",
            )
            response["sl_confirmed"] = protection.ok if sl > 0.0 else True
            response["tp_confirmed"] = protection.ok if tp is not None and tp > 0.0 else True
        return response

    def modify_position(self, ticket: int, sl: float | None, tp: float | None) -> dict[str, Any]:
        if not self.execute_orders:
            return {"retcode": 10030, "sl_confirmed": False, "tp_confirmed": False}
        result = self.client.modify_position_protection(
            symbol=self.config.supported_symbol,
            position_ticket=ticket,
            sl=sl or 0.0,
            tp=tp or 0.0,
            comment="SET_PROTECTION",
        )
        return {
            "retcode": result.retcode,
            "sl_confirmed": result.ok if sl is not None else True,
            "tp_confirmed": result.ok if tp is not None else True,
        }

    def partial_close(self, ticket: int, symbol: str, volume: float, comment: str) -> dict[str, Any]:
        if not self.execute_orders:
            return {"retcode": 10030, "volume_executed": 0.0}
        position = self.get_position_by_ticket(ticket)
        if position is None:
            return {"retcode": 10030, "volume_executed": 0.0}
        full_volume = float(position["volume"])
        if volume >= full_volume:
            result = self.client.close_position(ticket=ticket, comment=comment)
            return {
                "retcode": result.retcode,
                "volume_executed": full_volume if result.ok else 0.0,
            }

        mt5 = self.client.mt5
        live_positions = self.client.positions_get(ticket=ticket)
        if not live_positions:
            return {"retcode": 10030, "volume_executed": 0.0}
        live = live_positions[0]
        tick = self.client.get_latest_tick(symbol)
        close_side = "SELL" if int(live.type) == mt5.ORDER_TYPE_BUY else "BUY"
        price = float(tick.bid if close_side == "SELL" else tick.ask)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "position": ticket,
            "magic": self.config.bot.magic_number,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_SELL if close_side == "SELL" else mt5.ORDER_TYPE_BUY,
            "price": price,
            "deviation": 20,
            "type_time": mt5.ORDER_TIME_GTC,
            "comment": comment[:16],
        }
        result = None
        for filling in self.client._candidate_filling_modes(symbol):
            candidate = dict(request)
            candidate["type_filling"] = filling
            result = mt5.order_send(candidate)
            if result is not None and getattr(result, "retcode", None) == mt5.TRADE_RETCODE_DONE:
                break
        return {
            "retcode": getattr(result, "retcode", 10030) if result is not None else 10030,
            "volume_executed": float(volume) if result is not None and getattr(result, "retcode", None) == mt5.TRADE_RETCODE_DONE else 0.0,
        }

    def get_position_by_ticket(self, ticket: int) -> dict[str, Any] | None:
        positions = self.client.positions_get(ticket=ticket)
        if not positions:
            return None
        return self._position_to_dict(positions[0])

    def get_all_positions(self, magic: int) -> list[dict[str, Any]]:
        return [
            self._position_to_dict(position)
            for position in self.client.positions_get(symbol=self.config.supported_symbol)
            if int(getattr(position, "magic", -1)) == magic
        ]

    def emergency_close(self, ticket: int, symbol: str, volume: float, reason: str) -> dict[str, Any]:
        position = self.get_position_by_ticket(ticket)
        if position is None:
            return {"retcode": 10030}
        if volume >= float(position["volume"]):
            result = self.client.close_position(ticket=ticket, comment=reason)
            return {"retcode": result.retcode}
        partial = self.partial_close(ticket, symbol, volume, reason)
        return {"retcode": partial["retcode"]}

    def _position_to_dict(self, position: Any) -> dict[str, Any]:
        return {
            "ticket": int(getattr(position, "ticket")),
            "symbol": str(getattr(position, "symbol")),
            "volume": float(getattr(position, "volume")),
            "sl": float(getattr(position, "sl", 0.0) or 0.0),
            "tp": float(getattr(position, "tp", 0.0) or 0.0),
            "magic": int(getattr(position, "magic", 0) or 0),
            "type": int(getattr(position, "type", 0) or 0),
        }

    def _normalize_timestamp(self, epoch_seconds: float) -> datetime:
        raw = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        return raw - timedelta(hours=self.clock_profile.offset_hours)


def _build_legacy_settings(config: AppConfig) -> Settings:
    return Settings(
        login=config.credentials.login,
        password=config.credentials.password,
        server=config.credentials.server,
        terminal_path=config.credentials.terminal_path,
        symbol=config.supported_symbol,
        asset_mode="metal",
        enable_order_execution=_env_bool("TSP_ENABLE_LIVE_EXECUTION", False),
        magic_number=config.bot.magic_number,
        order_deviation_points=20,
        poll_interval_seconds=config.bot.poll_interval_seconds,
        bars_fetch_count=max(250, SnapshotBuildConfig().h1_bars * 2),
        layer_count=1,
        max_positions=2,
        max_layers_per_direction=2,
        layer_spacing_atr_mult=0.5,
        min_seconds_between_entries=60,
        max_lot_per_order=1.0,
        total_setup_risk_pct=0.01,
        tp_atr_mult=0.3,
        tp_broker_buffer=1.2,
        tp_feasibility_buffer=1.1,
        lock_structure_lookback=5,
        lock_failsafe_minutes=180,
        lock_buffer_pips=0.2,
        lock_buffer_spread_factor=0.1,
        freshness_buffer_pips=0.2,
        freshness_reset_expiry_bars=5,
        manual_close_cooldown_seconds=180,
        min_entry_interval_seconds=60,
        protection_attach_retry_count=3,
        protection_attach_retry_delay_seconds=2,
        position_close_retry_limit=5,
        position_close_retry_backoff_schedule_seconds=(5, 10, 20, 40, 60),
        daily_drawdown_soft_pct=3.0,
        equity_drawdown_hard_pct=5.0,
        consecutive_loss_limit=3,
        consecutive_loss_pause_minutes=120,
        strategy_near_miss_score_min=5,
        strategy_near_miss_sample_limit=100,
        progress_exit_counterfactual_limit=50,
        log_level=os.getenv("TSP_LOG_LEVEL", "INFO"),
    )


def _configure_logger(log_dir: Path, *, mode: str) -> tuple[logging.Logger, Path]:
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = _utcnow().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"tsp_{mode}_{stamp}.log"
    logger = logging.getLogger(f"tsp.deploy.{mode}")
    logger.setLevel(getattr(logging, os.getenv("TSP_LOG_LEVEL", "INFO").upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger, log_path


def resolve_broker_clock_profile(
    *,
    raw_server_time: datetime,
    now_utc: datetime | None = None,
    max_skew_seconds: int = DeploymentGuardrails.MAX_CLOCK_SKEW_SECONDS,
) -> BrokerClockProfile:
    current = now_utc or _utcnow()
    skew_seconds = (raw_server_time - current).total_seconds()
    if abs(skew_seconds) <= max_skew_seconds:
        return BrokerClockProfile(
            raw_server_time=raw_server_time,
            normalized_server_time=raw_server_time,
            offset_hours=0,
            residual_seconds=abs(skew_seconds),
        )

    offset_hours = int(round(skew_seconds / 3600.0))
    normalized = raw_server_time - timedelta(hours=offset_hours)
    residual = abs((normalized - current).total_seconds())
    if residual > max_skew_seconds:
        return BrokerClockProfile(
            raw_server_time=raw_server_time,
            normalized_server_time=raw_server_time,
            offset_hours=0,
            residual_seconds=abs(skew_seconds),
        )
    return BrokerClockProfile(
        raw_server_time=raw_server_time,
        normalized_server_time=normalized,
        offset_hours=offset_hours,
        residual_seconds=residual,
    )


def _write_last_known_good(config: AppConfig) -> None:
    target = config.bot.config_last_known_good_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(config.config_path, target)


def _write_summary(report_dir: Path, summary: DeploymentSummary) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{summary.mode}_{summary.started_at.replace(':', '').replace('-', '')}_summary.json"
    path.write_text(json.dumps(asdict(summary), indent=2, sort_keys=True), encoding="utf-8")
    return path


def run_deployment(
    *,
    config_path: Path,
    env_path: Path,
    mode: str,
    dry_run: bool,
    max_loops: int | None,
) -> DeploymentSummary:
    config = load_config(path=config_path, env_path=env_path)
    logger, log_path = _configure_logger(config.bot.log_dir, mode=mode)
    report_dir = Path.cwd() / "reports"
    lock = SingleInstanceLock(config.bot.state_dir / f"tsp_{mode}.lock")
    started = _utcnow()
    iterations = 0
    bars_processed = 0
    signals_generated = 0
    executions_attempted = 0
    executions_filled = 0
    last_execution_status: str | None = None

    with lock:
        settings = _build_legacy_settings(config)
        client = MT5Client(settings=settings, logger=logger)
        try:
            client.initialize()
            raw_server_time = client.get_server_time()
            clock_profile = resolve_broker_clock_profile(raw_server_time=raw_server_time)
            logger.info(
                "Broker clock profile resolved | raw=%s | normalized=%s | offset_hours=%s | residual_seconds=%.1f",
                raw_server_time.isoformat(),
                clock_profile.normalized_server_time.isoformat(),
                clock_profile.offset_hours,
                clock_profile.residual_seconds,
            )
            symbol_info = client.get_symbol_info(config.supported_symbol)
            DeploymentGuardrails.validate(
                config=config,
                server_time=clock_profile.normalized_server_time,
                symbol_info=symbol_info,
            )
            _write_last_known_good(config)
            adapter = TSPMT5Adapter(
                client=client,
                config=config,
                execute_orders=not dry_run and _env_bool("TSP_ENABLE_LIVE_EXECUTION", False),
                clock_profile=clock_profile,
            )
            if dry_run:
                build_market_snapshot(
                    adapter,
                    symbol=config.supported_symbol,
                    cfg=SnapshotBuildConfig(),
                    server_time=clock_profile.normalized_server_time,
                )
                logger.info(
                    "Forward-test dry-run completed | symbol=%s | normalized_server_time=%s",
                    config.supported_symbol,
                    clock_profile.normalized_server_time.isoformat(),
                )
            else:
                bot = TSPBot(config=config, adapter=adapter, snapshot_config=SnapshotBuildConfig())
                while True:
                    result = bot.process_bar()
                    iterations += 1
                    bars_processed += int(result.processed_new_bar)
                    signals_generated += int(result.signal_generated)
                    if result.execution_status is not None:
                        executions_attempted += 1
                        last_execution_status = result.execution_status
                        if result.executed:
                            executions_filled += 1
                    raw = result.regime_raw_scores
                    diag = result.regime_diagnostics
                    logger.info(
                        "Forward-test heartbeat | iteration=%s | processed_new_bar=%s | duplicate_bar_skip=%s | bar_timestamp=%s | regime=%s | regime_confidence=%s | regime_conflict=%s | governor=%s | signal=%s | executed=%s | execution_status=%s | trend_candidate=%s | trend_direction_bias=%s | trend_fail_reason=%s | trend_composite=%s | trend_strength_threshold=%s | trend_adx_h1=%s | trend_adx_m15=%s | trend_adx_threshold=%s | trend_h1_slope_norm=%s | trend_m15_slope_norm=%s | trend_htf_alignment=%s | breakout_candidate=%s | breakout_direction=%s | breakout_fail_reason=%s | breakout_compression_ratio=%s | breakout_compression_threshold=%s | breakout_atr_ratio_m1=%s | breakout_burst_threshold=%s | breakout_secondary_count=%s | breakout_secondary_required=%s | spread_ratio=%s",
                        iterations,
                        result.processed_new_bar,
                        result.duplicate_bar_skip,
                        result.bar_timestamp.isoformat() if result.bar_timestamp is not None else None,
                        result.regime.name,
                        f"{result.regime_confidence:.3f}" if result.regime_confidence is not None else None,
                        result.regime_conflict_note or None,
                        result.governor_state,
                        result.signal_generated,
                        result.executed,
                        result.execution_status,
                        diag.get("trend_candidate"),
                        diag.get("trend_direction_bias"),
                        diag.get("trend_fail_reason"),
                        raw.get("trend_composite"),
                        raw.get("trend_strength_threshold"),
                        raw.get("trend_adx_h1"),
                        raw.get("trend_adx_m15"),
                        raw.get("trend_adx_threshold"),
                        raw.get("trend_h1_slope_norm"),
                        raw.get("trend_m15_slope_norm"),
                        raw.get("trend_htf_alignment"),
                        diag.get("breakout_candidate"),
                        diag.get("breakout_direction"),
                        diag.get("breakout_fail_reason"),
                        raw.get("breakout_compression_ratio"),
                        raw.get("breakout_compression_threshold"),
                        raw.get("breakout_atr_ratio_m1"),
                        raw.get("breakout_burst_threshold"),
                        raw.get("breakout_secondary_count"),
                        raw.get("breakout_secondary_required"),
                        raw.get("spread_ratio"),
                    )
                    if max_loops is not None and iterations >= max_loops:
                        break
                    time.sleep(config.bot.poll_interval_seconds)
        finally:
            with suppress(Exception):
                client.shutdown()

    ended = _utcnow()
    summary = DeploymentSummary(
        mode=mode,
        started_at=started.isoformat(),
        ended_at=ended.isoformat(),
        dry_run=dry_run,
        iterations=iterations,
        bars_processed=bars_processed,
        signals_generated=signals_generated,
        executions_attempted=executions_attempted,
        executions_filled=executions_filled,
        last_execution_status=last_execution_status,
        log_file=str(log_path),
        report_file="",
    )
    report_path = _write_summary(report_dir, summary)
    logger.info(
        "Deployment summary written | mode=%s | dry_run=%s | iterations=%s | bars_processed=%s | signals_generated=%s | executions_attempted=%s | executions_filled=%s | report=%s",
        summary.mode,
        summary.dry_run,
        summary.iterations,
        summary.bars_processed,
        summary.signals_generated,
        summary.executions_attempted,
        summary.executions_filled,
        report_path,
    )
    return DeploymentSummary(**{**asdict(summary), "report_file": str(report_path)})


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(description="TSP V1 forward-test deployment runner")
    parser.add_argument("--config", type=Path, required=True, help="Path to config yaml")
    parser.add_argument("--env-file", type=Path, required=True, help="Path to env file")
    parser.add_argument(
        "--mode",
        choices=("forward_test", "live"),
        default="forward_test",
        help="Deployment mode label for lockfiles and reports",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate guardrails and snapshot build only")
    parser.add_argument("--max-loops", type=int, default=None, help="Optional bounded loop count")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = run_deployment(
        config_path=args.config.resolve(),
        env_path=args.env_file.resolve(),
        mode=args.mode,
        dry_run=args.dry_run,
        max_loops=args.max_loops,
    )
    print(json.dumps(asdict(summary), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
