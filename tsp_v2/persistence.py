"""SQLite persistence runtime for TSP V2."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .config_schema import ConfigValidationError
from .enums import ExecutionRegistryState, GovernorState, HealthState, RuntimeMode
from .models import ExecutionRegistryEntry, GovernorDecision, PositionSnapshot


SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class PersistenceMetaRecord:
    meta_key: str
    meta_value: str
    updated_at_utc: datetime


@dataclass(frozen=True, slots=True)
class RuntimeStateRecord:
    state_key: str
    state_value: str
    updated_at_utc: datetime


@dataclass(frozen=True, slots=True)
class GovernorStateRecord:
    state: GovernorState
    state_reason: str
    pace_classification: str
    aggression_multiplier: float
    profile_constraints_json: str
    escalation_flags_json: str
    updated_at_utc: datetime


@dataclass(frozen=True, slots=True)
class AccountStateRecord:
    equity: float
    balance: float
    drawdown_pct: float
    daily_loss_pct: float
    unrealized_r: float
    updated_at_utc: datetime
    payload_json: str = "{}"


@dataclass(frozen=True, slots=True)
class RecoveryEventRecord:
    event_time_utc: datetime
    stage: str
    outcome: str
    payload_json: str = "{}"


class SQLiteRuntimeStore:
    def __init__(
        self,
        db_path: Path,
        *,
        schema_path: Path | None = None,
        wal_enabled: bool = True,
    ) -> None:
        self.db_path = db_path
        self.schema_path = schema_path or Path(__file__).resolve().parent / "schemas" / "sql" / "001_runtime_schema.sql"
        self.wal_enabled = wal_enabled
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        self._ensure_connection()
        assert self._connection is not None
        return self._connection

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.schema_path.parent:
            self.schema_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            isolation_level=None,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA journal_mode = {'WAL' if self.wal_enabled else 'DELETE'}")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.executescript(self.schema_path.read_text(encoding="utf-8"))
        self._connection = conn
        if self.get_schema_version() is None:
            self.set_schema_version(SCHEMA_VERSION)
        self._set_meta("db_path", str(self.db_path))
        self._set_meta("schema_path", str(self.schema_path))
        self._set_meta("wal_enabled", "1" if self.wal_enabled else "0")

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def set_schema_version(self, schema_version: str) -> None:
        self._set_meta("schema_version", schema_version)

    def get_schema_version(self) -> str | None:
        return self._get_meta("schema_version")

    def set_config_fingerprint(self, fingerprint: str) -> None:
        self._set_meta("config_fingerprint", fingerprint)
        with self._write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO config_fingerprint (id, fingerprint, created_at_utc)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    fingerprint=excluded.fingerprint,
                    created_at_utc=excluded.created_at_utc
                """,
                (fingerprint, _now_utc().isoformat()),
            )

    def get_config_fingerprint(self) -> str | None:
        with self._read_connection() as conn:
            row = conn.execute("SELECT fingerprint FROM config_fingerprint WHERE id = 1").fetchone()
        return None if row is None else str(row["fingerprint"])

    def assert_compatible(self, *, schema_version: str, config_fingerprint: str) -> None:
        current_schema = self.get_schema_version()
        current_fingerprint = self.get_config_fingerprint()
        if current_schema != schema_version:
            raise ConfigValidationError(
                f"Schema version mismatch: expected {schema_version}, found {current_schema}"
            )
        if current_fingerprint != config_fingerprint:
            raise ConfigValidationError(
                f"Config fingerprint mismatch: expected {config_fingerprint}, found {current_fingerprint}"
            )

    def load_runtime_state(self) -> dict[str, str]:
        with self._read_connection() as conn:
            rows = conn.execute("SELECT state_key, state_value FROM runtime_state").fetchall()
        return {str(row["state_key"]): str(row["state_value"]) for row in rows}

    def store_runtime_state(self, state: Mapping[str, Any]) -> None:
        now = _now_utc().isoformat()
        with self._write_transaction() as conn:
            for key, value in state.items():
                conn.execute(
                    """
                    INSERT INTO runtime_state (state_key, state_value, updated_at_utc)
                    VALUES (?, ?, ?)
                    ON CONFLICT(state_key) DO UPDATE SET
                        state_value=excluded.state_value,
                        updated_at_utc=excluded.updated_at_utc
                    """,
                    (str(key), _json_dumps(value), now),
                )

    def store_governor_state(self, decision: GovernorDecision, *, updated_at_utc: datetime | None = None) -> None:
        timestamp = _ensure_utc(updated_at_utc or _now_utc(), field_name="updated_at_utc")
        with self._write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO governor_state (
                    id, state, state_reason, pace_classification,
                    aggression_multiplier, profile_constraints_json,
                    escalation_flags_json, updated_at_utc
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    state=excluded.state,
                    state_reason=excluded.state_reason,
                    pace_classification=excluded.pace_classification,
                    aggression_multiplier=excluded.aggression_multiplier,
                    profile_constraints_json=excluded.profile_constraints_json,
                    escalation_flags_json=excluded.escalation_flags_json,
                    updated_at_utc=excluded.updated_at_utc
                """,
                (
                    decision.state.value,
                    decision.state_reason,
                    decision.pace_classification.value,
                    decision.aggression_multiplier,
                    _json_dumps(decision.profile_constraints),
                    _json_dumps(list(decision.escalation_flags)),
                    timestamp.isoformat(),
                ),
            )

    def load_governor_state(self) -> GovernorStateRecord | None:
        with self._read_connection() as conn:
            row = conn.execute("SELECT * FROM governor_state WHERE id = 1").fetchone()
        if row is None:
            return None
        return GovernorStateRecord(
            state=GovernorState(str(row["state"])),
            state_reason=str(row["state_reason"]),
            pace_classification=str(row["pace_classification"]),
            aggression_multiplier=float(row["aggression_multiplier"]),
            profile_constraints_json=str(row["profile_constraints_json"]),
            escalation_flags_json=str(row["escalation_flags_json"]),
            updated_at_utc=_parse_utc(str(row["updated_at_utc"])),
        )

    def store_account_state(self, record: AccountStateRecord) -> None:
        with self._write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO account_state (
                    id, equity, balance, drawdown_pct, daily_loss_pct,
                    unrealized_r, updated_at_utc, payload_json
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    equity=excluded.equity,
                    balance=excluded.balance,
                    drawdown_pct=excluded.drawdown_pct,
                    daily_loss_pct=excluded.daily_loss_pct,
                    unrealized_r=excluded.unrealized_r,
                    updated_at_utc=excluded.updated_at_utc,
                    payload_json=excluded.payload_json
                """,
                (
                    record.equity,
                    record.balance,
                    record.drawdown_pct,
                    record.daily_loss_pct,
                    record.unrealized_r,
                    _ensure_utc(record.updated_at_utc, field_name="updated_at_utc").isoformat(),
                    record.payload_json,
                ),
            )

    def load_account_state(self) -> AccountStateRecord | None:
        with self._read_connection() as conn:
            row = conn.execute("SELECT * FROM account_state WHERE id = 1").fetchone()
        if row is None:
            return None
        return AccountStateRecord(
            equity=float(row["equity"]),
            balance=float(row["balance"]),
            drawdown_pct=float(row["drawdown_pct"]),
            daily_loss_pct=float(row["daily_loss_pct"]),
            unrealized_r=float(row["unrealized_r"]),
            updated_at_utc=_parse_utc(str(row["updated_at_utc"])),
            payload_json=str(row["payload_json"]),
        )

    def store_execution_registry(self, entries: Iterable[ExecutionRegistryEntry]) -> None:
        with self._write_transaction() as conn:
            for entry in entries:
                conn.execute(
                    """
                    INSERT INTO execution_registry (
                        setup_id, submission_uuid, symbol, state,
                        updated_at_utc, direction, decision_price,
                        cycle_time_utc, expires_at_utc, broker_ticket
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(setup_id) DO UPDATE SET
                        submission_uuid=excluded.submission_uuid,
                        symbol=excluded.symbol,
                        state=excluded.state,
                        updated_at_utc=excluded.updated_at_utc,
                        direction=excluded.direction,
                        decision_price=excluded.decision_price,
                        cycle_time_utc=excluded.cycle_time_utc,
                        expires_at_utc=excluded.expires_at_utc,
                        broker_ticket=excluded.broker_ticket
                    """,
                    (
                        entry.setup_id,
                        entry.submission_uuid,
                        entry.symbol,
                        entry.state.value,
                        _ensure_utc(entry.updated_at_utc, field_name="updated_at_utc").isoformat(),
                        entry.direction.value if entry.direction is not None else None,
                        entry.decision_price,
                        _dt_or_none(entry.cycle_time_utc),
                        _dt_or_none(entry.expires_at_utc),
                        entry.broker_ticket,
                    ),
                )

    def load_execution_registry(self) -> tuple[ExecutionRegistryEntry, ...]:
        with self._read_connection() as conn:
            rows = conn.execute("SELECT * FROM execution_registry ORDER BY updated_at_utc ASC").fetchall()
        return tuple(_row_to_execution_entry(row) for row in rows)

    def store_execution_event(self, *, submission_uuid: str, event_type: str, payload: Mapping[str, Any]) -> None:
        with self._write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO execution_events (submission_uuid, event_type, event_time_utc, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (submission_uuid, event_type, _now_utc().isoformat(), _json_dumps(payload)),
            )

    def store_position(self, position: PositionSnapshot) -> None:
        with self._write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO positions (
                    symbol, direction, setup_id, correlation_group,
                    risk_pct, signal_score, open_time_utc, pyramid_count, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(setup_id) DO UPDATE SET
                    symbol=excluded.symbol,
                    direction=excluded.direction,
                    correlation_group=excluded.correlation_group,
                    risk_pct=excluded.risk_pct,
                    signal_score=excluded.signal_score,
                    open_time_utc=excluded.open_time_utc,
                    pyramid_count=excluded.pyramid_count,
                    payload_json=excluded.payload_json
                """,
                (
                    position.symbol,
                    position.direction.value,
                    position.setup_id,
                    position.correlation_group,
                    position.risk_pct,
                    position.signal_score,
                    _dt_or_none(position.open_time_utc),
                    position.pyramid_count,
                    _json_dumps({"symbol": position.symbol, "setup_id": position.setup_id}),
                ),
            )

    def load_positions(self) -> tuple[dict[str, Any], ...]:
        with self._read_connection() as conn:
            rows = conn.execute("SELECT * FROM positions ORDER BY setup_id ASC").fetchall()
        return tuple(dict(row) for row in rows)

    def store_health_state(self, component: str, state: HealthState, payload: Mapping[str, Any] | None = None) -> None:
        with self._write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO health_state (component, state, updated_at_utc, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(component) DO UPDATE SET
                    state=excluded.state,
                    updated_at_utc=excluded.updated_at_utc,
                    payload_json=excluded.payload_json
                """,
                (component, state.value, _now_utc().isoformat(), _json_dumps(payload or {})),
            )

    def store_recovery_event(self, record: RecoveryEventRecord) -> None:
        with self._write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO recovery_events (event_time_utc, stage, outcome, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    _ensure_utc(record.event_time_utc, field_name="event_time_utc").isoformat(),
                    record.stage,
                    record.outcome,
                    record.payload_json,
                ),
            )

    def store_telemetry_index(self, topic: str, payload: Mapping[str, Any]) -> None:
        with self._write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO telemetry_index (event_time_utc, topic, payload_json)
                VALUES (?, ?, ?)
                """,
                (_now_utc().isoformat(), topic, _json_dumps(payload)),
            )

    def count_rows(self, table: str) -> int:
        with self._read_connection() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        return int(row["count"]) if row is not None else 0

    def set_meta(self, key: str, value: str) -> None:
        self._set_meta(key, value)

    def get_meta(self, key: str) -> str | None:
        return self._get_meta(key)

    def ensure_schema(self) -> None:
        if self._connection is None:
            self.initialize()
        self.assert_compatible(schema_version=SCHEMA_VERSION, config_fingerprint=self.get_config_fingerprint() or "")

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        self._ensure_connection()
        assert self._connection is not None
        yield self._connection

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        self._ensure_connection()
        assert self._connection is not None
        conn = self._connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def _ensure_connection(self) -> None:
        if self._connection is None:
            raise ConfigValidationError("SQLiteRuntimeStore is not initialized")

    def _set_meta(self, key: str, value: str) -> None:
        with self._write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO persistence_meta (meta_key, meta_value, updated_at_utc)
                VALUES (?, ?, ?)
                ON CONFLICT(meta_key) DO UPDATE SET
                    meta_value=excluded.meta_value,
                    updated_at_utc=excluded.updated_at_utc
                """,
                (key, value, _now_utc().isoformat()),
            )

    def _get_meta(self, key: str) -> str | None:
        with self._read_connection() as conn:
            row = conn.execute(
                "SELECT meta_value FROM persistence_meta WHERE meta_key = ?",
                (key,),
            ).fetchone()
        return None if row is None else str(row["meta_value"])


def _row_to_execution_entry(row: sqlite3.Row) -> ExecutionRegistryEntry:
    direction_raw = row["direction"]
    return ExecutionRegistryEntry(
        setup_id=str(row["setup_id"]),
        submission_uuid=str(row["submission_uuid"]),
        symbol=str(row["symbol"]),
        state=ExecutionRegistryState(str(row["state"])),
        updated_at_utc=_parse_utc(str(row["updated_at_utc"])),
        direction=None if direction_raw is None else _direction_from_raw(str(direction_raw)),
        decision_price=None if row["decision_price"] is None else float(row["decision_price"]),
        cycle_time_utc=_parse_utc_or_none(row["cycle_time_utc"]),
        expires_at_utc=_parse_utc_or_none(row["expires_at_utc"]),
        broker_ticket=None if row["broker_ticket"] is None else int(row["broker_ticket"]),
    )


def _direction_from_raw(raw: str):
    from .enums import Direction

    return Direction(raw)


def _parse_utc(raw: str) -> datetime:
    return datetime.fromisoformat(raw).astimezone(timezone.utc)


def _parse_utc_or_none(raw: Any) -> datetime | None:
    if raw is None:
        return None
    return _parse_utc(str(raw))


def _ensure_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ConfigValidationError(f"{field_name} must be timezone-aware UTC datetime")
    return value.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return _ensure_utc(value, field_name="json_datetime").isoformat()
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "__dict__"):
        return value.__dict__
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def _dt_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _ensure_utc(value, field_name="datetime").isoformat()
