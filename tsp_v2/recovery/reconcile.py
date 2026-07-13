"""Broker reconciliation helpers for TSP V2."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Protocol, Sequence

from ..config_schema import ConfigValidationError
from ..enums import ExecutionRegistryState, HealthState
from ..execution import ExecutionRegistryBook, reconcile_registry_against_broker_truth
from ..models import ExecutionRegistryEntry
from ..persistence import AccountStateRecord, RecoveryEventRecord, SQLiteRuntimeStore


class BrokerTruthProvider(Protocol):
    def query_account(self) -> Mapping[str, Any]: ...

    def query_positions(self) -> Sequence[Mapping[str, Any]]: ...

    def query_orders(self) -> Sequence[Mapping[str, Any]]: ...

    def query_deals(self) -> Sequence[Mapping[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class RecoveryReconciliationReport:
    reconciled_entries: tuple[ExecutionRegistryEntry, ...]
    reconciled_count: int
    filled_count: int
    rejected_count: int
    cancelled_count: int
    partial_count: int
    ambiguous_count: int
    expired_count: int
    unresolved_setup_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BrokerReconciliationFinding:
    scope: str
    status: str
    identifier: str
    symbol: str
    setup_id: str = ""
    submission_uuid: str = ""
    broker_ticket: int | None = None
    local_state: str = ""
    broker_state: str = ""
    detail: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BrokerReconciliationReport:
    broker_account: dict[str, Any]
    registry_report: RecoveryReconciliationReport
    findings: tuple[BrokerReconciliationFinding, ...]
    matched_count: int
    missing_local_count: int
    missing_broker_count: int
    orphan_position_count: int
    state_divergence_count: int
    account_divergence_count: int
    deal_divergence_count: int
    order_divergence_count: int
    ready_to_resume: bool
    unresolved_setup_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BrokerRecord:
    kind: str
    payload: dict[str, Any]


@dataclass(slots=True)
class BrokerReconciliationRuntime:
    store: SQLiteRuntimeStore

    def reconcile(
        self,
        broker_truth: BrokerTruthProvider,
        *,
        at_utc: datetime,
        allow_flatten_unresolved: bool = True,
        persist_account_state: bool = True,
    ) -> BrokerReconciliationReport:
        current_time = _ensure_utc(at_utc)
        store = self.store
        store.store_telemetry_index(
            "reconciliation_started",
            {
                "stage": "broker_reconciliation",
                "event_time_utc": current_time.isoformat(),
            },
        )

        local_registry = store.load_execution_registry()
        local_positions = store.load_positions()
        local_account = store.load_account_state()
        broker_account = _normalize_account_record(_query_provider(broker_truth, "query_account", default={}))
        broker_positions = _load_broker_records(broker_truth, "query_positions", kind="position")
        broker_orders = _load_broker_records(broker_truth, "query_orders", kind="order")
        broker_deals = _load_broker_records(broker_truth, "query_deals", kind="deal")
        combined_broker_records = broker_positions + broker_orders + broker_deals

        registry = ExecutionRegistryBook()
        for entry in local_registry:
            registry.entries_by_setup_id[entry.setup_id] = entry
            registry.entries_by_submission_uuid[entry.submission_uuid] = entry
            if entry.expires_at_utc is not None and entry.state not in {
                ExecutionRegistryState.EXPIRED,
                ExecutionRegistryState.CANCELLED,
            }:
                registry.symbol_locks_until_utc[entry.symbol.upper()] = entry.expires_at_utc

        reconciled_entries = reconcile_registry_against_broker_truth(
            registry,
            [record.payload for record in combined_broker_records],
            at_utc=current_time,
        )
        store.store_execution_registry(reconciled_entries)
        registry_report = _summarize_registry(reconciled_entries)

        findings: list[BrokerReconciliationFinding] = []
        matched_count = 0
        missing_local_count = 0
        missing_broker_count = 0
        orphan_position_count = 0
        state_divergence_count = 0
        account_divergence_count = 0
        deal_divergence_count = 0
        order_divergence_count = 0

        broker_registry_index = _index_broker_records(combined_broker_records)
        broker_position_index = _index_broker_records(broker_positions)
        matched_broker_positions: set[int] = set()

        for entry in local_registry:
            if _skip_missing_broker_check(entry):
                continue
            broker_match = _find_broker_record(entry, broker_registry_index)
            if broker_match is None:
                missing_broker_count += 1
                findings.append(
                    BrokerReconciliationFinding(
                        scope="registry",
                        status="MISSING_BROKER",
                        identifier=entry.submission_uuid,
                        symbol=entry.symbol,
                        setup_id=entry.setup_id,
                        submission_uuid=entry.submission_uuid,
                        local_state=entry.state.value,
                        detail="Local registry entry not found in broker truth",
                    )
                )
                continue

            broker_state = _normalized_state(broker_match.payload)
            if _states_diverge(entry.state.value, broker_state):
                state_divergence_count += 1
                if broker_match.kind == "deal":
                    deal_divergence_count += 1
                elif broker_match.kind == "order":
                    order_divergence_count += 1
                findings.append(
                    BrokerReconciliationFinding(
                        scope="registry",
                        status="STATE_DIVERGENCE",
                        identifier=entry.submission_uuid,
                        symbol=entry.symbol,
                        setup_id=entry.setup_id,
                        submission_uuid=entry.submission_uuid,
                        broker_ticket=_extract_ticket(broker_match.payload),
                        local_state=entry.state.value,
                        broker_state=broker_state,
                        detail="Registry state differs from broker truth",
                        payload={"broker_kind": broker_match.kind},
                    )
                )
            else:
                matched_count += 1
                findings.append(
                    BrokerReconciliationFinding(
                        scope="registry",
                        status="MATCHED",
                        identifier=entry.submission_uuid,
                        symbol=entry.symbol,
                        setup_id=entry.setup_id,
                        submission_uuid=entry.submission_uuid,
                        broker_ticket=_extract_ticket(broker_match.payload),
                        local_state=entry.state.value,
                        broker_state=broker_state,
                        detail="Registry entry matches broker truth",
                        payload={"broker_kind": broker_match.kind},
                    )
                )

        for row in local_positions:
            setup_id = str(row["setup_id"])
            broker_match = _find_position_record(row, broker_position_index)
            if broker_match is None:
                missing_broker_count += 1
                findings.append(
                    BrokerReconciliationFinding(
                        scope="position",
                        status="MISSING_BROKER",
                        identifier=setup_id,
                        symbol=str(row["symbol"]),
                        setup_id=setup_id,
                        local_state=f"{row['direction']}",
                        detail="Local position missing from broker truth",
                    )
                )
                continue

            matched_broker_positions.add(id(broker_match))
            broker_state = _normalized_state(broker_match.payload)
            local_direction = str(row["direction"])
            broker_direction = _normalized_direction(broker_match.payload)
            if broker_direction and broker_direction != local_direction.upper():
                state_divergence_count += 1
                if broker_match.kind == "deal":
                    deal_divergence_count += 1
                elif broker_match.kind == "order":
                    order_divergence_count += 1
                findings.append(
                    BrokerReconciliationFinding(
                        scope="position",
                        status="STATE_DIVERGENCE",
                        identifier=setup_id,
                        symbol=str(row["symbol"]),
                        setup_id=setup_id,
                        broker_ticket=_extract_ticket(broker_match.payload),
                        local_state=local_direction,
                        broker_state=broker_direction,
                        detail="Local position direction differs from broker truth",
                        payload={"broker_kind": broker_match.kind},
                    )
                )
            else:
                matched_count += 1
                findings.append(
                    BrokerReconciliationFinding(
                        scope="position",
                        status="MATCHED",
                        identifier=setup_id,
                        symbol=str(row["symbol"]),
                        setup_id=setup_id,
                        broker_ticket=_extract_ticket(broker_match.payload),
                        local_state=local_direction,
                        broker_state=broker_state,
                        detail="Local position matches broker truth",
                        payload={"broker_kind": broker_match.kind},
                    )
                )

        for broker_record in broker_positions:
            if id(broker_record) in matched_broker_positions:
                continue
            missing_local_count += 1
            orphan_position_count += 1
            findings.append(
                BrokerReconciliationFinding(
                    scope="position",
                    status="ORPHAN_POSITION",
                    identifier=_broker_identifier(broker_record.payload),
                    symbol=str(broker_record.payload.get("symbol", "")),
                    setup_id=str(broker_record.payload.get("setup_id", "")),
                    submission_uuid=str(broker_record.payload.get("submission_uuid", "")),
                    broker_ticket=_extract_ticket(broker_record.payload),
                    broker_state=_normalized_state(broker_record.payload),
                    detail="Broker position is not represented locally",
                    payload={"broker_kind": broker_record.kind},
                )
            )

        account_status = "MATCHED"
        if local_account is None and broker_account:
            missing_local_count += 1
            account_status = "MISSING_LOCAL"
            findings.append(
                BrokerReconciliationFinding(
                    scope="account",
                    status="MISSING_LOCAL",
                    identifier="account",
                    symbol="ACCOUNT",
                    detail="Broker account snapshot exists but local account state is missing",
                    payload={"broker_account": broker_account},
                )
            )
        elif local_account is not None and not broker_account:
            missing_broker_count += 1
            account_status = "MISSING_BROKER"
            findings.append(
                BrokerReconciliationFinding(
                    scope="account",
                    status="MISSING_BROKER",
                    identifier="account",
                    symbol="ACCOUNT",
                    detail="Local account state exists but broker account snapshot is unavailable",
                    payload={"local_account": _json_ready(asdict(local_account))},
                )
            )
        elif local_account is not None and broker_account:
            if _account_diverges(local_account, broker_account):
                state_divergence_count += 1
                account_divergence_count += 1
                account_status = "STATE_DIVERGENCE"
                findings.append(
                    BrokerReconciliationFinding(
                        scope="account",
                        status="STATE_DIVERGENCE",
                        identifier="account",
                        symbol="ACCOUNT",
                        local_state=_json_ready(asdict(local_account)),
                        broker_state="ACCOUNT_DIVERGENCE",
                        detail="Local account state differs from broker truth",
                        payload={
                            "local": _json_ready(asdict(local_account)),
                            "broker": broker_account,
                        },
                    )
                )
            else:
                matched_count += 1
                findings.append(
                    BrokerReconciliationFinding(
                        scope="account",
                        status="MATCHED",
                        identifier="account",
                        symbol="ACCOUNT",
                        detail="Local account state matches broker truth",
                        payload={"broker_account": broker_account},
                    )
                )

        if broker_account and persist_account_state:
            store.store_account_state(
                AccountStateRecord(
                    equity=float(broker_account.get("equity", 0.0)),
                    balance=float(broker_account.get("balance", broker_account.get("equity", 0.0))),
                    drawdown_pct=float(broker_account.get("drawdown_pct", 0.0)),
                    daily_loss_pct=float(broker_account.get("daily_loss_pct", 0.0)),
                    unrealized_r=float(broker_account.get("unrealized_r", 0.0)),
                    updated_at_utc=current_time,
                    payload_json=_json_dumps(broker_account),
                )
            )

        unresolved_setup_ids = registry_report.unresolved_setup_ids
        critical_divergence = bool(state_divergence_count or orphan_position_count or missing_broker_count)
        ready_to_resume = (not unresolved_setup_ids or allow_flatten_unresolved) and not critical_divergence

        health = HealthState.GREEN if ready_to_resume else HealthState.YELLOW if not critical_divergence else HealthState.RED
        store.store_health_state(
            "broker_reconciliation",
            health,
            {
                "ready_to_resume": ready_to_resume,
                "account_status": account_status,
                "matched_count": matched_count,
                "missing_local_count": missing_local_count,
                "missing_broker_count": missing_broker_count,
                "orphan_position_count": orphan_position_count,
                "state_divergence_count": state_divergence_count,
                "account_divergence_count": account_divergence_count,
                "deal_divergence_count": deal_divergence_count,
                "order_divergence_count": order_divergence_count,
            },
        )

        store.store_recovery_event(
            RecoveryEventRecord(
                event_time_utc=current_time,
                stage="broker_reconciliation",
                outcome="ready" if ready_to_resume else "conflict",
                payload_json=_json_dumps(
                    {
                        "matched_count": matched_count,
                        "missing_local_count": missing_local_count,
                        "missing_broker_count": missing_broker_count,
                        "orphan_position_count": orphan_position_count,
                        "state_divergence_count": state_divergence_count,
                        "account_divergence_count": account_divergence_count,
                        "deal_divergence_count": deal_divergence_count,
                        "order_divergence_count": order_divergence_count,
                        "ready_to_resume": ready_to_resume,
                    }
                ),
            )
        )
        if state_divergence_count or orphan_position_count or missing_broker_count:
            store.store_telemetry_index(
                "reconciliation_conflict",
                {
                    "event_time_utc": current_time.isoformat(),
                    "state_divergence_count": state_divergence_count,
                    "orphan_position_count": orphan_position_count,
                    "missing_broker_count": missing_broker_count,
                },
            )
        if orphan_position_count:
            store.store_telemetry_index(
                "orphan_detected",
                {
                    "event_time_utc": current_time.isoformat(),
                    "orphan_position_count": orphan_position_count,
                },
            )
        store.store_telemetry_index(
            "reconciliation_completed",
            {
                "event_time_utc": current_time.isoformat(),
                "ready_to_resume": ready_to_resume,
                "matched_count": matched_count,
                "missing_local_count": missing_local_count,
                "missing_broker_count": missing_broker_count,
                "orphan_position_count": orphan_position_count,
                "state_divergence_count": state_divergence_count,
            },
        )

        return BrokerReconciliationReport(
            broker_account=broker_account,
            registry_report=registry_report,
            findings=tuple(findings),
            matched_count=matched_count,
            missing_local_count=missing_local_count,
            missing_broker_count=missing_broker_count,
            orphan_position_count=orphan_position_count,
            state_divergence_count=state_divergence_count,
            account_divergence_count=account_divergence_count,
            deal_divergence_count=deal_divergence_count,
            order_divergence_count=order_divergence_count,
            ready_to_resume=ready_to_resume,
            unresolved_setup_ids=unresolved_setup_ids,
        )


def build_reconciliation_report(
    store: SQLiteRuntimeStore,
    broker_positions: Iterable[Mapping[str, Any]],
    *,
    at_utc: datetime,
) -> RecoveryReconciliationReport:
    registry = ExecutionRegistryBook()
    for entry in store.load_execution_registry():
        registry.entries_by_setup_id[entry.setup_id] = entry
        registry.entries_by_submission_uuid[entry.submission_uuid] = entry
    reconciled = registry.reconcile_against_broker_truth(broker_positions, at_utc=_ensure_utc(at_utc))
    store.store_execution_registry(reconciled)
    report = _summarize_registry(reconciled)
    store.store_recovery_event(
        RecoveryEventRecord(
            event_time_utc=_ensure_utc(at_utc),
            stage="reconcile",
            outcome="ok",
            payload_json=_json_dumps(
                {
                    "reconciled_count": report.reconciled_count,
                    "filled_count": report.filled_count,
                    "rejected_count": report.rejected_count,
                    "cancelled_count": report.cancelled_count,
                    "partial_count": report.partial_count,
                    "ambiguous_count": report.ambiguous_count,
                    "expired_count": report.expired_count,
                    "unresolved_setup_ids": list(report.unresolved_setup_ids),
                }
            ),
        )
    )
    return report


def reconcile_broker_truth(
    store: SQLiteRuntimeStore,
    broker_positions: Iterable[Mapping[str, Any]],
    *,
    at_utc: datetime,
) -> tuple[ExecutionRegistryEntry, ...]:
    report = build_reconciliation_report(store, broker_positions, at_utc=at_utc)
    return report.reconciled_entries


def _query_provider(provider: BrokerTruthProvider, method_name: str, *, default: Any) -> Any:
    method = getattr(provider, method_name, None)
    if callable(method):
        try:
            return method()
        except TypeError:
            return default
    return default


def _load_broker_records(
    provider: BrokerTruthProvider,
    method_name: str,
    *,
    kind: str,
) -> tuple[BrokerRecord, ...]:
    raw = _query_provider(provider, method_name, default=())
    records: list[BrokerRecord] = []
    for record in raw or ():
        if isinstance(record, Mapping):
            records.append(BrokerRecord(kind=kind, payload=dict(record)))
    return tuple(records)


def _find_broker_record(entry: ExecutionRegistryEntry, index: Mapping[str, BrokerRecord]) -> BrokerRecord | None:
    for key in (
        entry.submission_uuid,
        entry.setup_id,
        entry.symbol,
        str(entry.broker_ticket) if entry.broker_ticket is not None else "",
    ):
        if key and key in index:
            return index[key]
    return None


def _skip_missing_broker_check(entry: ExecutionRegistryEntry) -> bool:
    return entry.state in {ExecutionRegistryState.EXPIRED, ExecutionRegistryState.CANCELLED} and entry.broker_ticket is None


def _find_position_record(row: Mapping[str, Any], index: Mapping[str, BrokerRecord]) -> BrokerRecord | None:
    for key in (str(row["setup_id"]),):
        if key in index:
            return index[key]
    return None


def _index_broker_records(records: Sequence[BrokerRecord]) -> dict[str, BrokerRecord]:
    index: dict[str, BrokerRecord] = {}
    for record in records:
        payload = record.payload
        keys = (
            payload.get("submission_uuid"),
            payload.get("setup_id"),
            payload.get("symbol"),
            payload.get("ticket"),
            payload.get("position_ticket"),
            payload.get("order_ticket"),
        )
        for key in keys:
            if isinstance(key, str) and key.strip():
                index[key.strip()] = record
            elif isinstance(key, int) and not isinstance(key, bool):
                index[str(key)] = record
    return index


def _summarize_registry(entries: tuple[ExecutionRegistryEntry, ...]) -> RecoveryReconciliationReport:
    filled = sum(1 for entry in entries if entry.state is ExecutionRegistryState.FILLED)
    rejected = sum(1 for entry in entries if entry.state is ExecutionRegistryState.REJECTED)
    cancelled = sum(1 for entry in entries if entry.state is ExecutionRegistryState.CANCELLED)
    partial = sum(1 for entry in entries if entry.state is ExecutionRegistryState.PARTIAL)
    ambiguous = sum(1 for entry in entries if entry.state is ExecutionRegistryState.AMBIGUOUS)
    expired = sum(1 for entry in entries if entry.state is ExecutionRegistryState.EXPIRED)
    unresolved = tuple(
        entry.setup_id
        for entry in entries
        if entry.state in {
            ExecutionRegistryState.PENDING,
            ExecutionRegistryState.SUBMITTED,
            ExecutionRegistryState.ACKNOWLEDGED,
            ExecutionRegistryState.PARTIAL,
            ExecutionRegistryState.AMBIGUOUS,
        }
    )
    return RecoveryReconciliationReport(
        reconciled_entries=entries,
        reconciled_count=len(entries),
        filled_count=filled,
        rejected_count=rejected,
        cancelled_count=cancelled,
        partial_count=partial,
        ambiguous_count=ambiguous,
        expired_count=expired,
        unresolved_setup_ids=unresolved,
    )


def _account_diverges(local_account: AccountStateRecord, broker_account: Mapping[str, Any]) -> bool:
    comparisons = (
        ("equity", local_account.equity),
        ("balance", local_account.balance),
        ("drawdown_pct", local_account.drawdown_pct),
        ("daily_loss_pct", local_account.daily_loss_pct),
        ("unrealized_r", local_account.unrealized_r),
    )
    for key, local_value in comparisons:
        broker_value = broker_account.get(key)
        if broker_value is None:
            continue
        try:
            if abs(float(broker_value) - float(local_value)) > 1e-9:
                return True
        except (TypeError, ValueError):
            return True
    return False


def _normalize_account_record(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    if hasattr(raw, "_asdict") and callable(raw._asdict):
        return dict(raw._asdict())
    if hasattr(raw, "__dict__"):
        return dict(vars(raw))
    return {}


def _states_diverge(local_state: str, broker_state: str) -> bool:
    normalized_local = local_state.strip().upper()
    normalized_broker = broker_state.strip().upper()
    if not normalized_broker:
        return False
    if normalized_broker in {"DONE", "FILLED", "DONE_PARTIAL", "PARTIAL", "ACKNOWLEDGED", "ACCEPTED", "PLACED"}:
        return False
    if normalized_broker in {"REJECTED", "INVALID", "INVALID_VOLUME", "INVALID_STOPS", "CANCELLED", "EXPIRED"}:
        return normalized_local not in {"REJECTED", "CANCELLED", "EXPIRED"}
    return normalized_local not in {normalized_broker}


def _normalized_state(record: Mapping[str, Any]) -> str:
    for key in ("state", "status", "retcode", "code", "result"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return ""


def _normalized_direction(record: Mapping[str, Any]) -> str:
    value = record.get("direction") or record.get("type")
    if isinstance(value, str) and value.strip():
        raw = value.strip().upper()
        if raw in {"BUY", "LONG", "0"}:
            return "LONG"
        if raw in {"SELL", "SHORT", "1"}:
            return "SHORT"
        return raw
    if isinstance(value, int) and not isinstance(value, bool):
        return "LONG" if value == 0 else "SHORT" if value == 1 else str(value)
    return ""


def _extract_ticket(record: Mapping[str, Any]) -> int | None:
    for key in ("broker_ticket", "ticket", "order_ticket", "position_ticket", "deal"):
        value = record.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _broker_identifier(record: Mapping[str, Any]) -> str:
    for key in ("submission_uuid", "setup_id", "ticket", "position_ticket", "order_ticket", "deal"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    symbol = record.get("symbol")
    if isinstance(symbol, str) and symbol.strip():
        return symbol.strip()
    return "broker_record"


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ConfigValidationError("datetime must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def _json_dumps(payload: Mapping[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _json_ready(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value
