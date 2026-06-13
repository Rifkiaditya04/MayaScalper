"""Restart idempotency helpers for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping

from ..config_schema import ConfigValidationError
from ..execution import ExecutionRegistryBook
from ..enums import ExecutionRegistryState
from ..models import ExecutionIntent, ExecutionRegistryEntry
from ..persistence import SQLiteRuntimeStore


@dataclass(frozen=True, slots=True)
class IdempotencyCheckResult:
    duplicate: bool
    reason: str
    submission_uuid: str
    setup_id: str
    symbol: str


def build_submission_identity(
    *,
    setup_id: str,
    symbol: str,
    direction: Any,
    decision_price: float,
    cycle_time_utc: datetime,
) -> str:
    direction_value = direction.value if hasattr(direction, "value") else str(direction)
    payload = "|".join(
        (
            setup_id,
            symbol.upper(),
            direction_value.upper(),
            f"{decision_price:.8f}",
            _ensure_utc(cycle_time_utc).isoformat(),
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:24]


def build_registry_book(store: SQLiteRuntimeStore) -> ExecutionRegistryBook:
    book = ExecutionRegistryBook()
    for entry in store.load_execution_registry():
        book.entries_by_setup_id[entry.setup_id] = entry
        book.entries_by_submission_uuid[entry.submission_uuid] = entry
        if entry.expires_at_utc is not None and entry.state not in {
            ExecutionRegistryState.EXPIRED,
            ExecutionRegistryState.CANCELLED,
        }:
            book.symbol_locks_until_utc[entry.symbol.upper()] = entry.expires_at_utc
    return book


def check_duplicate_submission(
    store: SQLiteRuntimeStore,
    intent: ExecutionIntent,
    *,
    at_utc: datetime,
) -> IdempotencyCheckResult:
    canonical_time = _ensure_utc(at_utc)
    book = build_registry_book(store)
    duplicate = book.is_duplicate(intent=intent, at_utc=canonical_time)
    return IdempotencyCheckResult(
        duplicate=duplicate,
        reason="duplicate" if duplicate else "clear",
        submission_uuid=intent.submission_uuid,
        setup_id=intent.setup_id,
        symbol=intent.symbol,
    )


def register_submission(
    store: SQLiteRuntimeStore,
    entry: ExecutionRegistryEntry,
) -> ExecutionRegistryEntry:
    _validate_entry(entry)
    store.store_execution_registry((entry,))
    return entry


def _validate_entry(entry: ExecutionRegistryEntry) -> None:
    if not entry.setup_id.strip():
        raise ConfigValidationError("execution registry entry requires setup_id")
    if not entry.submission_uuid.strip():
        raise ConfigValidationError("execution registry entry requires submission_uuid")
    if not entry.symbol.strip():
        raise ConfigValidationError("execution registry entry requires symbol")


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ConfigValidationError("datetime must be timezone-aware UTC")
    return value.astimezone(timezone.utc)
