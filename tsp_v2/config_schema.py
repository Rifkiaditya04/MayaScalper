"""Strict config schema contract for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .enums import GovernorState, NewsProviderMode, ProfileName, RuntimeMode


class ConfigValidationError(ValueError):
    """Raised when config input violates the production contract."""


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    field_type: str
    required: bool
    unit: str = ""
    description: str = ""
    enum_type: type | None = None


CONFIG_SCHEMA: dict[str, tuple[FieldSpec, ...]] = {
    "bot": (
        FieldSpec("mode", "enum", True, enum_type=RuntimeMode),
        FieldSpec("profile", "enum", True, enum_type=ProfileName),
        FieldSpec("expert_mode", "bool", True),
        FieldSpec("poll_interval_seconds", "int", True, unit="seconds"),
    ),
    "symbols": (
        FieldSpec("allowlist", "list[str]", True),
    ),
    "alpha": (
        FieldSpec("setup_cooldown_bars", "int", True, unit="bars"),
    ),
    "regime": (
        FieldSpec("closed_bar_timeframe", "str", True),
        FieldSpec("news_lockout_minutes", "int", True, unit="minutes"),
    ),
    "signal": (
        FieldSpec("min_score", "float", True),
        FieldSpec("ttl_seconds", "int", True, unit="seconds"),
    ),
    "risk": (
        FieldSpec("max_open_risk_pct", "float", True, unit="percent"),
        FieldSpec("max_daily_loss_pct", "float", True, unit="percent"),
    ),
    "governor": (
        FieldSpec("initial_state", "enum", True, enum_type=GovernorState),
        FieldSpec("kill_review_drawdown_pct", "float", True, unit="percent"),
        FieldSpec("offensive_profiles_require_expert_mode", "bool", True),
    ),
    "lifecycle": (
        FieldSpec("thesis_ttl_minutes", "int", True, unit="minutes"),
        FieldSpec("break_even_after_r", "float", True, unit="R"),
    ),
    "execution": (
        FieldSpec("signal_ttl_seconds", "int", True, unit="seconds"),
        FieldSpec("slippage_veto_ratio", "float", True, unit="slippage_atr_ratio"),
        FieldSpec("max_spread_ratio", "float", True, unit="spread_ratio"),
    ),
    "telemetry": (
        FieldSpec("heartbeat_interval_seconds", "int", True, unit="seconds"),
        FieldSpec("emit_candidate_diagnostics", "bool", True),
    ),
    "persistence": (
        FieldSpec("sqlite_path", "str", True),
        FieldSpec("lock_path", "str", True),
        FieldSpec("wal_enabled", "bool", True),
    ),
    "contest": (
        FieldSpec("ranking_proxy_enabled", "bool", True),
        FieldSpec("contest_window_minutes", "int", True, unit="minutes"),
    ),
    "deployment": (
        FieldSpec("runtime_root", "str", True),
        FieldSpec("log_root", "str", True),
        FieldSpec("report_root", "str", True),
        FieldSpec("allow_live_execution", "bool", True),
    ),
    "news": (
        FieldSpec("provider_mode", "enum", True, enum_type=NewsProviderMode),
        FieldSpec("source_path", "str|none", True),
        FieldSpec("stale_warn_minutes", "int", True, unit="minutes"),
        FieldSpec("stale_soft_fail_minutes", "int", True, unit="minutes"),
        FieldSpec("stale_hard_fail_minutes", "int", True, unit="minutes"),
    ),
}

OFFICIAL_MODES: frozenset[RuntimeMode] = frozenset(RuntimeMode)
OFFICIAL_PROFILES: frozenset[ProfileName] = frozenset(ProfileName)
UNSAFE_PROFILES: frozenset[ProfileName] = frozenset({ProfileName.FINAL_SPRINT})
PROFILE_MODE_COMPATIBILITY: dict[ProfileName, frozenset[RuntimeMode]] = {
    ProfileName.FORWARD_SAFE: frozenset(
        {RuntimeMode.DEVELOPMENT, RuntimeMode.BACKTEST, RuntimeMode.FORWARD_TEST}
    ),
    ProfileName.CONTEST_BALANCED: frozenset({RuntimeMode.BACKTEST, RuntimeMode.CONTEST}),
    ProfileName.CONTEST_HUNTER: frozenset({RuntimeMode.BACKTEST, RuntimeMode.CONTEST}),
    ProfileName.FINAL_SPRINT: frozenset({RuntimeMode.BACKTEST, RuntimeMode.CONTEST}),
    ProfileName.DIAGNOSTIC: frozenset({RuntimeMode.DEVELOPMENT, RuntimeMode.DIAGNOSTIC}),
}


def parse_enum_member(enum_type: type, raw_value: Any, field_name: str) -> Any:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ConfigValidationError(f"{field_name} must be a non-empty string enum value")
    try:
        return enum_type(raw_value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_type)
        raise ConfigValidationError(
            f"{field_name} must be one of: {allowed}"
        ) from exc


def coerce_field_value(spec: FieldSpec, raw_value: Any, field_name: str) -> Any:
    field_type = spec.field_type
    if field_type == "bool":
        if not isinstance(raw_value, bool):
            raise ConfigValidationError(f"{field_name} must be a boolean")
        return raw_value
    if field_type == "int":
        if not isinstance(raw_value, int) or isinstance(raw_value, bool):
            raise ConfigValidationError(f"{field_name} must be an integer")
        return raw_value
    if field_type == "float":
        if not isinstance(raw_value, (int, float)) or isinstance(raw_value, bool):
            raise ConfigValidationError(f"{field_name} must be a float")
        return float(raw_value)
    if field_type == "str":
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ConfigValidationError(f"{field_name} must be a non-empty string")
        return raw_value.strip()
    if field_type == "str|none":
        if raw_value is None:
            return None
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ConfigValidationError(f"{field_name} must be null or a non-empty string")
        return raw_value.strip()
    if field_type == "list[str]":
        if not isinstance(raw_value, list) or not raw_value:
            raise ConfigValidationError(f"{field_name} must be a non-empty list")
        normalized: list[str] = []
        for idx, item in enumerate(raw_value):
            if not isinstance(item, str) or not item.strip():
                raise ConfigValidationError(f"{field_name}[{idx}] must be a non-empty string")
            normalized.append(item.strip())
        return normalized
    if field_type == "enum":
        if spec.enum_type is None:
            raise ConfigValidationError(f"{field_name} is missing enum metadata")
        return parse_enum_member(spec.enum_type, raw_value, field_name)
    raise ConfigValidationError(f"{field_name} uses unsupported field type '{field_type}'")


def _validate_keys(section_name: str, data: dict[str, Any], allowed_keys: set[str]) -> None:
    unknown = sorted(set(data) - allowed_keys)
    missing = sorted(allowed_keys - set(data))
    if unknown:
        raise ConfigValidationError(
            f"Unknown keys in section '{section_name}': {', '.join(unknown)}"
        )
    if missing:
        raise ConfigValidationError(
            f"Missing keys in section '{section_name}': {', '.join(missing)}"
        )


def _require_positive_int(value: int, field_name: str) -> None:
    if value <= 0:
        raise ConfigValidationError(f"{field_name} must be a positive integer")


def _require_non_negative_float(value: float, field_name: str) -> None:
    if value < 0.0:
        raise ConfigValidationError(f"{field_name} must be non-negative")


def _require_positive_float(value: float, field_name: str) -> None:
    if value <= 0.0:
        raise ConfigValidationError(f"{field_name} must be positive")


def validate_schema_shape(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate the full config tree and return normalized values."""
    if not isinstance(config, dict):
        raise ConfigValidationError("Top-level config must be a mapping")

    unknown_sections = sorted(set(config) - set(CONFIG_SCHEMA))
    missing_sections = sorted(set(CONFIG_SCHEMA) - set(config))
    if unknown_sections:
        raise ConfigValidationError(
            f"Unknown top-level sections: {', '.join(unknown_sections)}"
        )
    if missing_sections:
        raise ConfigValidationError(
            f"Missing top-level sections: {', '.join(missing_sections)}"
        )

    normalized: dict[str, dict[str, Any]] = {}
    for section_name, field_specs in CONFIG_SCHEMA.items():
        raw_section = config.get(section_name)
        if not isinstance(raw_section, dict):
            raise ConfigValidationError(f"Section '{section_name}' must be a mapping")
        allowed_keys = {field.name for field in field_specs}
        _validate_keys(section_name, raw_section, allowed_keys)

        section_values: dict[str, Any] = {}
        for field_spec in field_specs:
            raw_value = raw_section[field_spec.name]
            field_name = f"{section_name}.{field_spec.name}"
            section_values[field_spec.name] = coerce_field_value(field_spec, raw_value, field_name)
        normalized[section_name] = section_values

    _validate_cross_section(normalized)
    return normalized


def _validate_cross_section(normalized: dict[str, dict[str, Any]]) -> None:
    bot = normalized["bot"]
    symbols = normalized["symbols"]
    regime = normalized["regime"]
    signal = normalized["signal"]
    risk = normalized["risk"]
    governor = normalized["governor"]
    lifecycle = normalized["lifecycle"]
    execution = normalized["execution"]
    telemetry = normalized["telemetry"]
    contest = normalized["contest"]
    news = normalized["news"]

    _require_positive_int(bot["poll_interval_seconds"], "bot.poll_interval_seconds")
    _require_positive_int(signal["ttl_seconds"], "signal.ttl_seconds")
    _require_positive_int(execution["signal_ttl_seconds"], "execution.signal_ttl_seconds")
    _require_positive_int(telemetry["heartbeat_interval_seconds"], "telemetry.heartbeat_interval_seconds")
    _require_positive_int(regime["news_lockout_minutes"], "regime.news_lockout_minutes")
    _require_positive_int(lifecycle["thesis_ttl_minutes"], "lifecycle.thesis_ttl_minutes")
    _require_positive_int(contest["contest_window_minutes"], "contest.contest_window_minutes")
    _require_positive_int(normalized["alpha"]["setup_cooldown_bars"], "alpha.setup_cooldown_bars")

    _require_positive_float(signal["min_score"], "signal.min_score")
    _require_positive_float(execution["slippage_veto_ratio"], "execution.slippage_veto_ratio")
    _require_positive_float(execution["max_spread_ratio"], "execution.max_spread_ratio")
    _require_positive_float(governor["kill_review_drawdown_pct"], "governor.kill_review_drawdown_pct")
    _require_positive_float(lifecycle["break_even_after_r"], "lifecycle.break_even_after_r")
    _require_positive_float(risk["max_open_risk_pct"], "risk.max_open_risk_pct")
    _require_positive_float(risk["max_daily_loss_pct"], "risk.max_daily_loss_pct")

    if signal["min_score"] > 1.0:
        raise ConfigValidationError("signal.min_score must be <= 1.0")
    if risk["max_open_risk_pct"] > 100.0:
        raise ConfigValidationError("risk.max_open_risk_pct must be <= 100")
    if risk["max_daily_loss_pct"] > 100.0:
        raise ConfigValidationError("risk.max_daily_loss_pct must be <= 100")
    if execution["signal_ttl_seconds"] != signal["ttl_seconds"]:
        raise ConfigValidationError(
            "execution.signal_ttl_seconds must equal signal.ttl_seconds"
        )

    provider_mode = news["provider_mode"]
    if (
        provider_mode is NewsProviderMode.DISABLED_DIAGNOSTIC_ONLY
        and bot["mode"] is not RuntimeMode.DIAGNOSTIC
    ):
        raise ConfigValidationError(
            "news.provider_mode=DISABLED_DIAGNOSTIC_ONLY is allowed only in DIAGNOSTIC mode"
        )

    warn = news["stale_warn_minutes"]
    soft_fail = news["stale_soft_fail_minutes"]
    hard_fail = news["stale_hard_fail_minutes"]
    _require_positive_int(warn, "news.stale_warn_minutes")
    _require_positive_int(soft_fail, "news.stale_soft_fail_minutes")
    _require_positive_int(hard_fail, "news.stale_hard_fail_minutes")
    if not (warn < soft_fail < hard_fail):
        raise ConfigValidationError(
            "news freshness thresholds must satisfy warn < soft_fail < hard_fail"
        )

    allowlist = symbols["allowlist"]
    canonical_symbols = {item.strip().upper() for item in allowlist}
    if len(canonical_symbols) != len(allowlist):
        raise ConfigValidationError("symbols.allowlist must not contain duplicates")
    if any(not symbol.isascii() for symbol in canonical_symbols):
        raise ConfigValidationError("symbols.allowlist must contain ASCII symbols only")

    profile = bot["profile"]
    mode = bot["mode"]
    supported_modes = PROFILE_MODE_COMPATIBILITY[profile]
    if mode not in supported_modes:
        raise ConfigValidationError(
            f"bot.profile={profile.value} does not support bot.mode={mode.value}"
        )

    if profile in UNSAFE_PROFILES and not bot["expert_mode"]:
        raise ConfigValidationError(
            f"bot.profile={profile.value} requires bot.expert_mode=true"
        )

    if profile is ProfileName.DIAGNOSTIC and mode is not RuntimeMode.DIAGNOSTIC:
        raise ConfigValidationError(
            "bot.profile=DIAGNOSTIC requires bot.mode=DIAGNOSTIC"
        )

    if governor["offensive_profiles_require_expert_mode"]:
        if profile in UNSAFE_PROFILES and not bot["expert_mode"]:
            raise ConfigValidationError(
                f"bot.profile={profile.value} requires bot.expert_mode=true"
            )

    if contest["contest_window_minutes"] < 60:
        raise ConfigValidationError("contest.contest_window_minutes must be >= 60")
