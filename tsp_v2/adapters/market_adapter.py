"""Market adapter implementation for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from ..config_schema import ConfigValidationError
from ..enums import HealthState
from ..market_data import MarketDataProvider
from .mt5_bridge import MT5Bridge, MT5BridgeError, MT5BridgeStatus


class MarketAdapter(MarketDataProvider, Protocol):
    """Alias protocol for clearer dependency wiring."""


@dataclass(frozen=True, slots=True)
class MarketAdapterStatus:
    ok: bool
    health: HealthState
    response_class: str
    failure_class: str
    message: str
    symbol: str
    broker_time_utc: datetime | None
    tick_age_seconds: float | None = None
    spread_points: float | None = None
    heartbeat: MT5BridgeStatus | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MT5MarketAdapter:
    bridge: MT5Bridge
    primary_symbol: str
    stale_warn_seconds: float = 5.0
    stale_hard_seconds: float = 10.0
    last_status: MarketAdapterStatus | None = None

    def __post_init__(self) -> None:
        symbol = str(self.primary_symbol).strip().upper()
        if not symbol:
            raise ConfigValidationError("primary_symbol must be a non-empty string")
        if self.stale_warn_seconds < 0.0:
            raise ConfigValidationError("stale_warn_seconds must be non-negative")
        if self.stale_hard_seconds < self.stale_warn_seconds:
            raise ConfigValidationError("stale_hard_seconds must be >= stale_warn_seconds")
        self.primary_symbol = symbol

    def get_broker_time(self) -> datetime:
        return self._get_broker_time_for_symbol(self.primary_symbol)

    def get_latest_tick(self, symbol: str) -> dict[str, Any]:
        return self.bridge.get_latest_tick(symbol)

    def get_rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, Any]]:
        return list(self.bridge.get_rates(symbol, timeframe, count))

    def get_symbol_contract(self, symbol: str) -> dict[str, Any]:
        return self._normalize_contract(self.bridge.query_symbol_contract(symbol))

    def market_status(
        self,
        symbol: str | None = None,
        *,
        now_utc: datetime | None = None,
    ) -> MarketAdapterStatus:
        target_symbol = self._resolve_symbol(symbol)
        heartbeat = self.bridge.heartbeat()
        current_time = _ensure_utc(now_utc or datetime.now(tz=timezone.utc), field_name="now_utc")
        if not heartbeat.ok:
            status = MarketAdapterStatus(
                ok=False,
                health=HealthState.RED,
                response_class=heartbeat.response_class,
                failure_class=heartbeat.failure_class,
                message=heartbeat.message,
                symbol=target_symbol,
                broker_time_utc=None,
                heartbeat=heartbeat,
                diagnostics={"heartbeat": heartbeat.to_payload()},
            )
            self.last_status = status
            return status

        try:
            tick = self.bridge.get_latest_tick(target_symbol)
            contract = self.bridge.query_symbol_contract(target_symbol)
        except MT5BridgeError as exc:
            status = MarketAdapterStatus(
                ok=False,
                health=HealthState.RED,
                response_class=exc.status.response_class,
                failure_class=exc.status.failure_class,
                message=exc.status.message,
                symbol=target_symbol,
                broker_time_utc=None,
                heartbeat=heartbeat,
                diagnostics={
                    "heartbeat": heartbeat.to_payload(),
                    "bridge_error": exc.status.to_payload(),
                },
            )
            self.last_status = status
            return status

        broker_time = _ensure_utc(tick["timestamp"], field_name="tick.timestamp")
        spread_points = self._spread_points_from_tick(tick, contract)
        tick_age_seconds = max(0.0, (current_time - broker_time).total_seconds())
        if tick_age_seconds > self.stale_hard_seconds:
            health = HealthState.RED
        elif tick_age_seconds > self.stale_warn_seconds:
            health = HealthState.YELLOW
        else:
            health = HealthState.GREEN

        status = MarketAdapterStatus(
            ok=health is HealthState.GREEN,
            health=health,
            response_class="OK" if health is HealthState.GREEN else "DEGRADE_SYMBOL",
            failure_class="" if health is HealthState.GREEN else "SYMBOL_STALE",
            message="MT5 market adapter healthy" if health is HealthState.GREEN else "MT5 market data stale",
            symbol=target_symbol,
            broker_time_utc=broker_time,
            tick_age_seconds=tick_age_seconds,
            spread_points=spread_points,
            heartbeat=heartbeat,
            diagnostics={
                "heartbeat": heartbeat.to_payload(),
                "tick": _json_ready(tick),
                "contract": self._normalize_contract(contract),
                "tick_age_seconds": tick_age_seconds,
                "spread_points": spread_points,
            },
        )
        self.last_status = status
        return status

    def symbol_metadata(self, symbol: str) -> dict[str, Any]:
        return self.get_symbol_contract(symbol)

    def contract_normalization(self, symbol: str) -> dict[str, Any]:
        return self.get_symbol_contract(symbol)

    def _resolve_symbol(self, symbol: str | None) -> str:
        candidate = self.primary_symbol if symbol is None else str(symbol).strip()
        if not candidate:
            raise ConfigValidationError("symbol must be a non-empty string")
        return candidate.upper()

    def _get_broker_time_for_symbol(self, symbol: str) -> datetime:
        tick = self.bridge.get_latest_tick(symbol)
        return _ensure_utc(tick["timestamp"], field_name="tick.timestamp")

    def _normalize_contract(self, contract: Mapping[str, Any]) -> dict[str, Any]:
        symbol = str(contract.get("symbol", self.primary_symbol)).strip().upper() or self.primary_symbol
        point = float(contract["point"])
        tick_size = float(contract.get("trade_tick_size", contract.get("tick_size", point)))
        tick_value = float(contract.get("trade_tick_value", contract.get("tick_value", 0.0)))
        min_lot = float(contract.get("volume_min", contract.get("min_lot", 0.0)))
        max_lot = float(contract.get("volume_max", contract.get("max_lot", 0.0)))
        lot_step = float(contract.get("volume_step", contract.get("lot_step", 0.0)))
        stop_level = int(contract.get("trade_stops_level", contract.get("stop_level_points", 0)))
        freeze_level = int(contract.get("trade_freeze_level", contract.get("freeze_level_points", 0)))
        return {
            "symbol": symbol,
            "visible": bool(contract.get("visible", True)),
            "point": point,
            "trade_tick_size": tick_size,
            "trade_tick_value": tick_value,
            "volume_min": min_lot,
            "volume_max": max_lot,
            "volume_step": lot_step,
            "trade_stops_level": stop_level,
            "trade_freeze_level": freeze_level,
            "tick_size": tick_size,
            "tick_value": tick_value,
            "min_lot": min_lot,
            "max_lot": max_lot,
            "lot_step": lot_step,
            "stop_level_points": stop_level,
            "freeze_level_points": freeze_level,
            "spread_points": float(contract.get("spread", 0.0)),
            "digits": int(contract.get("digits", 0)),
        }

    def _spread_points_from_tick(self, tick: Mapping[str, Any], contract: Mapping[str, Any]) -> float:
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        point = float(contract["point"])
        if point <= 0.0:
            raise ConfigValidationError("Contract point must be positive for market adapter status")
        return (ask - bid) / point


def _ensure_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ConfigValidationError(f"{field_name} must be timezone-aware UTC datetime")
    return value.astimezone(timezone.utc)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value
