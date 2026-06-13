"""Execution adapter implementation for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol

from ..config_schema import ConfigValidationError
from ..enums import Direction, ExecutionRegistryState
from ..execution import (
    ExecutionRegistryBook,
    classify_broker_response,
)
from ..models import ExecutionIntent, ExecutionResult
from .mt5_bridge import MT5Bridge, MT5BridgeError, MT5TradeResult


class ExecutionAdapter(Protocol):
    def execute(self, intent: ExecutionIntent) -> ExecutionResult:
        ...

    def query_positions(self) -> list[dict[str, Any]]:
        ...


@dataclass(slots=True)
class MT5ExecutionAdapter:
    bridge: MT5Bridge
    registry: ExecutionRegistryBook | None = None
    default_deviation: int = 20
    last_result: ExecutionResult | None = None

    def __post_init__(self) -> None:
        if self.default_deviation < 0:
            raise ConfigValidationError("default_deviation must be non-negative")
        if self.registry is None:
            self.registry = ExecutionRegistryBook()

    def execute(self, intent: ExecutionIntent, *, at_utc: datetime | None = None) -> ExecutionResult:
        canonical_time = _ensure_utc(at_utc or intent.cycle_time_utc, field_name="at_utc")
        self._validate_intent(intent, canonical_time)
        assert self.registry is not None

        if self.registry.is_duplicate(intent=intent, at_utc=canonical_time):
            result = self._build_reject_result(
                intent,
                broker_code="DUPLICATE_INTENT",
                classification="BLOCK_EXECUTION",
                message="Duplicate execution intent rejected by registry",
                retryable=False,
                fatal=False,
                terminal=True,
                at_utc=canonical_time,
            )
            self.last_result = result
            return result

        self.registry.reserve(intent, at_utc=canonical_time)
        request = self._build_order_request(intent)
        try:
            bridge_result = self.bridge.place_order(request)
        except MT5BridgeError as exc:
            bridge_result = MT5TradeResult(
                ok=False,
                failure_class=exc.status.failure_class,
                response_class=exc.status.response_class,
                retryable=exc.status.retryable,
                fatal=exc.status.fatal,
                terminal=False,
                message=exc.status.message,
                request=dict(request),
                response={},
                diagnostics=exc.status.diagnostics,
            )
        disposition = classify_broker_response(bridge_result.response)
        result = self._build_execution_result(intent, bridge_result, disposition, request=request)
        self._update_registry(intent, bridge_result, disposition, at_utc=canonical_time)
        self.last_result = result
        return result

    def place_order(self, intent: ExecutionIntent, *, at_utc: datetime | None = None) -> ExecutionResult:
        return self.execute(intent, at_utc=at_utc)

    def query_positions(self) -> list[dict[str, Any]]:
        return list(self.bridge.query_positions())

    def _validate_intent(self, intent: ExecutionIntent, at_utc: datetime) -> None:
        if intent.sized_volume <= 0.0:
            raise ConfigValidationError("Execution intent sized_volume must be positive")
        if intent.decision_price <= 0.0:
            raise ConfigValidationError("Execution intent decision_price must be positive")
        if not intent.setup_id.strip():
            raise ConfigValidationError("Execution intent setup_id must be non-empty")
        if not intent.submission_uuid.strip():
            raise ConfigValidationError("Execution intent submission_uuid must be non-empty")
        if intent.symbol.upper() != intent.symbol.strip().upper():
            raise ConfigValidationError("Execution intent symbol must be canonical uppercase")
        ttl_seconds = _ttl_for_signal_family(intent.signal_family)
        if at_utc > intent.cycle_time_utc + timedelta(seconds=ttl_seconds):
            raise ConfigValidationError("Execution intent is stale")
        contract = self.bridge.query_symbol_contract(intent.symbol)
        self._validate_contract_volume(contract, intent.sized_volume)

    def _validate_contract_volume(self, contract: Mapping[str, Any], volume: float) -> None:
        min_lot = float(contract.get("volume_min", contract.get("min_lot", 0.0)))
        max_lot = float(contract.get("volume_max", contract.get("max_lot", 0.0)))
        lot_step = float(contract.get("volume_step", contract.get("lot_step", 0.0)))
        if min_lot > 0.0 and volume < min_lot:
            raise ConfigValidationError("Execution intent volume below contract minimum")
        if max_lot > 0.0 and volume > max_lot:
            raise ConfigValidationError("Execution intent volume above contract maximum")
        if lot_step > 0.0:
            steps = round(volume / lot_step)
            normalized = round(steps * lot_step, 8)
            if abs(normalized - volume) > 1e-8:
                raise ConfigValidationError("Execution intent volume does not align with contract lot_step")

    def _build_order_request(self, intent: ExecutionIntent) -> dict[str, Any]:
        mt5 = self.bridge.mt5_module
        buy = getattr(mt5, "ORDER_TYPE_BUY", "BUY") if mt5 is not None else "BUY"
        sell = getattr(mt5, "ORDER_TYPE_SELL", "SELL") if mt5 is not None else "SELL"
        action = getattr(mt5, "TRADE_ACTION_DEAL", "TRADE_ACTION_DEAL") if mt5 is not None else "TRADE_ACTION_DEAL"
        type_time = getattr(mt5, "ORDER_TIME_GTC", "ORDER_TIME_GTC") if mt5 is not None else "ORDER_TIME_GTC"
        default_filling = _default_filling_mode(mt5)
        order_type = buy if intent.direction is Direction.LONG else sell
        request: dict[str, Any] = {
            "action": action,
            "symbol": intent.symbol,
            "volume": intent.sized_volume,
            "type": order_type,
            "price": intent.decision_price,
            "deviation": self.default_deviation,
            "comment": f"TSP_V2|{intent.setup_id}|{intent.submission_uuid}",
            "type_time": type_time,
        }
        if default_filling is not None:
            request["type_filling"] = default_filling
        return request

    def _build_execution_result(
        self,
        intent: ExecutionIntent,
        bridge_result: MT5TradeResult,
        disposition,
        *,
        request: Mapping[str, Any],
    ) -> ExecutionResult:
        accepted = bool(bridge_result.ok)
        rejected = not accepted
        filled = accepted and disposition.suggested_state is ExecutionRegistryState.FILLED
        partial_fill = accepted and disposition.suggested_state is ExecutionRegistryState.PARTIAL
        broker_code = bridge_result.failure_class or disposition.reason or ""
        classification = bridge_result.response_class or disposition.diagnostics.get("classification", "")
        diagnostics = {
            "bridge": bridge_result.to_payload(),
            "disposition": disposition.diagnostics,
        }
        return ExecutionResult(
            accepted=accepted,
            rejected=rejected,
            filled=filled,
            partial_fill=partial_fill,
            ticket=bridge_result.ticket or disposition.broker_ticket,
            broker_code=broker_code,
            classification=classification,
            retryable=bool(bridge_result.retryable or disposition.retryable),
            fatal=bool(bridge_result.fatal),
            terminal=bool(bridge_result.terminal or disposition.terminal),
            message=bridge_result.message,
            submission_uuid=intent.submission_uuid,
            setup_id=intent.setup_id,
            symbol=intent.symbol,
            request=dict(request),
            response=dict(bridge_result.response),
            diagnostics=diagnostics,
            registry_state=disposition.suggested_state,
        )

    def _build_reject_result(
        self,
        intent: ExecutionIntent,
        *,
        broker_code: str,
        classification: str,
        message: str,
        retryable: bool,
        fatal: bool,
        terminal: bool,
        at_utc: datetime,
    ) -> ExecutionResult:
        del at_utc
        return ExecutionResult(
            accepted=False,
            rejected=True,
            filled=False,
            partial_fill=False,
            ticket=None,
            broker_code=broker_code,
            classification=classification,
            retryable=retryable,
            fatal=fatal,
            terminal=terminal,
            message=message,
            submission_uuid=intent.submission_uuid,
            setup_id=intent.setup_id,
            symbol=intent.symbol,
            request={},
            response={},
            diagnostics={},
            registry_state=ExecutionRegistryState.REJECTED,
        )

    def _update_registry(
        self,
        intent: ExecutionIntent,
        bridge_result: MT5TradeResult,
        disposition,
        *,
        at_utc: datetime,
    ) -> None:
        assert self.registry is not None
        ticket = bridge_result.ticket or disposition.broker_ticket
        if bridge_result.ok:
            self.registry.mark_submitted(intent.submission_uuid, at_utc=at_utc, broker_ticket=ticket)
            if disposition.suggested_state is ExecutionRegistryState.PARTIAL:
                self.registry.mark_partial(intent.submission_uuid, at_utc=at_utc, broker_ticket=ticket)
            elif disposition.suggested_state is ExecutionRegistryState.FILLED:
                self.registry.mark_filled(intent.submission_uuid, at_utc=at_utc, broker_ticket=ticket)
            else:
                self.registry.mark_acknowledged(intent.submission_uuid, at_utc=at_utc, broker_ticket=ticket)
            return

        if disposition.retryable:
            self.registry.mark_submitted(intent.submission_uuid, at_utc=at_utc, broker_ticket=ticket)
            return
        if disposition.suggested_state is ExecutionRegistryState.REJECTED:
            self.registry.mark_rejected(intent.submission_uuid, at_utc=at_utc, broker_ticket=ticket)
            return
        if disposition.suggested_state is ExecutionRegistryState.CANCELLED:
            self.registry.mark_cancelled(intent.submission_uuid, at_utc=at_utc, broker_ticket=ticket)
            return
        if disposition.suggested_state is ExecutionRegistryState.EXPIRED:
            self.registry.mark_expired(intent.submission_uuid, at_utc=at_utc, broker_ticket=ticket)
            return
        self.registry.mark_ambiguous(intent.submission_uuid, at_utc=at_utc, broker_ticket=ticket)


def _ttl_for_signal_family(family: Any) -> int:
    name = getattr(family, "value", str(family))
    if name == "TREND_CONTINUATION":
        return 120
    if name == "BREAKOUT_MOMENTUM":
        return 90
    return 90


def _default_filling_mode(mt5: Any) -> Any | None:
    if mt5 is None:
        return None
    for name in ("ORDER_FILLING_RETURN", "ORDER_FILLING_FOK", "ORDER_FILLING_IOC"):
        if hasattr(mt5, name):
            return getattr(mt5, name)
    return None


def _ensure_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ConfigValidationError(f"{field_name} must be timezone-aware UTC datetime")
    return value.astimezone(timezone.utc)
