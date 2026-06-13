"""Execution orchestration for TSP V1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from time import perf_counter
from typing import Any, Optional, Protocol

from .config import ExecutionConfig
from .data_pipeline import SymbolContract
from .risk import RiskDecision
from .state import (
    ExecutionResult,
    ExecutionStatus,
    MarketSnapshot,
    Regime,
    RetcodeClass,
    RuntimeState,
    SignalScore,
)


SUCCESS_RETCODES = {10009}
PARTIAL_FILL_RETCODES = {10010}
RETRYABLE_RETCODES = {10004, 10020, 10021, 10031}
NON_RETRYABLE_RETCODES = {10006, 10013, 10014, 10016, 10019, 10026, 10027, 10030, 10032}


class RegistryStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"


class BrokerExecutionAdapter(Protocol):
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
        ...

    def modify_position(self, ticket: int, sl: float | None, tp: float | None) -> dict[str, Any]:
        ...

    def partial_close(self, ticket: int, symbol: str, volume: float, comment: str) -> dict[str, Any]:
        ...

    def get_position_by_ticket(self, ticket: int) -> Optional[dict[str, Any]]:
        ...

    def get_all_positions(self, magic: int) -> list[dict[str, Any]]:
        ...

    def emergency_close(self, ticket: int, symbol: str, volume: float, reason: str) -> dict[str, Any]:
        ...

    def get_symbol_info(self, symbol: str) -> Any:
        ...

    def get_server_time(self) -> datetime:
        ...

    def get_equity(self) -> float:
        ...


@dataclass(slots=True)
class ExecutionRegistryEntry:
    setup_id: str
    status: RegistryStatus
    created_at: datetime
    expires_at: datetime
    ticket: int | None = None


class ExecutionRegistry:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, ExecutionRegistryEntry] = {}

    def prune(self, now: datetime) -> None:
        current = _to_utc(now)
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= current]
        for key in expired:
            del self._entries[key]

    def get(self, setup_id: str, now: datetime) -> ExecutionRegistryEntry | None:
        self.prune(now)
        return self._entries.get(setup_id)

    def mark_pending(self, setup_id: str, now: datetime) -> None:
        current = _to_utc(now)
        self._entries[setup_id] = ExecutionRegistryEntry(
            setup_id=setup_id,
            status=RegistryStatus.PENDING,
            created_at=current,
            expires_at=current + timedelta(seconds=self.ttl_seconds),
        )

    def mark_completed(self, setup_id: str, now: datetime, ticket: int | None) -> None:
        current = _to_utc(now)
        self._entries[setup_id] = ExecutionRegistryEntry(
            setup_id=setup_id,
            status=RegistryStatus.COMPLETED,
            created_at=current,
            expires_at=current + timedelta(seconds=self.ttl_seconds),
            ticket=ticket,
        )

    def clear(self, setup_id: str) -> None:
        self._entries.pop(setup_id, None)

    def snapshot(self, now: datetime) -> tuple[ExecutionRegistryEntry, ...]:
        self.prune(now)
        return tuple(self._entries.values())

    def restore(self, entries: list[ExecutionRegistryEntry], now: datetime) -> None:
        self._entries.clear()
        current = _to_utc(now)
        for entry in entries:
            normalized = ExecutionRegistryEntry(
                setup_id=entry.setup_id,
                status=entry.status,
                created_at=_to_utc(entry.created_at),
                expires_at=_to_utc(entry.expires_at),
                ticket=entry.ticket,
            )
            if normalized.expires_at > current:
                self._entries[normalized.setup_id] = normalized


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _retcode_class(retcode: int | None) -> RetcodeClass:
    if retcode in SUCCESS_RETCODES or retcode in PARTIAL_FILL_RETCODES:
        return RetcodeClass.SUCCESS
    if retcode in RETRYABLE_RETCODES:
        return RetcodeClass.RETRYABLE
    if retcode in NON_RETRYABLE_RETCODES:
        return RetcodeClass.NON_RETRYABLE
    return RetcodeClass.UNKNOWN


def _slippage_ticks(fill_price: float | None, expected_price: float, contract: SymbolContract) -> float | None:
    if fill_price is None or contract.tick_size <= 0.0:
        return None
    return abs(fill_price - expected_price) / contract.tick_size


def _result(
    *,
    status: ExecutionStatus,
    runtime: RuntimeState,
    signal: SignalScore,
    note: str,
    attempt_count: int = 0,
    ticket: int | None = None,
    fill_price: float | None = None,
    fill_lot: float | None = None,
    sl_confirmed: bool = False,
    tp_confirmed: bool = False,
    retcode: int | None = None,
    expected_price: float | None = None,
    contract: SymbolContract | None = None,
    latency_ms: float | None = None,
) -> ExecutionResult:
    slippage = None
    if expected_price is not None and contract is not None:
        slippage = _slippage_ticks(fill_price, expected_price, contract)
    return ExecutionResult(
        status=status,
        ticket=ticket,
        fill_price=fill_price,
        fill_lot=fill_lot,
        sl_confirmed=sl_confirmed,
        tp_confirmed=tp_confirmed,
        retcode=retcode,
        retcode_class=_retcode_class(retcode),
        slippage_ticks=slippage,
        latency_ms=latency_ms,
        setup_id=signal.setup_id,
        attempt_count=attempt_count,
        timestamp=_to_utc(signal.signal_timestamp if latency_ms is None else datetime.now(timezone.utc)),
        note=note,
    )


def validate_execution(
    signal: SignalScore,
    snap: MarketSnapshot,
    regime: Regime,
    runtime: RuntimeState,
    contract: SymbolContract,
    cfg: ExecutionConfig,
    registry: ExecutionRegistry,
    *,
    now: datetime,
) -> ExecutionResult | None:
    current = _to_utc(now)
    entry = registry.get(signal.setup_id, current)
    if entry is not None:
        return _result(
            status=ExecutionStatus.DUPLICATE,
            runtime=runtime,
            signal=signal,
            note=f"registry_{entry.status.value.lower()}",
        )

    age_seconds = max(0.0, (current - _to_utc(signal.signal_timestamp)).total_seconds())
    if age_seconds > cfg.signal_ttl_seconds:
        return _result(
            status=ExecutionStatus.STALE_SIGNAL,
            runtime=runtime,
            signal=signal,
            note="signal_ttl_exceeded",
        )

    spread_ratio = snap.spread_current / snap.spread_baseline if snap.spread_baseline > 0 else 0.0
    if spread_ratio > cfg.spread_hard_veto_ratio:
        return _result(
            status=ExecutionStatus.SPREAD_VETOED,
            runtime=runtime,
            signal=signal,
            note="spread_hard_veto",
        )

    entry_price = snap.ask if signal.direction.name == "LONG" else snap.bid
    sl_distance = abs(entry_price - signal.invalidation_anchor)
    sl_distance_ticks = sl_distance / contract.tick_size if contract.tick_size > 0 else 0.0
    if sl_distance_ticks < contract.stops_level:
        return _result(
            status=ExecutionStatus.INVALID_PARAMS,
            runtime=runtime,
            signal=signal,
            note="sl_distance_below_stops_level",
        )

    if runtime.kill_switch_active:
        return _result(
            status=ExecutionStatus.INVALID_PARAMS,
            runtime=runtime,
            signal=signal,
            note="kill_switch_active",
        )

    if regime in {Regime.NEWS_DEAD, Regime.CHOP}:
        return _result(
            status=ExecutionStatus.INVALID_PARAMS,
            runtime=runtime,
            signal=signal,
            note="regime_lockout",
        )
    return None


def execute_order(
    adapter: BrokerExecutionAdapter,
    registry: ExecutionRegistry,
    *,
    signal: SignalScore,
    decision: RiskDecision,
    snap: MarketSnapshot,
    runtime: RuntimeState,
    regime: Regime,
    contract: SymbolContract,
    cfg: ExecutionConfig,
) -> ExecutionResult:
    now = adapter.get_server_time()
    validation = validate_execution(
        signal,
        snap,
        regime,
        runtime,
        contract,
        cfg,
        registry,
        now=now,
    )
    if validation is not None:
        return validation

    if decision.action not in {"ENTER", "PYRAMID"}:
        return _result(
            status=ExecutionStatus.INVALID_PARAMS,
            runtime=runtime,
            signal=signal,
            note=f"unsupported_decision_{decision.action.lower()}",
        )

    registry.mark_pending(signal.setup_id, now)
    start = perf_counter()
    try:
        side = "BUY" if signal.direction.name == "LONG" else "SELL"
        response = adapter.send_market_order(
            symbol=snap.symbol,
            action=side,
            volume=decision.lot_size,
            sl=decision.invalidation_price,
            tp=None,
            comment=signal.module.name,
            magic=runtime.magic,
        )
        latency_ms = (perf_counter() - start) * 1000.0
        retcode = int(response.get("retcode")) if response.get("retcode") is not None else None
        fill_price = float(response["price"]) if response.get("price") is not None else None
        fill_lot = float(response["volume"]) if response.get("volume") is not None else decision.lot_size
        ticket = response.get("deal") or response.get("order")
        ticket_int = int(ticket) if ticket is not None else None
        expected_price = decision.entry_price

        if retcode in SUCCESS_RETCODES:
            registry.mark_completed(signal.setup_id, now, ticket_int)
            return _result(
                status=ExecutionStatus.FILLED,
                runtime=runtime,
                signal=signal,
                note="filled",
                attempt_count=1,
                ticket=ticket_int,
                fill_price=fill_price,
                fill_lot=fill_lot,
                sl_confirmed=True,
                tp_confirmed=False,
                retcode=retcode,
                expected_price=expected_price,
                contract=contract,
                latency_ms=latency_ms,
            )

        if retcode in PARTIAL_FILL_RETCODES:
            registry.mark_completed(signal.setup_id, now, ticket_int)
            return _result(
                status=ExecutionStatus.PARTIAL_FILL,
                runtime=runtime,
                signal=signal,
                note="partial_fill",
                attempt_count=1,
                ticket=ticket_int,
                fill_price=fill_price,
                fill_lot=fill_lot,
                sl_confirmed=True,
                tp_confirmed=False,
                retcode=retcode,
                expected_price=expected_price,
                contract=contract,
                latency_ms=latency_ms,
            )

        if ticket_int is not None and adapter.get_position_by_ticket(ticket_int) is not None:
            registry.mark_completed(signal.setup_id, now, ticket_int)
            return _result(
                status=ExecutionStatus.FILLED_UNVERIFIED,
                runtime=runtime,
                signal=signal,
                note="ticket_exists_verify_failed",
                attempt_count=1,
                ticket=ticket_int,
                fill_price=fill_price,
                fill_lot=fill_lot,
                sl_confirmed=False,
                tp_confirmed=False,
                retcode=retcode,
                expected_price=expected_price,
                contract=contract,
                latency_ms=latency_ms,
            )

        registry.clear(signal.setup_id)
        if retcode in RETRYABLE_RETCODES:
            return _result(
                status=ExecutionStatus.TIMEOUT,
                runtime=runtime,
                signal=signal,
                note="retryable_execution_failure",
                attempt_count=1,
                retcode=retcode,
                expected_price=expected_price,
                contract=contract,
                latency_ms=latency_ms,
            )

        if retcode in NON_RETRYABLE_RETCODES:
            return _result(
                status=ExecutionStatus.REJECTED,
                runtime=runtime,
                signal=signal,
                note="non_retryable_execution_failure",
                attempt_count=1,
                retcode=retcode,
                expected_price=expected_price,
                contract=contract,
                latency_ms=latency_ms,
            )

        return _result(
            status=ExecutionStatus.MT5_ERROR,
            runtime=runtime,
            signal=signal,
            note="unknown_execution_failure",
            attempt_count=1,
            retcode=retcode,
            expected_price=expected_price,
            contract=contract,
            latency_ms=latency_ms,
        )
    except Exception:
        registry.clear(signal.setup_id)
        raise


__all__ = [
    "BrokerExecutionAdapter",
    "ExecutionRegistry",
    "ExecutionRegistryEntry",
    "RegistryStatus",
    "execute_order",
    "validate_execution",
]
