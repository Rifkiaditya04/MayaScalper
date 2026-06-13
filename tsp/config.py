"""Config loading and validation surface for TSP V1."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import os
from typing import Any

from .state import RiskParams


CONFIG_VERSION = 1
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")
DEFAULT_ENV_PATH = Path.cwd() / ".env"
SECTION_NAMES = (
    "bot",
    "risk",
    "regime",
    "signal",
    "lifecycle",
    "execution",
    "competition",
)


@dataclass(frozen=True, slots=True)
class MT5Credentials:
    login: str
    password: str
    server: str
    terminal_path: str


@dataclass(frozen=True, slots=True)
class BotConfig:
    magic_number: int
    poll_interval_seconds: int
    max_consecutive_bar_errors: int
    db_path: Path
    log_dir: Path
    state_dir: Path
    config_last_known_good_path: Path
    expert_mode: bool


@dataclass(frozen=True, slots=True)
class RegimeConfig:
    atr_collapse_asia: float
    atr_collapse_other: float
    dead_spread_ratio: float
    trend_strength_min: float
    trend_adx_boost: float
    trend_adx_min: float
    breakout_compression_max: float
    breakout_burst_min: float
    breakout_adx_min: float
    breakout_adx_max: float
    slope_agree_min: float
    slope_bias_long_min: float
    breakout_secondary_min_count: int
    breakout_direction_emergence_min: float
    breakout_volume_expansion_min: float
    breakout_m5_expansion_min: float


@dataclass(frozen=True, slots=True)
class SignalConfig:
    threshold_trend: float
    threshold_breakout: float
    threshold_chop: float
    aggression_adj_aggressive: float
    aggression_adj_normal: float
    aggression_adj_defensive: float
    confidence_penalty_weight: float
    stale_bars: int
    stale_score_improvement_min: float
    roc_lookback_bars: int
    roc_min_atr_fraction: float
    pullback_min_depth_atr: float
    pullback_max_depth_atr: float
    spread_penalty_ratio_start: float
    breakout_atr_boost_multiplier: float


@dataclass(frozen=True, slots=True)
class LifecycleConfig:
    tp_rr_pullback: float
    tp_rr_breakout: float
    be_trigger_r: float
    be_buffer_ticks: float
    trail_trigger_r: float
    trail_atr_multiplier: float
    trail_min_improve_ticks: float
    partial_trigger_r: float
    partial_size_ratio: float
    tp_attach_retry_limit: int
    orphan_unknown_action: str


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    signal_ttl_seconds: int
    spread_hard_veto_ratio: float
    spread_soft_penalty_ratio: float
    max_slippage_ticks: float
    market_order_retry_count: int
    market_order_timeout_seconds: int
    dedup_ttl_seconds: int


@dataclass(frozen=True, slots=True)
class CompetitionConfig:
    total_days: int
    target_total_pnl_r: float
    lead_protect_r: float
    sprint_pct: float
    session_risk_budget_r: float
    hunt_aggression_bias: float
    protect_aggression_bias: float
    sprint_aggression_bias: float
    hunt_threshold_modifier: float
    protect_threshold_modifier: float
    sprint_threshold_modifier: float
    circuit_loss_count: int
    circuit_session_pnl_r: float


@dataclass(frozen=True, slots=True)
class AppConfig:
    config_version: int
    supported_symbol: str
    config_path: Path
    fingerprint: str
    credentials: MT5Credentials
    bot: BotConfig
    risk: RiskParams
    regime: RegimeConfig
    signal: SignalConfig
    lifecycle: LifecycleConfig
    execution: ExecutionConfig
    competition: CompetitionConfig


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def _strip_inline_comment(raw: str) -> str:
    quote: str | None = None
    for idx, char in enumerate(raw):
        if char in {'"', "'"}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif char == "#" and quote is None:
            return raw[:idx].rstrip()
    return raw.rstrip()


def _parse_scalar(raw: str) -> Any:
    value = _strip_inline_comment(raw).strip()
    if value == "":
        return ""
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    try:
        if any(ch in value for ch in (".", "e", "E")):
            return float(value)
        return int(value)
    except ValueError:
        return value


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent % 2 != 0:
            raise ValueError(f"Invalid indentation at line {line_no}")
        line = _strip_inline_comment(raw_line).rstrip()
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError(f"Invalid YAML entry at line {line_no}: missing ':'")
        key_part, value_part = line.lstrip().split(":", 1)
        key = key_part.strip()
        if not key:
            raise ValueError(f"Invalid YAML entry at line {line_no}: empty key")
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value_part.strip() == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(value_part)
    return root


def _require_mapping(section_name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Section '{section_name}' must be a mapping")
    return value


def _validate_keys(section_name: str, data: dict[str, Any], allowed_keys: set[str]) -> None:
    unknown = sorted(set(data) - allowed_keys)
    missing = sorted(allowed_keys - set(data))
    if unknown:
        raise ValueError(f"Unknown keys in section '{section_name}': {', '.join(unknown)}")
    if missing:
        raise ValueError(f"Missing keys in section '{section_name}': {', '.join(missing)}")


def _coerce_path(base_dir: Path, raw: Any, field_name: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{field_name} must be a non-empty path string")
    path = Path(raw)
    return path if path.is_absolute() else (base_dir / path).resolve()


def _require_positive_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _require_non_negative_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _require_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    return float(value)


def _require_non_empty_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _build_credentials() -> MT5Credentials:
    login = _require_non_empty_str(os.getenv("TSP_MT5_LOGIN", ""), "TSP_MT5_LOGIN")
    password = _require_non_empty_str(os.getenv("TSP_MT5_PASSWORD", ""), "TSP_MT5_PASSWORD")
    server = _require_non_empty_str(os.getenv("TSP_MT5_SERVER", ""), "TSP_MT5_SERVER")
    terminal_path = os.getenv("TSP_MT5_TERMINAL_PATH", "").strip()
    return MT5Credentials(
        login=login,
        password=password,
        server=server,
        terminal_path=terminal_path,
    )


def _build_bot_config(data: dict[str, Any], base_dir: Path) -> BotConfig:
    _validate_keys(
        "bot",
        data,
        {
            "magic_number",
            "poll_interval_seconds",
            "max_consecutive_bar_errors",
            "db_path",
            "log_dir",
            "state_dir",
            "config_last_known_good_path",
            "expert_mode",
        },
    )
    expert_mode = data["expert_mode"]
    if not isinstance(expert_mode, bool):
        raise ValueError("bot.expert_mode must be boolean")
    return BotConfig(
        magic_number=_require_positive_int(data["magic_number"], "bot.magic_number"),
        poll_interval_seconds=_require_positive_int(
            data["poll_interval_seconds"], "bot.poll_interval_seconds"
        ),
        max_consecutive_bar_errors=_require_positive_int(
            data["max_consecutive_bar_errors"], "bot.max_consecutive_bar_errors"
        ),
        db_path=_coerce_path(base_dir, data["db_path"], "bot.db_path"),
        log_dir=_coerce_path(base_dir, data["log_dir"], "bot.log_dir"),
        state_dir=_coerce_path(base_dir, data["state_dir"], "bot.state_dir"),
        config_last_known_good_path=_coerce_path(
            base_dir,
            data["config_last_known_good_path"],
            "bot.config_last_known_good_path",
        ),
        expert_mode=expert_mode,
    )


def _build_risk_config(data: dict[str, Any]) -> RiskParams:
    _validate_keys(
        "risk",
        data,
        {
            "r_weak",
            "r_normal",
            "r_good",
            "r_elite",
            "r_max_single",
            "mult_defensive",
            "mult_normal",
            "mult_aggressive",
            "equity_floor_ratio",
            "dd_pct_defensive",
            "dd_pct_kill",
            "losses_defensive",
            "losses_kill",
            "wins_for_aggressive",
            "daily_r_for_aggressive",
            "dd_max_for_aggressive",
            "pyramid_max_layers",
            "pyramid_min_profit_r",
            "pyramid_aggregate_cap",
            "reentry_max_attempts",
            "reentry_min_bars",
            "spread_min_base_ticks",
        },
    )
    return RiskParams(
        r_weak=_require_float(data["r_weak"], "risk.r_weak"),
        r_normal=_require_float(data["r_normal"], "risk.r_normal"),
        r_good=_require_float(data["r_good"], "risk.r_good"),
        r_elite=_require_float(data["r_elite"], "risk.r_elite"),
        r_max_single=_require_float(data["r_max_single"], "risk.r_max_single"),
        mult_defensive=_require_float(data["mult_defensive"], "risk.mult_defensive"),
        mult_normal=_require_float(data["mult_normal"], "risk.mult_normal"),
        mult_aggressive=_require_float(data["mult_aggressive"], "risk.mult_aggressive"),
        equity_floor_ratio=_require_float(
            data["equity_floor_ratio"], "risk.equity_floor_ratio"
        ),
        dd_pct_defensive=_require_float(data["dd_pct_defensive"], "risk.dd_pct_defensive"),
        dd_pct_kill=_require_float(data["dd_pct_kill"], "risk.dd_pct_kill"),
        losses_defensive=_require_positive_int(
            data["losses_defensive"], "risk.losses_defensive"
        ),
        losses_kill=_require_positive_int(data["losses_kill"], "risk.losses_kill"),
        wins_for_aggressive=_require_positive_int(
            data["wins_for_aggressive"], "risk.wins_for_aggressive"
        ),
        daily_r_for_aggressive=_require_float(
            data["daily_r_for_aggressive"], "risk.daily_r_for_aggressive"
        ),
        dd_max_for_aggressive=_require_float(
            data["dd_max_for_aggressive"], "risk.dd_max_for_aggressive"
        ),
        pyramid_max_layers=_require_positive_int(
            data["pyramid_max_layers"], "risk.pyramid_max_layers"
        ),
        pyramid_min_profit_r=_require_float(
            data["pyramid_min_profit_r"], "risk.pyramid_min_profit_r"
        ),
        pyramid_aggregate_cap=_require_float(
            data["pyramid_aggregate_cap"], "risk.pyramid_aggregate_cap"
        ),
        reentry_max_attempts=_require_non_negative_int(
            data["reentry_max_attempts"], "risk.reentry_max_attempts"
        ),
        reentry_min_bars=_require_non_negative_int(
            data["reentry_min_bars"], "risk.reentry_min_bars"
        ),
        spread_min_base_ticks=_require_float(
            data["spread_min_base_ticks"], "risk.spread_min_base_ticks"
        ),
    )


def _build_regime_config(data: dict[str, Any]) -> RegimeConfig:
    _validate_keys(
        "regime",
        data,
        {
            "atr_collapse_asia",
            "atr_collapse_other",
            "dead_spread_ratio",
            "trend_strength_min",
            "trend_adx_boost",
            "trend_adx_min",
            "breakout_compression_max",
            "breakout_burst_min",
            "breakout_adx_min",
            "breakout_adx_max",
            "slope_agree_min",
            "slope_bias_long_min",
            "breakout_secondary_min_count",
            "breakout_direction_emergence_min",
            "breakout_volume_expansion_min",
            "breakout_m5_expansion_min",
        },
    )
    return RegimeConfig(
        atr_collapse_asia=_require_float(data["atr_collapse_asia"], "regime.atr_collapse_asia"),
        atr_collapse_other=_require_float(
            data["atr_collapse_other"], "regime.atr_collapse_other"
        ),
        dead_spread_ratio=_require_float(data["dead_spread_ratio"], "regime.dead_spread_ratio"),
        trend_strength_min=_require_float(
            data["trend_strength_min"], "regime.trend_strength_min"
        ),
        trend_adx_boost=_require_float(data["trend_adx_boost"], "regime.trend_adx_boost"),
        trend_adx_min=_require_float(data["trend_adx_min"], "regime.trend_adx_min"),
        breakout_compression_max=_require_float(
            data["breakout_compression_max"], "regime.breakout_compression_max"
        ),
        breakout_burst_min=_require_float(
            data["breakout_burst_min"], "regime.breakout_burst_min"
        ),
        breakout_adx_min=_require_float(data["breakout_adx_min"], "regime.breakout_adx_min"),
        breakout_adx_max=_require_float(data["breakout_adx_max"], "regime.breakout_adx_max"),
        slope_agree_min=_require_float(data["slope_agree_min"], "regime.slope_agree_min"),
        slope_bias_long_min=_require_float(
            data["slope_bias_long_min"], "regime.slope_bias_long_min"
        ),
        breakout_secondary_min_count=_require_positive_int(
            data["breakout_secondary_min_count"],
            "regime.breakout_secondary_min_count",
        ),
        breakout_direction_emergence_min=_require_float(
            data["breakout_direction_emergence_min"],
            "regime.breakout_direction_emergence_min",
        ),
        breakout_volume_expansion_min=_require_float(
            data["breakout_volume_expansion_min"],
            "regime.breakout_volume_expansion_min",
        ),
        breakout_m5_expansion_min=_require_float(
            data["breakout_m5_expansion_min"],
            "regime.breakout_m5_expansion_min",
        ),
    )


def _build_signal_config(data: dict[str, Any]) -> SignalConfig:
    _validate_keys(
        "signal",
        data,
        {
            "threshold_trend",
            "threshold_breakout",
            "threshold_chop",
            "aggression_adj_aggressive",
            "aggression_adj_normal",
            "aggression_adj_defensive",
            "confidence_penalty_weight",
            "stale_bars",
            "stale_score_improvement_min",
            "roc_lookback_bars",
            "roc_min_atr_fraction",
            "pullback_min_depth_atr",
            "pullback_max_depth_atr",
            "spread_penalty_ratio_start",
            "breakout_atr_boost_multiplier",
        },
    )
    return SignalConfig(
        threshold_trend=_require_float(data["threshold_trend"], "signal.threshold_trend"),
        threshold_breakout=_require_float(
            data["threshold_breakout"], "signal.threshold_breakout"
        ),
        threshold_chop=_require_float(data["threshold_chop"], "signal.threshold_chop"),
        aggression_adj_aggressive=_require_float(
            data["aggression_adj_aggressive"], "signal.aggression_adj_aggressive"
        ),
        aggression_adj_normal=_require_float(
            data["aggression_adj_normal"], "signal.aggression_adj_normal"
        ),
        aggression_adj_defensive=_require_float(
            data["aggression_adj_defensive"], "signal.aggression_adj_defensive"
        ),
        confidence_penalty_weight=_require_float(
            data["confidence_penalty_weight"], "signal.confidence_penalty_weight"
        ),
        stale_bars=_require_positive_int(data["stale_bars"], "signal.stale_bars"),
        stale_score_improvement_min=_require_float(
            data["stale_score_improvement_min"], "signal.stale_score_improvement_min"
        ),
        roc_lookback_bars=_require_positive_int(
            data["roc_lookback_bars"], "signal.roc_lookback_bars"
        ),
        roc_min_atr_fraction=_require_float(
            data["roc_min_atr_fraction"], "signal.roc_min_atr_fraction"
        ),
        pullback_min_depth_atr=_require_float(
            data["pullback_min_depth_atr"], "signal.pullback_min_depth_atr"
        ),
        pullback_max_depth_atr=_require_float(
            data["pullback_max_depth_atr"], "signal.pullback_max_depth_atr"
        ),
        spread_penalty_ratio_start=_require_float(
            data["spread_penalty_ratio_start"], "signal.spread_penalty_ratio_start"
        ),
        breakout_atr_boost_multiplier=_require_float(
            data["breakout_atr_boost_multiplier"],
            "signal.breakout_atr_boost_multiplier",
        ),
    )


def _build_lifecycle_config(data: dict[str, Any]) -> LifecycleConfig:
    _validate_keys(
        "lifecycle",
        data,
        {
            "tp_rr_pullback",
            "tp_rr_breakout",
            "be_trigger_r",
            "be_buffer_ticks",
            "trail_trigger_r",
            "trail_atr_multiplier",
            "trail_min_improve_ticks",
            "partial_trigger_r",
            "partial_size_ratio",
            "tp_attach_retry_limit",
            "orphan_unknown_action",
        },
    )
    return LifecycleConfig(
        tp_rr_pullback=_require_float(data["tp_rr_pullback"], "lifecycle.tp_rr_pullback"),
        tp_rr_breakout=_require_float(data["tp_rr_breakout"], "lifecycle.tp_rr_breakout"),
        be_trigger_r=_require_float(data["be_trigger_r"], "lifecycle.be_trigger_r"),
        be_buffer_ticks=_require_float(data["be_buffer_ticks"], "lifecycle.be_buffer_ticks"),
        trail_trigger_r=_require_float(
            data["trail_trigger_r"], "lifecycle.trail_trigger_r"
        ),
        trail_atr_multiplier=_require_float(
            data["trail_atr_multiplier"], "lifecycle.trail_atr_multiplier"
        ),
        trail_min_improve_ticks=_require_float(
            data["trail_min_improve_ticks"], "lifecycle.trail_min_improve_ticks"
        ),
        partial_trigger_r=_require_float(
            data["partial_trigger_r"], "lifecycle.partial_trigger_r"
        ),
        partial_size_ratio=_require_float(
            data["partial_size_ratio"], "lifecycle.partial_size_ratio"
        ),
        tp_attach_retry_limit=_require_positive_int(
            data["tp_attach_retry_limit"], "lifecycle.tp_attach_retry_limit"
        ),
        orphan_unknown_action=_require_non_empty_str(
            data["orphan_unknown_action"], "lifecycle.orphan_unknown_action"
        ).upper(),
    )


def _build_execution_config(data: dict[str, Any]) -> ExecutionConfig:
    _validate_keys(
        "execution",
        data,
        {
            "signal_ttl_seconds",
            "spread_hard_veto_ratio",
            "spread_soft_penalty_ratio",
            "max_slippage_ticks",
            "market_order_retry_count",
            "market_order_timeout_seconds",
            "dedup_ttl_seconds",
        },
    )
    return ExecutionConfig(
        signal_ttl_seconds=_require_positive_int(
            data["signal_ttl_seconds"], "execution.signal_ttl_seconds"
        ),
        spread_hard_veto_ratio=_require_float(
            data["spread_hard_veto_ratio"], "execution.spread_hard_veto_ratio"
        ),
        spread_soft_penalty_ratio=_require_float(
            data["spread_soft_penalty_ratio"], "execution.spread_soft_penalty_ratio"
        ),
        max_slippage_ticks=_require_float(
            data["max_slippage_ticks"], "execution.max_slippage_ticks"
        ),
        market_order_retry_count=_require_non_negative_int(
            data["market_order_retry_count"], "execution.market_order_retry_count"
        ),
        market_order_timeout_seconds=_require_positive_int(
            data["market_order_timeout_seconds"],
            "execution.market_order_timeout_seconds",
        ),
        dedup_ttl_seconds=_require_positive_int(
            data["dedup_ttl_seconds"], "execution.dedup_ttl_seconds"
        ),
    )


def _build_competition_config(data: dict[str, Any]) -> CompetitionConfig:
    _validate_keys(
        "competition",
        data,
        {
            "total_days",
            "target_total_pnl_r",
            "lead_protect_r",
            "sprint_pct",
            "session_risk_budget_r",
            "hunt_aggression_bias",
            "protect_aggression_bias",
            "sprint_aggression_bias",
            "hunt_threshold_modifier",
            "protect_threshold_modifier",
            "sprint_threshold_modifier",
            "circuit_loss_count",
            "circuit_session_pnl_r",
        },
    )
    return CompetitionConfig(
        total_days=_require_positive_int(data["total_days"], "competition.total_days"),
        target_total_pnl_r=_require_float(
            data["target_total_pnl_r"], "competition.target_total_pnl_r"
        ),
        lead_protect_r=_require_float(data["lead_protect_r"], "competition.lead_protect_r"),
        sprint_pct=_require_float(data["sprint_pct"], "competition.sprint_pct"),
        session_risk_budget_r=_require_float(
            data["session_risk_budget_r"], "competition.session_risk_budget_r"
        ),
        hunt_aggression_bias=_require_float(
            data["hunt_aggression_bias"], "competition.hunt_aggression_bias"
        ),
        protect_aggression_bias=_require_float(
            data["protect_aggression_bias"], "competition.protect_aggression_bias"
        ),
        sprint_aggression_bias=_require_float(
            data["sprint_aggression_bias"], "competition.sprint_aggression_bias"
        ),
        hunt_threshold_modifier=_require_float(
            data["hunt_threshold_modifier"], "competition.hunt_threshold_modifier"
        ),
        protect_threshold_modifier=_require_float(
            data["protect_threshold_modifier"], "competition.protect_threshold_modifier"
        ),
        sprint_threshold_modifier=_require_float(
            data["sprint_threshold_modifier"], "competition.sprint_threshold_modifier"
        ),
        circuit_loss_count=_require_positive_int(
            data["circuit_loss_count"], "competition.circuit_loss_count"
        ),
        circuit_session_pnl_r=_require_float(
            data["circuit_session_pnl_r"], "competition.circuit_session_pnl_r"
        ),
    )


def _validate_cross_section_rules(config: AppConfig) -> None:
    if config.supported_symbol != "XAUUSD":
        raise ValueError("supported_symbol must be XAUUSD for TSP V1")
    if config.risk.mult_aggressive > 2.0:
        raise ValueError("risk.mult_aggressive must be <= 2.0")
    if config.lifecycle.tp_rr_pullback < 1.2 or config.lifecycle.tp_rr_breakout < 1.2:
        raise ValueError("lifecycle TP RR values must be >= 1.2")
    if config.lifecycle.be_trigger_r >= config.lifecycle.trail_trigger_r:
        raise ValueError("lifecycle.be_trigger_r must be < lifecycle.trail_trigger_r")
    if not 0.0 < config.lifecycle.partial_size_ratio < 1.0:
        raise ValueError("lifecycle.partial_size_ratio must be in (0, 1)")
    if config.execution.spread_hard_veto_ratio <= config.signal.spread_penalty_ratio_start:
        raise ValueError(
            "execution.spread_hard_veto_ratio must be greater than signal.spread_penalty_ratio_start"
        )
    if config.competition.session_risk_budget_r <= 0.0:
        raise ValueError("competition.session_risk_budget_r must be > 0")
    if config.competition.circuit_session_pnl_r >= 0.0:
        raise ValueError("competition.circuit_session_pnl_r must be negative")
    if config.competition.lead_protect_r >= config.competition.target_total_pnl_r:
        raise ValueError("competition.lead_protect_r must be < competition.target_total_pnl_r")
    if not 0.0 < config.competition.sprint_pct < 1.0:
        raise ValueError("competition.sprint_pct must be between 0 and 1")
    if config.lifecycle.orphan_unknown_action not in {"FLATTEN", "ADOPT"}:
        raise ValueError("lifecycle.orphan_unknown_action must be FLATTEN or ADOPT")
    if config.lifecycle.orphan_unknown_action == "ADOPT" and not config.bot.expert_mode:
        raise ValueError("lifecycle.orphan_unknown_action=ADOPT requires bot.expert_mode=true")


def load_config(path: Path | None = None, env_path: Path | None = None) -> AppConfig:
    config_path = (path or DEFAULT_CONFIG_PATH).resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"TSP config file not found: {config_path}")

    _load_dotenv(env_path or DEFAULT_ENV_PATH)
    credentials = _build_credentials()

    raw_text = config_path.read_text(encoding="utf-8")
    parsed = _parse_simple_yaml(raw_text)
    if not isinstance(parsed, dict):
        raise ValueError("Config root must be a mapping")

    version = parsed.get("config_version")
    supported_symbol = parsed.get("supported_symbol")
    if version != CONFIG_VERSION:
        raise ValueError(
            f"Unsupported config_version={version}; expected {CONFIG_VERSION}"
        )
    if supported_symbol != "XAUUSD":
        raise ValueError("supported_symbol must be XAUUSD for TSP V1")

    top_level_allowed = {"config_version", "supported_symbol", *SECTION_NAMES}
    _validate_keys("root", parsed, top_level_allowed)

    base_dir = config_path.parent
    app_config = AppConfig(
        config_version=CONFIG_VERSION,
        supported_symbol="XAUUSD",
        config_path=config_path,
        fingerprint=sha256(raw_text.encode("utf-8")).hexdigest(),
        credentials=credentials,
        bot=_build_bot_config(_require_mapping("bot", parsed["bot"]), base_dir),
        risk=_build_risk_config(_require_mapping("risk", parsed["risk"])),
        regime=_build_regime_config(_require_mapping("regime", parsed["regime"])),
        signal=_build_signal_config(_require_mapping("signal", parsed["signal"])),
        lifecycle=_build_lifecycle_config(
            _require_mapping("lifecycle", parsed["lifecycle"])
        ),
        execution=_build_execution_config(
            _require_mapping("execution", parsed["execution"])
        ),
        competition=_build_competition_config(
            _require_mapping("competition", parsed["competition"])
        ),
    )
    _validate_cross_section_rules(app_config)
    return app_config


__all__ = [
    "AppConfig",
    "BotConfig",
    "CompetitionConfig",
    "CONFIG_VERSION",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_ENV_PATH",
    "ExecutionConfig",
    "LifecycleConfig",
    "MT5Credentials",
    "RegimeConfig",
    "SignalConfig",
    "load_config",
]
