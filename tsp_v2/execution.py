"""Execution orchestration and idempotency for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Iterable, Mapping

from .config_schema import ConfigValidationError
from .enums import Direction, ExecutionRegistryState, GovernorState, HealthState, RiskAction, SignalFamily
from .models import ExecutionIntent, ExecutionRegistryEntry, GovernorDecision, MarketSnapshot, RiskDecision, SignalDecision


SYMBOL_LOCK_SECONDS = 15
RETRYABLE_FAILURE_CODES = {
    "TRADE_CONTEXT_BUSY",
    "NETWORK_TIMEOUT",
    "TEMPORARY_DISCONNECT",
    "REQUOTE",
    "PRICE_CHANGED",
    "OFF_QUOTES",
}
NON_RETRYABLE_FAILURE_CODES = {
    "INVALID_VOLUME",
    "INVALID_STOPS",
    "SYMBOL_DISABLED",
    "MARKET_CLOSED",
    "TRADE_DISABLED",
    "NOT_ENOUGH_MONEY",
    "NO_MONEY",
    "INVALID",
}
SUCCESS_CODES = {
    "DONE",
    "FILLED",
    "DONE_PARTIAL",
    "ACCEPTED",
    "ACKNOWLEDGED",
    "PLACED",
}
ALLOWED_ENTRY_STATES = {
    GovernorState.NORMAL,
    GovernorState.ATTACK,
    GovernorState.HUNTER,
    GovernorState.CHASE,
    GovernorState.SPRINT,
}
ALLOWED_RISK_ACTIONS = {
    RiskAction.ENTER,
    RiskAction.SCALE,
    RiskAction.PYRAMID,
}
TRANSITION_ALLOWED: dict[ExecutionRegistryState, frozenset[ExecutionRegistryState]] = {
    ExecutionRegistryState.PENDING: frozenset(
        {
            ExecutionRegistryState.SUBMITTED,
            ExecutionRegistryState.ACKNOWLEDGED,
            ExecutionRegistryState.PARTIAL,
            ExecutionRegistryState.FILLED,
            ExecutionRegistryState.REJECTED,
            ExecutionRegistryState.CANCELLED,
            ExecutionRegistryState.EXPIRED,
            ExecutionRegistryState.AMBIGUOUS,
        }
    ),
    ExecutionRegistryState.SUBMITTED: frozenset(
        {
            ExecutionRegistryState.ACKNOWLEDGED,
            ExecutionRegistryState.PARTIAL,
            ExecutionRegistryState.FILLED,
            ExecutionRegistryState.REJECTED,
            ExecutionRegistryState.CANCELLED,
            ExecutionRegistryState.EXPIRED,
            ExecutionRegistryState.AMBIGUOUS,
        }
    ),
    ExecutionRegistryState.ACKNOWLEDGED: frozenset(
        {
            ExecutionRegistryState.PARTIAL,
            ExecutionRegistryState.FILLED,
            ExecutionRegistryState.REJECTED,
            ExecutionRegistryState.CANCELLED,
            ExecutionRegistryState.EXPIRED,
            ExecutionRegistryState.AMBIGUOUS,
        }
    ),
    ExecutionRegistryState.PARTIAL: frozenset(
        {
            ExecutionRegistryState.FILLED,
            ExecutionRegistryState.REJECTED,
            ExecutionRegistryState.CANCELLED,
            ExecutionRegistryState.EXPIRED,
            ExecutionRegistryState.AMBIGUOUS,
        }
    ),
    ExecutionRegistryState.FILLED: frozenset(),
    ExecutionRegistryState.REJECTED: frozenset(),
    ExecutionRegistryState.CANCELLED: frozenset(),
    ExecutionRegistryState.EXPIRED: frozenset(),
    ExecutionRegistryState.AMBIGUOUS: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ExecutionValidationResult:
    accepted: bool
    intent: ExecutionIntent | None
    reject_reason: str
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BrokerDisposition:
    suggested_state: ExecutionRegistryState
    retryable: bool
    terminal: bool
    reason: str
    broker_ticket: int | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionRegistryBook:
    entries_by_setup_id: dict[str, ExecutionRegistryEntry] = field(default_factory=dict)
    entries_by_submission_uuid: dict[str, ExecutionRegistryEntry] = field(default_factory=dict)
    symbol_locks_until_utc: dict[str, datetime] = field(default_factory=dict)

    def is_duplicate(self, *, intent: ExecutionIntent, at_utc: datetime) -> bool:
        canonical_time = _ensure_utc(at_utc, field_name="at_utc")
        if self._is_symbol_locked(intent.symbol, canonical_time):
            return True
        entry = self.entries_by_setup_id.get(intent.setup_id)
        if entry is not None and entry.state not in {ExecutionRegistryState.EXPIRED, ExecutionRegistryState.CANCELLED}:
            return True
        entry = self.entries_by_submission_uuid.get(intent.submission_uuid)
        if entry is not None and entry.state not in {ExecutionRegistryState.EXPIRED, ExecutionRegistryState.CANCELLED}:
            return True
        return False

    def reserve(self, intent: ExecutionIntent, *, at_utc: datetime) -> ExecutionRegistryEntry:
        canonical_time = _ensure_utc(at_utc, field_name="at_utc")
        if self.is_duplicate(intent=intent, at_utc=canonical_time):
            raise ConfigValidationError("Duplicate execution intent rejected by registry")
        entry = ExecutionRegistryEntry(
            setup_id=intent.setup_id,
            submission_uuid=intent.submission_uuid,
            symbol=intent.symbol,
            state=ExecutionRegistryState.PENDING,
            updated_at_utc=canonical_time,
            direction=intent.direction,
            decision_price=intent.decision_price,
            cycle_time_utc=intent.cycle_time_utc,
            expires_at_utc=intent.cycle_time_utc + timedelta(seconds=_ttl_for_signal_family(intent.signal_family)),
        )
        self._store(entry)
        self._set_symbol_lock(intent.symbol, canonical_time)
        return entry

    def mark_submitted(
        self,
        submission_uuid: str,
        *,
        at_utc: datetime,
        broker_ticket: int | None = None,
    ) -> ExecutionRegistryEntry:
        return self.transition(
            submission_uuid,
            ExecutionRegistryState.SUBMITTED,
            at_utc=at_utc,
            broker_ticket=broker_ticket,
        )

    def mark_acknowledged(
        self,
        submission_uuid: str,
        *,
        at_utc: datetime,
        broker_ticket: int | None = None,
    ) -> ExecutionRegistryEntry:
        return self.transition(
            submission_uuid,
            ExecutionRegistryState.ACKNOWLEDGED,
            at_utc=at_utc,
            broker_ticket=broker_ticket,
        )

    def mark_partial(
        self,
        submission_uuid: str,
        *,
        at_utc: datetime,
        broker_ticket: int | None = None,
    ) -> ExecutionRegistryEntry:
        return self.transition(
            submission_uuid,
            ExecutionRegistryState.PARTIAL,
            at_utc=at_utc,
            broker_ticket=broker_ticket,
        )

    def mark_filled(
        self,
        submission_uuid: str,
        *,
        at_utc: datetime,
        broker_ticket: int | None = None,
    ) -> ExecutionRegistryEntry:
        return self.transition(
            submission_uuid,
            ExecutionRegistryState.FILLED,
            at_utc=at_utc,
            broker_ticket=broker_ticket,
        )

    def mark_rejected(
        self,
        submission_uuid: str,
        *,
        at_utc: datetime,
        broker_ticket: int | None = None,
    ) -> ExecutionRegistryEntry:
        return self.transition(
            submission_uuid,
            ExecutionRegistryState.REJECTED,
            at_utc=at_utc,
            broker_ticket=broker_ticket,
        )

    def mark_cancelled(
        self,
        submission_uuid: str,
        *,
        at_utc: datetime,
        broker_ticket: int | None = None,
    ) -> ExecutionRegistryEntry:
        return self.transition(
            submission_uuid,
            ExecutionRegistryState.CANCELLED,
            at_utc=at_utc,
            broker_ticket=broker_ticket,
        )

    def mark_expired(
        self,
        submission_uuid: str,
        *,
        at_utc: datetime,
        broker_ticket: int | None = None,
    ) -> ExecutionRegistryEntry:
        return self.transition(
            submission_uuid,
            ExecutionRegistryState.EXPIRED,
            at_utc=at_utc,
            broker_ticket=broker_ticket,
        )

    def mark_ambiguous(
        self,
        submission_uuid: str,
        *,
        at_utc: datetime,
        broker_ticket: int | None = None,
    ) -> ExecutionRegistryEntry:
        return self.transition(
            submission_uuid,
            ExecutionRegistryState.AMBIGUOUS,
            at_utc=at_utc,
            broker_ticket=broker_ticket,
        )

    def transition(
        self,
        submission_uuid: str,
        new_state: ExecutionRegistryState,
        *,
        at_utc: datetime,
        broker_ticket: int | None = None,
    ) -> ExecutionRegistryEntry:
        canonical_time = _ensure_utc(at_utc, field_name="at_utc")
        entry = self.entries_by_submission_uuid.get(submission_uuid)
        if entry is None:
            raise ConfigValidationError(f"Unknown execution submission UUID: {submission_uuid}")
        if new_state == entry.state:
            updated = ExecutionRegistryEntry(
                setup_id=entry.setup_id,
                submission_uuid=entry.submission_uuid,
                symbol=entry.symbol,
                state=entry.state,
                updated_at_utc=canonical_time,
                direction=entry.direction,
                decision_price=entry.decision_price,
                cycle_time_utc=entry.cycle_time_utc,
                expires_at_utc=entry.expires_at_utc,
                broker_ticket=broker_ticket if broker_ticket is not None else entry.broker_ticket,
            )
            self._store(updated)
            if new_state in {ExecutionRegistryState.FILLED, ExecutionRegistryState.REJECTED, ExecutionRegistryState.CANCELLED, ExecutionRegistryState.EXPIRED}:
                self._release_symbol_lock(updated.symbol)
            else:
                self._set_symbol_lock(updated.symbol, canonical_time)
            return updated
        if new_state not in TRANSITION_ALLOWED.get(entry.state, frozenset()):
            raise ConfigValidationError(
                f"Invalid execution state transition: {entry.state.value} -> {new_state.value}"
            )
        updated = ExecutionRegistryEntry(
            setup_id=entry.setup_id,
            submission_uuid=entry.submission_uuid,
            symbol=entry.symbol,
            state=new_state,
            updated_at_utc=canonical_time,
            direction=entry.direction,
            decision_price=entry.decision_price,
            cycle_time_utc=entry.cycle_time_utc,
            expires_at_utc=entry.expires_at_utc,
            broker_ticket=broker_ticket if broker_ticket is not None else entry.broker_ticket,
        )
        self._store(updated)
        if new_state in {ExecutionRegistryState.FILLED, ExecutionRegistryState.REJECTED, ExecutionRegistryState.CANCELLED, ExecutionRegistryState.EXPIRED}:
            self._release_symbol_lock(updated.symbol)
        else:
            self._set_symbol_lock(updated.symbol, canonical_time)
        return updated

    def apply_broker_response(
        self,
        submission_uuid: str,
        response: Mapping[str, Any],
        *,
        at_utc: datetime,
    ) -> tuple[ExecutionRegistryEntry, BrokerDisposition]:
        canonical_time = _ensure_utc(at_utc, field_name="at_utc")
        disposition = classify_broker_response(response)
        entry = self.entries_by_submission_uuid.get(submission_uuid)
        if entry is None:
            raise ConfigValidationError(f"Unknown execution submission UUID: {submission_uuid}")
        if disposition.retryable:
            if entry.state is ExecutionRegistryState.PENDING:
                updated = self.transition(submission_uuid, ExecutionRegistryState.SUBMITTED, at_utc=canonical_time, broker_ticket=disposition.broker_ticket)
            else:
                updated = self.transition(
                    submission_uuid,
                    entry.state,
                    at_utc=canonical_time,
                    broker_ticket=disposition.broker_ticket,
                )
            return updated, disposition
        if disposition.suggested_state is ExecutionRegistryState.AMBIGUOUS:
            updated = self.mark_ambiguous(submission_uuid, at_utc=canonical_time, broker_ticket=disposition.broker_ticket)
            return updated, disposition
        updated = self.transition(submission_uuid, disposition.suggested_state, at_utc=canonical_time, broker_ticket=disposition.broker_ticket)
        return updated, disposition

    def reconcile_against_broker_truth(
        self,
        broker_positions: Iterable[Mapping[str, Any]],
        *,
        at_utc: datetime,
    ) -> tuple[ExecutionRegistryEntry, ...]:
        canonical_time = _ensure_utc(at_utc, field_name="at_utc")
        index = _index_broker_positions(broker_positions)
        reconciled: list[ExecutionRegistryEntry] = []
        for submission_uuid, entry in list(self.entries_by_submission_uuid.items()):
            if entry.state in {ExecutionRegistryState.FILLED, ExecutionRegistryState.REJECTED, ExecutionRegistryState.CANCELLED, ExecutionRegistryState.EXPIRED}:
                reconciled.append(entry)
                continue
            broker_record = (
                index.get(entry.submission_uuid)
                or index.get(entry.setup_id)
                or index.get(entry.symbol)
            )
            if broker_record is None:
                if entry.expires_at_utc is not None and canonical_time >= entry.expires_at_utc:
                    reconciled.append(self.mark_expired(submission_uuid, at_utc=canonical_time))
                else:
                    reconciled.append(entry)
                continue
            updated = _apply_broker_record(self, entry, broker_record, canonical_time)
            reconciled.append(updated)
        return tuple(reconciled)

    def _store(self, entry: ExecutionRegistryEntry) -> None:
        self.entries_by_setup_id[entry.setup_id] = entry
        self.entries_by_submission_uuid[entry.submission_uuid] = entry

    def _set_symbol_lock(self, symbol: str, at_utc: datetime) -> None:
        self.symbol_locks_until_utc[symbol.upper()] = at_utc + timedelta(seconds=SYMBOL_LOCK_SECONDS)

    def _release_symbol_lock(self, symbol: str) -> None:
        self.symbol_locks_until_utc.pop(symbol.upper(), None)

    def _is_symbol_locked(self, symbol: str, at_utc: datetime) -> bool:
        lock_until = self.symbol_locks_until_utc.get(symbol.upper())
        return lock_until is not None and lock_until > at_utc


def build_execution_intent(
    signal: SignalDecision,
    risk: RiskDecision,
    *,
    decision_price: float,
    cycle_time_utc: datetime | None = None,
) -> ExecutionIntent:
    if signal.signal_family not in {
        SignalFamily.TREND_CONTINUATION,
        SignalFamily.BREAKOUT_MOMENTUM,
        SignalFamily.MICRO_IMPULSE,
    }:
        raise ConfigValidationError(f"Unsupported signal family for execution: {signal.signal_family.value}")
    if risk.action not in ALLOWED_RISK_ACTIONS:
        raise ConfigValidationError(f"Risk action does not permit execution: {risk.action.value}")
    if risk.sized_volume <= 0.0:
        raise ConfigValidationError("Risk decision must size positive volume")
    if risk.hard_block_reason:
        raise ConfigValidationError(f"Risk decision blocks execution: {risk.hard_block_reason}")
    if decision_price <= 0.0:
        raise ConfigValidationError("decision_price must be positive")

    cycle_utc = _ensure_utc(cycle_time_utc or _cycle_time_from_signal(signal), field_name="cycle_time_utc")
    submission_uuid = _build_submission_uuid(
        setup_id=signal.setup_id,
        symbol=signal.symbol,
        direction=signal.direction,
        decision_price=decision_price,
        cycle_time_utc=cycle_utc,
    )
    return ExecutionIntent(
        setup_id=signal.setup_id,
        signal_family=signal.signal_family,
        symbol=signal.symbol,
        direction=signal.direction,
        decision_price=decision_price,
        sized_volume=risk.sized_volume,
        submission_uuid=submission_uuid,
        cycle_time_utc=cycle_utc,
    )


def validate_execution_intent(
    snapshot: MarketSnapshot,
    signal: SignalDecision,
    risk: RiskDecision,
    governor: GovernorDecision,
    *,
    registry: ExecutionRegistryBook | None = None,
    current_time_utc: datetime | None = None,
    decision_price: float | None = None,
) -> ExecutionValidationResult:
    at_utc = _ensure_utc(current_time_utc or snapshot.cycle_time_utc, field_name="current_time_utc")
    diagnostics: dict[str, Any] = {
        "symbol": snapshot.symbol,
        "signal_setup_id": signal.setup_id,
        "signal_family": signal.signal_family.value,
        "governor_state": governor.state.value,
        "risk_action": risk.action.value,
        "spread_health": snapshot.spread_health.value,
        "latency_health": snapshot.latency_health.value,
        "feed_health": snapshot.feed_health.value,
        "payload_health": getattr(snapshot, "payload_health", HealthState.GREEN).value,
    }

    if signal.symbol.upper() != snapshot.symbol.upper():
        return _reject("symbol_mismatch", diagnostics, intent=None)
    if snapshot.spread_health is not HealthState.GREEN:
        return _reject("spread_degraded", diagnostics, intent=None)
    if snapshot.latency_health is not HealthState.GREEN:
        return _reject("latency_degraded", diagnostics, intent=None)
    if snapshot.feed_health is not HealthState.GREEN:
        return _reject("feed_degraded", diagnostics, intent=None)
    payload_health = getattr(snapshot, "payload_health", HealthState.GREEN)
    if payload_health is HealthState.RED:
        return _reject("payload_degraded", diagnostics, intent=None)
    if governor.state not in ALLOWED_ENTRY_STATES:
        return _reject("governor_state_block", diagnostics, intent=None)
    if risk.action not in ALLOWED_RISK_ACTIONS:
        return _reject("risk_not_authorized", diagnostics, intent=None)
    if risk.sized_volume <= 0.0:
        return _reject("sized_volume_zero", diagnostics, intent=None)
    if signal.expires_at_utc <= at_utc:
        return _reject("expired_signal", diagnostics, intent=None)

    resolved_price = decision_price if decision_price is not None else _mid_price(snapshot)
    if resolved_price <= 0.0:
        return _reject("invalid_decision_price", diagnostics, intent=None)

    intent = build_execution_intent(
        signal,
        risk,
        decision_price=resolved_price,
        cycle_time_utc=at_utc,
    )
    diagnostics["decision_price"] = round(resolved_price, 6)
    diagnostics["submission_uuid"] = intent.submission_uuid

    if registry is not None and registry.is_duplicate(intent=intent, at_utc=at_utc):
        return _reject("duplicate_intent", diagnostics, intent=None)

    return ExecutionValidationResult(True, intent, "", diagnostics)


def classify_broker_response(response: Mapping[str, Any]) -> BrokerDisposition:
    code = _normalized_code(response)
    ticket = _extract_broker_ticket(response)
    diagnostics = {"code": code, "ticket": ticket}

    if code in SUCCESS_CODES:
        if code == "DONE_PARTIAL":
            return BrokerDisposition(
                suggested_state=ExecutionRegistryState.PARTIAL,
                retryable=False,
                terminal=False,
                reason=code,
                broker_ticket=ticket,
                diagnostics=diagnostics,
            )
        if code in {"ACCEPTED", "ACKNOWLEDGED", "PLACED"}:
            return BrokerDisposition(
                suggested_state=ExecutionRegistryState.ACKNOWLEDGED,
                retryable=False,
                terminal=False,
                reason=code,
                broker_ticket=ticket,
                diagnostics=diagnostics,
            )
        return BrokerDisposition(
            suggested_state=ExecutionRegistryState.FILLED,
            retryable=False,
            terminal=True,
            reason=code,
            broker_ticket=ticket,
            diagnostics=diagnostics,
        )

    if code in RETRYABLE_FAILURE_CODES:
        return BrokerDisposition(
            suggested_state=ExecutionRegistryState.SUBMITTED,
            retryable=True,
            terminal=False,
            reason=code,
            broker_ticket=ticket,
            diagnostics=diagnostics,
        )

    if code in NON_RETRYABLE_FAILURE_CODES:
        return BrokerDisposition(
            suggested_state=ExecutionRegistryState.REJECTED,
            retryable=False,
            terminal=True,
            reason=code,
            broker_ticket=ticket,
            diagnostics=diagnostics,
        )

    if code:
        return BrokerDisposition(
            suggested_state=ExecutionRegistryState.AMBIGUOUS,
            retryable=False,
            terminal=False,
            reason=code,
            broker_ticket=ticket,
            diagnostics=diagnostics,
        )

    return BrokerDisposition(
        suggested_state=ExecutionRegistryState.AMBIGUOUS,
        retryable=False,
        terminal=False,
        reason="unknown",
        broker_ticket=ticket,
        diagnostics=diagnostics,
    )


def update_registry(entry: ExecutionRegistryEntry) -> ExecutionRegistryEntry:
    _validate_registry_entry(entry)
    return entry


def reconcile_registry_against_broker_truth(
    registry: ExecutionRegistryBook,
    broker_positions: Iterable[Mapping[str, Any]],
    *,
    at_utc: datetime,
) -> tuple[ExecutionRegistryEntry, ...]:
    return registry.reconcile_against_broker_truth(broker_positions, at_utc=at_utc)


def _apply_broker_record(
    registry: ExecutionRegistryBook,
    entry: ExecutionRegistryEntry,
    broker_record: Mapping[str, Any],
    at_utc: datetime,
) -> ExecutionRegistryEntry:
    broker_state = _normalized_code(broker_record)
    broker_ticket = _extract_broker_ticket(broker_record)
    if broker_state in {"FILLED", "DONE"}:
        return registry.mark_filled(entry.submission_uuid, at_utc=at_utc, broker_ticket=broker_ticket)
    if broker_state in {"DONE_PARTIAL", "PARTIAL"}:
        return registry.mark_partial(entry.submission_uuid, at_utc=at_utc, broker_ticket=broker_ticket)
    if broker_state in {"ACKNOWLEDGED", "ACCEPTED", "PLACED"}:
        return registry.mark_acknowledged(entry.submission_uuid, at_utc=at_utc, broker_ticket=broker_ticket)
    if broker_state in {"REJECTED", "INVALID", "INVALID_VOLUME", "INVALID_STOPS"}:
        return registry.mark_rejected(entry.submission_uuid, at_utc=at_utc, broker_ticket=broker_ticket)
    if broker_state in {"CANCELLED", "EXPIRED"}:
        return registry.mark_cancelled(entry.submission_uuid, at_utc=at_utc, broker_ticket=broker_ticket)
    return registry.mark_ambiguous(entry.submission_uuid, at_utc=at_utc, broker_ticket=broker_ticket)


def _index_broker_positions(broker_positions: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for record in broker_positions:
        if not isinstance(record, Mapping):
            continue
        for key in (
            record.get("submission_uuid"),
            record.get("setup_id"),
            record.get("symbol"),
            record.get("ticket"),
            record.get("position_ticket"),
            record.get("order_ticket"),
        ):
            if isinstance(key, str) and key.strip():
                index[key.strip()] = record
            elif isinstance(key, int) and not isinstance(key, bool):
                index[str(key)] = record
    return index


def _validate_registry_entry(entry: ExecutionRegistryEntry) -> None:
    if not entry.setup_id.strip():
        raise ConfigValidationError("Execution registry entry requires setup_id")
    if not entry.submission_uuid.strip():
        raise ConfigValidationError("Execution registry entry requires submission_uuid")
    if not entry.symbol.strip():
        raise ConfigValidationError("Execution registry entry requires symbol")
    if entry.updated_at_utc.tzinfo is None:
        raise ConfigValidationError("Execution registry entry updated_at_utc must be timezone-aware")
    if entry.expires_at_utc is not None and entry.expires_at_utc.tzinfo is None:
        raise ConfigValidationError("Execution registry entry expires_at_utc must be timezone-aware")


def _reject(reject_reason: str, diagnostics: dict[str, Any], *, intent: ExecutionIntent | None) -> ExecutionValidationResult:
    return ExecutionValidationResult(False, intent, reject_reason, diagnostics)


def _mid_price(snapshot: MarketSnapshot) -> float:
    return (snapshot.tick_bid + snapshot.tick_ask) / 2.0


def _cycle_time_from_signal(signal: SignalDecision) -> datetime:
    ttl_seconds = _ttl_for_signal_family(signal.signal_family)
    return signal.expires_at_utc - timedelta(seconds=ttl_seconds)


def _ttl_for_signal_family(family: SignalFamily) -> int:
    if family is SignalFamily.TREND_CONTINUATION:
        return 120
    if family is SignalFamily.BREAKOUT_MOMENTUM:
        return 90
    return 90


def _build_submission_uuid(
    *,
    setup_id: str,
    symbol: str,
    direction: Direction,
    decision_price: float,
    cycle_time_utc: datetime,
) -> str:
    payload = "|".join(
        (
            setup_id,
            symbol.upper(),
            direction.value,
            f"{decision_price:.8f}",
            _ensure_utc(cycle_time_utc, field_name="cycle_time_utc").isoformat(),
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:24]


def _normalized_code(record: Mapping[str, Any]) -> str:
    for key in ("retcode", "status", "state", "code", "result"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return ""


def _extract_broker_ticket(record: Mapping[str, Any]) -> int | None:
    for key in ("broker_ticket", "ticket", "order_ticket", "position_ticket"):
        value = record.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _ensure_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ConfigValidationError(f"{field_name} must be timezone-aware UTC datetime")
    return value.astimezone(timezone.utc)
