"""Bootstrap recovery orchestration for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..config_schema import ConfigValidationError
from ..models import ExecutionRegistryEntry
from ..persistence import RecoveryEventRecord, SQLiteRuntimeStore, SCHEMA_VERSION
from .reconcile import (
    BrokerReconciliationRuntime,
    BrokerTruthProvider,
    RecoveryReconciliationReport,
    build_reconciliation_report,
)


@dataclass(frozen=True, slots=True)
class RecoveryStep:
    stage: str
    outcome: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RecoveryBootstrapReport:
    lock_owned: bool
    schema_version: str
    config_fingerprint: str
    registry_entries: tuple[ExecutionRegistryEntry, ...]
    reconciliation_report: RecoveryReconciliationReport
    unresolved_setup_ids: tuple[str, ...]
    ready_to_resume: bool
    steps: tuple[RecoveryStep, ...] = ()


def bootstrap_recovery_runtime(
    store: SQLiteRuntimeStore,
    *,
    schema_version: str,
    config_fingerprint: str,
    broker_positions: Sequence[Mapping[str, Any]] = (),
    broker_truth_provider: BrokerTruthProvider | None = None,
    current_time_utc: datetime | None = None,
    lock_owned: bool = True,
    allow_flatten_unresolved: bool = True,
) -> RecoveryBootstrapReport:
    if not lock_owned:
        raise ConfigValidationError("Runtime lock ownership is required before bootstrap")

    at_utc = _ensure_utc(current_time_utc or datetime.now(timezone.utc))
    steps: list[RecoveryStep] = [RecoveryStep("lock", "ok" if lock_owned else "fail", "runtime lock validated")]
    store.assert_compatible(schema_version=schema_version, config_fingerprint=config_fingerprint)
    steps.append(RecoveryStep("schema", "ok", f"schema={schema_version}"))
    steps.append(RecoveryStep("fingerprint", "ok", config_fingerprint))

    registry_entries = store.load_execution_registry()
    steps.append(RecoveryStep("registry_restore", "ok", f"entries={len(registry_entries)}"))

    if broker_truth_provider is None:
        reconciliation_report = build_reconciliation_report(
            store,
            broker_positions,
            at_utc=at_utc,
        )
    else:
        runtime = BrokerReconciliationRuntime(store)
        reconciliation = runtime.reconcile(
            broker_truth_provider,
            at_utc=at_utc,
            allow_flatten_unresolved=allow_flatten_unresolved,
        )
        reconciliation_report = reconciliation.registry_report
    steps.append(
        RecoveryStep(
            "reconcile",
            "ok",
            f"reconciled={reconciliation_report.reconciled_count}",
        )
    )

    unresolved_setup_ids = reconciliation_report.unresolved_setup_ids
    if unresolved_setup_ids:
        outcome = "flatten" if allow_flatten_unresolved else "hold"
        steps.append(
            RecoveryStep(
                "ambiguous_exposure",
                outcome,
                ",".join(unresolved_setup_ids),
            )
        )
    else:
        steps.append(RecoveryStep("ambiguous_exposure", "clear", "none"))

    store.store_recovery_event(
        RecoveryEventRecord(
            event_time_utc=at_utc,
            stage="bootstrap",
            outcome="ready" if not unresolved_setup_ids or allow_flatten_unresolved else "blocked",
            payload_json=_json_dumps(
                {
                    "schema_version": schema_version,
                    "config_fingerprint": config_fingerprint,
                    "registry_count": len(registry_entries),
                    "reconciled_count": reconciliation_report.reconciled_count,
                    "unresolved_setup_ids": list(unresolved_setup_ids),
                }
            ),
        )
    )

    ready_to_resume = not unresolved_setup_ids or allow_flatten_unresolved
    return RecoveryBootstrapReport(
        lock_owned=lock_owned,
        schema_version=schema_version,
        config_fingerprint=config_fingerprint,
        registry_entries=registry_entries,
        reconciliation_report=reconciliation_report,
        unresolved_setup_ids=unresolved_setup_ids,
        ready_to_resume=ready_to_resume,
        steps=tuple(steps),
    )


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ConfigValidationError("Recovery bootstrap timestamp must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def _json_dumps(payload: Mapping[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
