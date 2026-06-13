"""Single-owner orchestration scaffold for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Any

from .config import AppConfig
from .config_schema import ConfigValidationError
from .persistence import SQLiteRuntimeStore, SCHEMA_VERSION
from .recovery import BrokerTruthProvider, RecoveryBootstrapReport, bootstrap_recovery_runtime


@dataclass(slots=True)
class TSPV2Orchestrator:
    config: AppConfig
    store: SQLiteRuntimeStore | None = None
    bootstrap_report: RecoveryBootstrapReport | None = None

    def bootstrap(
        self,
        *,
        broker_positions: tuple[Mapping[str, Any], ...] = (),
        broker_truth_provider: BrokerTruthProvider | None = None,
        current_time_utc: datetime | None = None,
        lock_owned: bool = True,
    ) -> RecoveryBootstrapReport:
        store = self.store or SQLiteRuntimeStore(self.config.persistence.sqlite_path, wal_enabled=self.config.persistence.wal_enabled)
        store.initialize()
        current_fingerprint = store.get_config_fingerprint()
        if current_fingerprint is not None and current_fingerprint != self.config.fingerprint:
            raise ConfigValidationError(
                f"Config fingerprint mismatch: expected {self.config.fingerprint}, found {current_fingerprint}"
            )
        current_schema = store.get_schema_version()
        if current_schema is not None and current_schema != SCHEMA_VERSION:
            raise ConfigValidationError(
                f"Schema version mismatch: expected {SCHEMA_VERSION}, found {current_schema}"
            )
        store.set_config_fingerprint(self.config.fingerprint)
        report = bootstrap_recovery_runtime(
            store,
            schema_version=SCHEMA_VERSION,
            config_fingerprint=self.config.fingerprint,
            broker_positions=broker_positions,
            broker_truth_provider=broker_truth_provider,
            current_time_utc=current_time_utc or datetime.now(tz=timezone.utc),
            lock_owned=lock_owned,
        )
        self.store = store
        self.bootstrap_report = report
        return report

    def process_cycle(self) -> None:
        raise NotImplementedError("PATCH-000 scaffold only: cycle processing not implemented yet.")
