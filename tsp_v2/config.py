"""Production-grade config foundation for TSP V2."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
import json
import os
from typing import Any, Mapping

from .config_schema import (
    CONFIG_SCHEMA,
    ConfigValidationError,
    coerce_field_value,
    validate_schema_shape,
)
from .enums import GovernorState, NewsProviderMode, ProfileName, RuntimeMode


CONFIG_ENV_PREFIX = "TSP_V2_"
SECRET_ENV_KEYS = (
    "TSP_V2_MT5_LOGIN",
    "TSP_V2_MT5_PASSWORD",
    "TSP_V2_MT5_SERVER",
    "TSP_V2_MT5_TERMINAL_PATH",
)
CLI_OVERRIDE_FIELDS = frozenset(
    {
        "bot.mode",
        "bot.profile",
        "bot.expert_mode",
        "deployment.allow_live_execution",
        "news.provider_mode",
    }
)


@dataclass(frozen=True, slots=True)
class BotConfig:
    mode: RuntimeMode
    profile: ProfileName
    expert_mode: bool
    poll_interval_seconds: int


@dataclass(frozen=True, slots=True)
class SymbolsConfig:
    allowlist: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AlphaConfig:
    setup_cooldown_bars: int


@dataclass(frozen=True, slots=True)
class RegimeConfig:
    closed_bar_timeframe: str
    news_lockout_minutes: int


@dataclass(frozen=True, slots=True)
class SignalConfig:
    min_score: float
    ttl_seconds: int


@dataclass(frozen=True, slots=True)
class RiskConfig:
    max_open_risk_pct: float
    max_daily_loss_pct: float


@dataclass(frozen=True, slots=True)
class GovernorConfig:
    initial_state: GovernorState
    kill_review_drawdown_pct: float
    offensive_profiles_require_expert_mode: bool


@dataclass(frozen=True, slots=True)
class LifecycleConfig:
    thesis_ttl_minutes: int
    break_even_after_r: float


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    signal_ttl_seconds: int
    slippage_veto_ratio: float
    max_spread_ratio: float


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    heartbeat_interval_seconds: int
    emit_candidate_diagnostics: bool


@dataclass(frozen=True, slots=True)
class PersistenceConfig:
    sqlite_path: Path
    lock_path: Path
    wal_enabled: bool


@dataclass(frozen=True, slots=True)
class ContestConfig:
    ranking_proxy_enabled: bool
    contest_window_minutes: int


@dataclass(frozen=True, slots=True)
class DeploymentConfig:
    runtime_root: Path
    log_root: Path
    report_root: Path
    allow_live_execution: bool


@dataclass(frozen=True, slots=True)
class NewsConfig:
    provider_mode: NewsProviderMode
    source_path: Path | None
    stale_warn_minutes: int
    stale_soft_fail_minutes: int
    stale_hard_fail_minutes: int


@dataclass(frozen=True, slots=True)
class SecretConfig:
    mt5_login: str | None
    mt5_password: str | None
    mt5_server: str | None
    mt5_terminal_path: Path | None


@dataclass(frozen=True, slots=True)
class AppConfig:
    config_path: Path
    base_config_path: Path
    fingerprint: str
    bot: BotConfig
    symbols: SymbolsConfig
    alpha: AlphaConfig
    regime: RegimeConfig
    signal: SignalConfig
    risk: RiskConfig
    governor: GovernorConfig
    lifecycle: LifecycleConfig
    execution: ExecutionConfig
    telemetry: TelemetryConfig
    persistence: PersistenceConfig
    contest: ContestConfig
    deployment: DeploymentConfig
    news: NewsConfig
    secrets: SecretConfig


def load_config(
    *,
    config_path: Path,
    env_path: Path | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
) -> AppConfig:
    """Load config using base -> profile -> environment -> governed CLI priority."""
    config_path = config_path.resolve()
    if not config_path.exists():
        raise ConfigValidationError(f"Config file does not exist: {config_path}")

    if env_path is not None:
        _load_dotenv(env_path.resolve())

    base_config_path = config_path.with_name("base.yaml")
    if not base_config_path.exists():
        raise ConfigValidationError(
            f"Base config is required beside profile config: {base_config_path}"
        )
    if config_path.name.lower() == "base.yaml":
        raise ConfigValidationError("Runtime startup must target a governed profile config, not base.yaml")

    base_layer = _parse_simple_yaml(base_config_path.read_text(encoding="utf-8"))
    profile_layer = _parse_simple_yaml(config_path.read_text(encoding="utf-8"))
    env_layer = _build_env_overrides()
    cli_layer = _build_cli_overrides(cli_overrides or {})

    merged = _deep_merge(base_layer, profile_layer)
    merged = _deep_merge(merged, env_layer)
    merged = _deep_merge(merged, cli_layer)

    normalized = validate_schema_shape(merged)
    fingerprint = build_config_fingerprint(normalized)

    project_root = Path(__file__).resolve().parent.parent
    secrets = _load_secrets(mode=normalized["bot"]["mode"], env_path=env_path, project_root=project_root)
    return _materialize_config(
        normalized=normalized,
        fingerprint=fingerprint,
        config_path=config_path,
        base_config_path=base_config_path,
        project_root=project_root,
        secrets=secrets,
    )


def build_config_fingerprint(normalized_config: dict[str, dict[str, Any]]) -> str:
    canonical_payload = canonicalize_config_for_fingerprint(normalized_config)
    payload = json.dumps(
        canonical_payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def canonicalize_config_for_fingerprint(
    normalized_config: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    unknown_sections = sorted(set(normalized_config) - set(CONFIG_SCHEMA))
    if unknown_sections:
        raise ConfigValidationError(
            f"Fingerprint canonicalization received unknown sections: {', '.join(unknown_sections)}"
        )
    for section_name, field_specs in CONFIG_SCHEMA.items():
        section = normalized_config.get(section_name)
        if section is None:
            raise ConfigValidationError(
                f"Fingerprint canonicalization missing section '{section_name}'"
            )
        canonical_section: dict[str, Any] = {}
        for field_spec in field_specs:
            if field_spec.name not in section:
                raise ConfigValidationError(
                    f"Fingerprint canonicalization missing field '{section_name}.{field_spec.name}'"
                )
            canonical_section[field_spec.name] = _json_ready(section[field_spec.name])
        canonical[section_name] = canonical_section
    return canonical


def _load_secrets(
    *,
    mode: RuntimeMode,
    env_path: Path | None,
    project_root: Path,
) -> SecretConfig:
    del env_path
    login = _optional_env_with_aliases("TSP_V2_MT5_LOGIN", "MT5_LOGIN", "TSP_MT5_LOGIN")
    password = _optional_env_with_aliases("TSP_V2_MT5_PASSWORD", "MT5_PASSWORD", "TSP_MT5_PASSWORD")
    server = _optional_env_with_aliases("TSP_V2_MT5_SERVER", "MT5_SERVER", "TSP_MT5_SERVER")
    terminal_raw = _optional_env_with_aliases(
        "TSP_V2_MT5_TERMINAL_PATH",
        "MT5_TERMINAL_PATH",
        "TSP_MT5_TERMINAL_PATH",
    )
    terminal_path = _coerce_path(project_root, terminal_raw) if terminal_raw is not None else None

    if mode in {RuntimeMode.FORWARD_TEST, RuntimeMode.CONTEST}:
        missing = [
            key
            for key, value in (
                ("TSP_V2_MT5_LOGIN", login),
                ("TSP_V2_MT5_PASSWORD", password),
                ("TSP_V2_MT5_SERVER", server),
                ("TSP_V2_MT5_TERMINAL_PATH", terminal_raw),
            )
            if value is None
        ]
        if missing:
            raise ConfigValidationError(
                f"Missing required secrets for {mode.value}: {', '.join(missing)}"
            )

    return SecretConfig(
        mt5_login=login,
        mt5_password=password,
        mt5_server=server,
        mt5_terminal_path=terminal_path,
    )


def _optional_env(key: str) -> str | None:
    raw = os.getenv(key)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _optional_env_with_aliases(primary_key: str, *alias_keys: str) -> str | None:
    value = _optional_env(primary_key)
    if value is not None:
        return value
    for alias_key in alias_keys:
        value = _optional_env(alias_key)
        if value is not None:
            return value
    return None


def _materialize_config(
    *,
    normalized: dict[str, dict[str, Any]],
    fingerprint: str,
    config_path: Path,
    base_config_path: Path,
    project_root: Path,
    secrets: SecretConfig,
) -> AppConfig:
    bot = normalized["bot"]
    news = normalized["news"]
    symbols = tuple(symbol.upper() for symbol in normalized["symbols"]["allowlist"])
    persistence = normalized["persistence"]
    deployment = normalized["deployment"]

    return AppConfig(
        config_path=config_path,
        base_config_path=base_config_path,
        fingerprint=fingerprint,
        bot=BotConfig(
            mode=bot["mode"],
            profile=bot["profile"],
            expert_mode=bot["expert_mode"],
            poll_interval_seconds=bot["poll_interval_seconds"],
        ),
        symbols=SymbolsConfig(allowlist=symbols),
        alpha=AlphaConfig(setup_cooldown_bars=normalized["alpha"]["setup_cooldown_bars"]),
        regime=RegimeConfig(
            closed_bar_timeframe=normalized["regime"]["closed_bar_timeframe"],
            news_lockout_minutes=normalized["regime"]["news_lockout_minutes"],
        ),
        signal=SignalConfig(
            min_score=normalized["signal"]["min_score"],
            ttl_seconds=normalized["signal"]["ttl_seconds"],
        ),
        risk=RiskConfig(
            max_open_risk_pct=normalized["risk"]["max_open_risk_pct"],
            max_daily_loss_pct=normalized["risk"]["max_daily_loss_pct"],
        ),
        governor=GovernorConfig(
            initial_state=normalized["governor"]["initial_state"],
            kill_review_drawdown_pct=normalized["governor"]["kill_review_drawdown_pct"],
            offensive_profiles_require_expert_mode=normalized["governor"][
                "offensive_profiles_require_expert_mode"
            ],
        ),
        lifecycle=LifecycleConfig(
            thesis_ttl_minutes=normalized["lifecycle"]["thesis_ttl_minutes"],
            break_even_after_r=normalized["lifecycle"]["break_even_after_r"],
        ),
        execution=ExecutionConfig(
            signal_ttl_seconds=normalized["execution"]["signal_ttl_seconds"],
            slippage_veto_ratio=normalized["execution"]["slippage_veto_ratio"],
            max_spread_ratio=normalized["execution"]["max_spread_ratio"],
        ),
        telemetry=TelemetryConfig(
            heartbeat_interval_seconds=normalized["telemetry"]["heartbeat_interval_seconds"],
            emit_candidate_diagnostics=normalized["telemetry"]["emit_candidate_diagnostics"],
        ),
        persistence=PersistenceConfig(
            sqlite_path=_coerce_path(project_root, persistence["sqlite_path"]),
            lock_path=_coerce_path(project_root, persistence["lock_path"]),
            wal_enabled=persistence["wal_enabled"],
        ),
        contest=ContestConfig(
            ranking_proxy_enabled=normalized["contest"]["ranking_proxy_enabled"],
            contest_window_minutes=normalized["contest"]["contest_window_minutes"],
        ),
        deployment=DeploymentConfig(
            runtime_root=_coerce_path(project_root, deployment["runtime_root"]),
            log_root=_coerce_path(project_root, deployment["log_root"]),
            report_root=_coerce_path(project_root, deployment["report_root"]),
            allow_live_execution=deployment["allow_live_execution"],
        ),
        news=NewsConfig(
            provider_mode=news["provider_mode"],
            source_path=_coerce_optional_path(project_root, news["source_path"]),
            stale_warn_minutes=news["stale_warn_minutes"],
            stale_soft_fail_minutes=news["stale_soft_fail_minutes"],
            stale_hard_fail_minutes=news["stale_hard_fail_minutes"],
        ),
        secrets=secrets,
    )


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


def _tokenize_yaml(text: str) -> list[tuple[int, str, int]]:
    tokens: list[tuple[int, str, int]] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent % 2 != 0:
            raise ConfigValidationError(f"Invalid indentation at line {line_no}")
        line = _strip_inline_comment(raw_line).rstrip()
        if not line.strip():
            continue
        tokens.append((indent, line.lstrip(), line_no))
    return tokens


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    tokens = _tokenize_yaml(text)
    parsed, next_index = _parse_mapping(tokens, 0, 0)
    if next_index != len(tokens):
        _, _, line_no = tokens[next_index]
        raise ConfigValidationError(f"Unexpected trailing YAML tokens at line {line_no}")
    return parsed


def _parse_mapping(
    tokens: list[tuple[int, str, int]],
    index: int,
    indent: int,
) -> tuple[dict[str, Any], int]:
    mapping: dict[str, Any] = {}
    while index < len(tokens):
        token_indent, content, line_no = tokens[index]
        if token_indent < indent:
            break
        if token_indent > indent:
            raise ConfigValidationError(f"Unexpected indentation at line {line_no}")
        if content.startswith("- "):
            raise ConfigValidationError(f"List item not allowed here at line {line_no}")
        if ":" not in content:
            raise ConfigValidationError(f"Invalid YAML entry at line {line_no}: missing ':'")
        key_part, value_part = content.split(":", 1)
        key = key_part.strip()
        if not key:
            raise ConfigValidationError(f"Invalid YAML entry at line {line_no}: empty key")
        value_raw = value_part.strip()
        if value_raw:
            mapping[key] = _parse_scalar(value_raw)
            index += 1
            continue

        next_index = index + 1
        if next_index >= len(tokens):
            raise ConfigValidationError(f"Nested block required after '{key}' at line {line_no}")
        child_indent, child_content, child_line_no = tokens[next_index]
        if child_indent <= token_indent:
            raise ConfigValidationError(f"Nested block required after '{key}' at line {line_no}")
        if child_indent != token_indent + 2:
            raise ConfigValidationError(f"Invalid nested indentation at line {child_line_no}")

        if child_content.startswith("- "):
            child_value, index = _parse_list(tokens, next_index, child_indent)
        else:
            child_value, index = _parse_mapping(tokens, next_index, child_indent)
        mapping[key] = child_value
    return mapping, index


def _parse_list(
    tokens: list[tuple[int, str, int]],
    index: int,
    indent: int,
) -> tuple[list[Any], int]:
    items: list[Any] = []
    while index < len(tokens):
        token_indent, content, line_no = tokens[index]
        if token_indent < indent:
            break
        if token_indent > indent:
            raise ConfigValidationError(f"Unexpected indentation at line {line_no}")
        if not content.startswith("- "):
            break
        item_content = content[2:].strip()
        if not item_content:
            raise ConfigValidationError(f"List item must not be empty at line {line_no}")
        items.append(_parse_scalar(item_content))
        index += 1
    if not items:
        raise ConfigValidationError("List block must not be empty")
    return items, index


def _build_env_overrides() -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for env_key in sorted(os.environ):
        raw_value = os.environ[env_key]
        if not env_key.startswith(CONFIG_ENV_PREFIX):
            continue
        if env_key in SECRET_ENV_KEYS:
            continue
        payload = env_key[len(CONFIG_ENV_PREFIX) :]
        if "__" not in payload:
            continue
        section_key, field_key = payload.split("__", 1)
        section_name = section_key.lower()
        field_name = field_key.lower()
        if section_name not in CONFIG_SCHEMA:
            raise ConfigValidationError(
                f"Unknown environment override section '{section_name}' from {env_key}"
            )
        field_spec = next(
            (field for field in CONFIG_SCHEMA[section_name] if field.name == field_name),
            None,
        )
        if field_spec is None:
            raise ConfigValidationError(
                f"Unknown environment override field '{section_name}.{field_name}' from {env_key}"
            )
        parsed_raw = _parse_env_value(raw_value, field_spec.field_type)
        normalized = coerce_field_value(field_spec, parsed_raw, f"{section_name}.{field_name}")
        overrides.setdefault(section_name, {})[field_name] = normalized
    return overrides


def _parse_env_value(raw_value: str, field_type: str) -> Any:
    if raw_value.strip() == "":
        raise ConfigValidationError("Environment override value must not be blank")
    if field_type == "list[str]":
        values = [item.strip() for item in raw_value.split(",")]
        normalized_values = [item for item in values if item]
        if not normalized_values:
            raise ConfigValidationError("Environment list override must contain at least one item")
        return normalized_values
    return _parse_scalar(raw_value)


def _build_cli_overrides(cli_overrides: Mapping[str, Any]) -> dict[str, Any]:
    nested: dict[str, Any] = {}
    for dotted_key, raw_value in cli_overrides.items():
        if dotted_key not in CLI_OVERRIDE_FIELDS:
            raise ConfigValidationError(
                f"CLI override '{dotted_key}' is not permitted by governance"
            )
        section_name, field_name = dotted_key.split(".", 1)
        field_spec = next(
            (field for field in CONFIG_SCHEMA[section_name] if field.name == field_name),
            None,
        )
        if field_spec is None:
            raise ConfigValidationError(f"CLI override '{dotted_key}' has no schema field")
        normalized = coerce_field_value(field_spec, raw_value, dotted_key)
        nested.setdefault(section_name, {})[field_name] = normalized
    return nested


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, override_value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            merged[key] = _deep_merge(base_value, override_value)
        else:
            merged[key] = override_value
    return merged


def _coerce_path(project_root: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (project_root / path).resolve()


def _coerce_optional_path(project_root: Path, raw: str | None) -> Path | None:
    if raw is None:
        return None
    return _coerce_path(project_root, raw)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def app_config_to_dict(config: AppConfig) -> dict[str, Any]:
    """Debug-only serializer that excludes secrets from fingerprint governance."""
    materialized = asdict(config)
    materialized.pop("secrets", None)
    return materialized
