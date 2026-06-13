"""Konfigurasi environment untuk MT5 live rebuild."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


def _env_int_list(name: str, default: str) -> tuple[int, ...]:
    raw = _env(name, default)
    values: list[int] = []
    for part in raw.split(","):
        piece = part.strip()
        if not piece:
            continue
        try:
            value = int(piece)
        except ValueError:
            continue
        if value > 0:
            values.append(value)
    return tuple(values) if values else tuple(int(p.strip()) for p in default.split(",") if p.strip())


def _load_dotenv() -> None:
    """Load `.env` sederhana tanpa dependency tambahan."""
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


@dataclass(slots=True)
class Settings:
    login: str
    password: str
    server: str
    terminal_path: str
    symbol: str
    asset_mode: str
    enable_order_execution: bool
    magic_number: int
    order_deviation_points: int
    poll_interval_seconds: int
    bars_fetch_count: int
    layer_count: int
    max_positions: int
    max_layers_per_direction: int
    layer_spacing_atr_mult: float
    min_seconds_between_entries: int
    max_lot_per_order: float
    total_setup_risk_pct: float
    tp_atr_mult: float
    tp_broker_buffer: float
    tp_feasibility_buffer: float
    lock_structure_lookback: int
    lock_failsafe_minutes: int
    lock_buffer_pips: float
    lock_buffer_spread_factor: float
    freshness_buffer_pips: float
    freshness_reset_expiry_bars: int
    manual_close_cooldown_seconds: int
    min_entry_interval_seconds: int
    protection_attach_retry_count: int
    protection_attach_retry_delay_seconds: int
    position_close_retry_limit: int
    position_close_retry_backoff_schedule_seconds: tuple[int, ...]
    daily_drawdown_soft_pct: float
    equity_drawdown_hard_pct: float
    consecutive_loss_limit: int
    consecutive_loss_pause_minutes: int
    strategy_near_miss_score_min: int
    strategy_near_miss_sample_limit: int
    progress_exit_counterfactual_limit: int
    log_level: str


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in {"1", "true", "yes", "y", "on"}


def load_settings() -> Settings:
    _load_dotenv()
    return Settings(
        login=_env("MT5_LOGIN"),
        password=_env("MT5_PASSWORD"),
        server=_env("MT5_SERVER"),
        terminal_path=_env("MT5_TERMINAL_PATH"),
        symbol=_env("MT5_SYMBOL", "GBPUSD"),
        asset_mode=_env("MT5_ASSET_MODE", "forex"),
        enable_order_execution=_env_bool("MT5_ENABLE_ORDER_EXECUTION", False),
        magic_number=int(_env("MT5_MAGIC_NUMBER", "20260508")),
        order_deviation_points=int(_env("MT5_ORDER_DEVIATION_POINTS", "20")),
        poll_interval_seconds=int(_env("MT5_POLL_INTERVAL_SECONDS", "5")),
        bars_fetch_count=int(_env("MT5_BARS_FETCH_COUNT", "250")),
        layer_count=int(_env("MT5_LAYER_COUNT", "1")),
        max_positions=int(_env("MT5_MAX_POSITIONS", "1")),
        max_layers_per_direction=int(_env("MT5_MAX_LAYERS_PER_DIRECTION", "2")),
        layer_spacing_atr_mult=float(_env("MT5_LAYER_SPACING_ATR_MULT", "0.50")),
        min_seconds_between_entries=int(_env("MT5_MIN_SECONDS_BETWEEN_ENTRIES", "120")),
        max_lot_per_order=float(_env("MT5_MAX_LOT_PER_ORDER", "0.10")),
        total_setup_risk_pct=float(_env("MT5_TOTAL_SETUP_RISK_PCT", "0.01")),
        tp_atr_mult=float(_env("MT5_TP_ATR_MULT", "0.30")),
        tp_broker_buffer=float(_env("MT5_TP_BROKER_BUFFER", "1.20")),
        tp_feasibility_buffer=float(_env("MT5_TP_FEASIBILITY_BUFFER", "1.10")),
        lock_structure_lookback=int(_env("MT5_DIRECTIONAL_LOCK_STRUCTURE_LOOKBACK", "5")),
        lock_failsafe_minutes=int(_env("MT5_DIRECTIONAL_LOCK_FAILSAFE_MINUTES", "180")),
        lock_buffer_pips=float(_env("MT5_DIRECTIONAL_LOCK_BUFFER_PIPS", "0.20")),
        lock_buffer_spread_factor=float(_env("MT5_DIRECTIONAL_LOCK_BUFFER_SPREAD_FACTOR", "0.10")),
        freshness_buffer_pips=float(_env("MT5_FRESHNESS_BUFFER_PIPS", "0.20")),
        freshness_reset_expiry_bars=int(_env("MT5_FRESHNESS_RESET_EXPIRY_BARS", "5")),
        manual_close_cooldown_seconds=int(_env("MT5_MANUAL_CLOSE_COOLDOWN_SECONDS", "180")),
        min_entry_interval_seconds=int(_env("MT5_MIN_ENTRY_INTERVAL_SECONDS", "60")),
        protection_attach_retry_count=int(_env("MT5_PROTECTION_ATTACH_RETRY_COUNT", "3")),
        protection_attach_retry_delay_seconds=int(_env("MT5_PROTECTION_ATTACH_RETRY_DELAY_SECONDS", "2")),
        position_close_retry_limit=int(_env("MT5_POSITION_CLOSE_RETRY_LIMIT", "5")),
        position_close_retry_backoff_schedule_seconds=_env_int_list("MT5_POSITION_CLOSE_RETRY_BACKOFF_SCHEDULE", "5,10,20,40,60"),
        daily_drawdown_soft_pct=float(_env("MT5_DAILY_DRAWDOWN_SOFT_PCT", "3.0")),
        equity_drawdown_hard_pct=float(_env("MT5_EQUITY_DRAWDOWN_HARD_PCT", "5.0")),
        consecutive_loss_limit=int(_env("MT5_CONSECUTIVE_LOSS_LIMIT", "3")),
        consecutive_loss_pause_minutes=int(_env("MT5_CONSECUTIVE_LOSS_PAUSE_MINUTES", "120")),
        strategy_near_miss_score_min=int(_env("MT5_STRATEGY_NEAR_MISS_SCORE_MIN", "5")),
        strategy_near_miss_sample_limit=int(_env("MT5_STRATEGY_NEAR_MISS_SAMPLE_LIMIT", "100")),
        progress_exit_counterfactual_limit=int(_env("MT5_PROGRESS_EXIT_COUNTERFACTUAL_LIMIT", "50")),
        log_level=_env("MT5_LOG_LEVEL", "INFO"),
    )
