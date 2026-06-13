"""SQLite persistence support for TSP V1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from .execution import ExecutionRegistry, ExecutionRegistryEntry, RegistryStatus
from .position_manager import LifecycleResult
from .risk import RiskDecision
from .state import (
    CompetitionContext,
    Direction,
    ExecutionResult,
    GovernorState,
    LayerState,
    Module,
    PositionState,
    RegimeResult,
    RuntimeState,
    SignalScore,
    TradePhase,
)


SCHEMA_VERSION = 1


def _to_utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return _to_utc_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "name"):
        return value.name
    return str(value)


def _parse_dt(raw: str | None) -> datetime | None:
    if raw is None or not raw.strip():
        return None
    return datetime.fromisoformat(raw)


@dataclass(frozen=True, slots=True)
class PersistedRuntimeSnapshot:
    last_bar_time: datetime | None
    kill_switch_active: bool
    kill_reason: str
    consecutive_bar_errors: int


@dataclass(frozen=True, slots=True)
class PersistedBootstrapState:
    runtime: PersistedRuntimeSnapshot
    competition_ctx: CompetitionContext | None
    position: PositionState
    config_fingerprint: str | None
    registry_entries: tuple[ExecutionRegistryEntry, ...]


class SQLitePersistence:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS persistence_meta (
                        key TEXT PRIMARY KEY,
                        value_text TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS competition_context (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        total_days INTEGER NOT NULL,
                        start_equity REAL NOT NULL,
                        starting_date TEXT NOT NULL,
                        total_pnl_r REAL NOT NULL,
                        daily_pnl_r REAL NOT NULL,
                        session_pnl_r REAL NOT NULL,
                        session_loss_count INTEGER NOT NULL,
                        session_risk_committed_r REAL NOT NULL,
                        current_session TEXT NOT NULL,
                        governor_state TEXT NOT NULL,
                        days_elapsed INTEGER NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS governor_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        governor_state TEXT NOT NULL,
                        aggression_bias REAL NOT NULL,
                        threshold_mod REAL NOT NULL,
                        budget_r REAL NOT NULL,
                        session_pause INTEGER NOT NULL,
                        note TEXT NOT NULL,
                        total_pnl_r REAL,
                        dd_pct REAL
                    );
                    CREATE TABLE IF NOT EXISTS execution_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        setup_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        ticket INTEGER,
                        fill_price REAL,
                        fill_lot REAL,
                        sl_confirmed INTEGER NOT NULL,
                        tp_confirmed INTEGER NOT NULL,
                        retcode INTEGER,
                        retcode_class TEXT NOT NULL,
                        slippage_ticks REAL,
                        latency_ms REAL,
                        attempt_count INTEGER NOT NULL,
                        note TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS execution_registry (
                        setup_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        ticket INTEGER
                    );
                    CREATE TABLE IF NOT EXISTS lifecycle_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        ticket INTEGER,
                        action TEXT NOT NULL,
                        note TEXT NOT NULL,
                        pnl_r REAL
                    );
                    CREATE TABLE IF NOT EXISTS position_layers (
                        ticket INTEGER PRIMARY KEY,
                        direction TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        sl_price REAL NOT NULL,
                        tp_price REAL,
                        lot_size REAL NOT NULL,
                        r_risk REAL NOT NULL,
                        initial_r_distance REAL NOT NULL,
                        open_time TEXT NOT NULL,
                        layer_index INTEGER NOT NULL,
                        module TEXT NOT NULL,
                        setup_id TEXT NOT NULL,
                        partial_taken INTEGER NOT NULL,
                        bars_in_trade INTEGER NOT NULL,
                        tp_attach_attempts INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS regime_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        regime_name TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        direction_bias TEXT NOT NULL,
                        conflict_note TEXT NOT NULL,
                        raw_scores_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS signal_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        setup_id TEXT NOT NULL,
                        module TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        score REAL NOT NULL,
                        confidence_tier TEXT NOT NULL,
                        entry_hint REAL NOT NULL,
                        invalidation_anchor REAL NOT NULL,
                        metadata_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS risk_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        action TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        r_percent REAL NOT NULL,
                        effective_equity REAL NOT NULL,
                        lot_size REAL NOT NULL,
                        entry_price REAL NOT NULL,
                        invalidation_price REAL NOT NULL,
                        allow_pyramid INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS runtime_counters (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        last_bar_time TEXT,
                        consecutive_bar_errors INTEGER NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS bot_state (
                        key TEXT PRIMARY KEY,
                        value_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                row = conn.execute(
                    "SELECT value_text FROM persistence_meta WHERE key = 'schema_version'"
                ).fetchone()
                if row is None:
                    self._upsert_meta(conn, "schema_version", str(SCHEMA_VERSION))
                self._assert_schema_version(conn)
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(
                f"SQLite persistence failed to initialize: {self.db_path}. "
                "Treat this as a fatal operator issue; inspect or rebuild the database."
            ) from exc

    def load_bootstrap_state(self, *, now: datetime) -> PersistedBootstrapState:
        try:
            with self._connect() as conn:
                self._assert_schema_version(conn)
                runtime = self._load_runtime_snapshot_conn(conn)
                competition_ctx = self._load_competition_context_conn(conn)
                position = self._load_position_state_conn(conn)
                fingerprint = self._load_config_fingerprint_conn(conn)
                registry_entries = self._load_registry_entries_conn(conn, now=now)
            return PersistedBootstrapState(
                runtime=runtime,
                competition_ctx=competition_ctx,
                position=position,
                config_fingerprint=fingerprint,
                registry_entries=registry_entries,
            )
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(
                f"SQLite persistence failed during bootstrap load: {self.db_path}. "
                "Treat this as fatal and require operator acknowledgement."
            ) from exc

    def save_config_fingerprint(self, fingerprint: str) -> None:
        try:
            with self._connect() as conn:
                self._assert_schema_version(conn)
                self._upsert_bot_state_conn(
                    conn,
                    "config_fingerprint",
                    json.dumps({"fingerprint": fingerprint}, sort_keys=True),
                )
        except sqlite3.DatabaseError as exc:
            raise RuntimeError("Failed to persist config fingerprint") from exc

    def save_runtime_counters(self, *, last_bar_time: datetime | None, consecutive_bar_errors: int) -> None:
        try:
            with self._connect() as conn:
                self._assert_schema_version(conn)
                self._save_runtime_counters_conn(
                    conn,
                    last_bar_time=last_bar_time,
                    consecutive_bar_errors=consecutive_bar_errors,
                )
        except sqlite3.DatabaseError as exc:
            raise RuntimeError("Failed to persist runtime counters") from exc

    def save_competition_context(self, ctx: CompetitionContext) -> None:
        try:
            with self._connect() as conn:
                self._assert_schema_version(conn)
                self._save_competition_context_conn(conn, ctx)
        except sqlite3.DatabaseError as exc:
            raise RuntimeError("Failed to persist competition context") from exc

    def replace_position_layers(self, position: PositionState) -> None:
        try:
            with self._connect() as conn:
                self._assert_schema_version(conn)
                self._replace_position_layers_conn(conn, position)
        except sqlite3.DatabaseError as exc:
            raise RuntimeError("Failed to persist position layers") from exc

    def load_runtime_snapshot(self) -> PersistedRuntimeSnapshot:
        return self.load_bootstrap_state(now=datetime.now(timezone.utc)).runtime

    def load_competition_context(self) -> CompetitionContext | None:
        return self.load_bootstrap_state(now=datetime.now(timezone.utc)).competition_ctx

    def load_position_state(self) -> PositionState:
        return self.load_bootstrap_state(now=datetime.now(timezone.utc)).position

    def persist_bar_cycle(
        self,
        *,
        runtime: RuntimeState,
        last_bar_time: datetime | None,
        regime: RegimeResult,
        snap_timestamp: datetime,
        governor,
        signal: SignalScore | None,
        risk_decision: RiskDecision | None,
        execution: ExecutionResult | None,
        lifecycle: LifecycleResult | None,
        registry: ExecutionRegistry,
        config_fingerprint: str,
    ) -> None:
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._assert_schema_version(conn)
                self._save_runtime_counters_conn(
                    conn,
                    last_bar_time=last_bar_time,
                    consecutive_bar_errors=runtime.consecutive_bar_errors,
                )
                self._upsert_bot_state_conn(
                    conn,
                    "kill_switch_state",
                    json.dumps(
                        {"active": runtime.kill_switch_active, "reason": runtime.kill_reason},
                        default=_json_default,
                        sort_keys=True,
                    ),
                )
                self._upsert_bot_state_conn(
                    conn,
                    "config_fingerprint",
                    json.dumps({"fingerprint": config_fingerprint}, sort_keys=True),
                )
                if runtime.competition_ctx is not None:
                    self._save_competition_context_conn(conn, runtime.competition_ctx)
                self._replace_position_layers_conn(conn, runtime.position)
                self._replace_registry_entries_conn(conn, registry.snapshot(snap_timestamp))
                self._log_regime_conn(conn, regime, snap_timestamp)
                self._log_governor_conn(
                    conn,
                    timestamp=snap_timestamp,
                    state=governor.governor_state.name,
                    aggression_bias=governor.aggression_bias,
                    threshold_mod=governor.threshold_modifier,
                    budget_r=governor.session_risk_budget_r,
                    session_pause=governor.session_pause,
                    note=governor.governor_note,
                    total_pnl_r=runtime.competition_ctx.total_pnl_r if runtime.competition_ctx is not None else None,
                    dd_pct=((runtime.equity_peak - runtime.equity_current) / runtime.equity_peak * 100.0)
                    if runtime.equity_peak > 0
                    else None,
                )
                if signal is not None:
                    self._log_signal_conn(conn, signal)
                if risk_decision is not None:
                    self._log_risk_conn(conn, risk_decision, snap_timestamp)
                if execution is not None:
                    self._log_execution_conn(conn, execution)
                if lifecycle is not None:
                    self._log_lifecycle_conn(conn, lifecycle)
                conn.commit()
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(
                f"Failed to atomically persist TSP bar cycle to {self.db_path}"
            ) from exc

    def log_lifecycle(self, result: LifecycleResult) -> None:
        try:
            with self._connect() as conn:
                self._assert_schema_version(conn)
                self._log_lifecycle_conn(conn, result)
        except sqlite3.DatabaseError as exc:
            raise RuntimeError("Failed to persist lifecycle events") from exc

    def _load_runtime_snapshot_conn(self, conn: sqlite3.Connection) -> PersistedRuntimeSnapshot:
        row = conn.execute(
            "SELECT last_bar_time, consecutive_bar_errors FROM runtime_counters WHERE id = 1"
        ).fetchone()
        kill_row = conn.execute(
            "SELECT value_json FROM bot_state WHERE key = 'kill_switch_state'"
        ).fetchone()
        last_bar_time = _parse_dt(row["last_bar_time"]) if row else None
        consecutive_bar_errors = int(row["consecutive_bar_errors"]) if row else 0
        kill_switch_active = False
        kill_reason = ""
        if kill_row:
            raw = json.loads(kill_row["value_json"])
            kill_switch_active = bool(raw.get("active", False))
            kill_reason = str(raw.get("reason", ""))
        return PersistedRuntimeSnapshot(
            last_bar_time=last_bar_time,
            kill_switch_active=kill_switch_active,
            kill_reason=kill_reason,
            consecutive_bar_errors=consecutive_bar_errors,
        )

    def _load_competition_context_conn(self, conn: sqlite3.Connection) -> CompetitionContext | None:
        row = conn.execute("SELECT * FROM competition_context WHERE id = 1").fetchone()
        if row is None:
            return None
        return CompetitionContext(
            total_days=int(row["total_days"]),
            start_equity=float(row["start_equity"]),
            starting_date=date.fromisoformat(row["starting_date"]),
            total_pnl_r=float(row["total_pnl_r"]),
            daily_pnl_r=float(row["daily_pnl_r"]),
            session_pnl_r=float(row["session_pnl_r"]),
            session_loss_count=int(row["session_loss_count"]),
            session_risk_committed_r=float(row["session_risk_committed_r"]),
            current_session=str(row["current_session"]),
            governor_state=GovernorState[str(row["governor_state"])],
            days_elapsed=int(row["days_elapsed"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _load_position_state_conn(self, conn: sqlite3.Connection) -> PositionState:
        rows = conn.execute("SELECT * FROM position_layers ORDER BY layer_index").fetchall()
        layers: list[LayerState] = []
        for row in rows:
            layers.append(
                LayerState(
                    ticket=int(row["ticket"]),
                    direction=Direction[row["direction"]],
                    entry_price=float(row["entry_price"]),
                    sl_price=float(row["sl_price"]),
                    tp_price=float(row["tp_price"]) if row["tp_price"] is not None else None,
                    lot_size=float(row["lot_size"]),
                    r_risk=float(row["r_risk"]),
                    initial_r_distance=float(row["initial_r_distance"]),
                    open_time=datetime.fromisoformat(row["open_time"]),
                    layer_index=int(row["layer_index"]),
                    module=Module[row["module"]],
                    setup_id=str(row["setup_id"]),
                    partial_taken=bool(row["partial_taken"]),
                    bars_in_trade=int(row["bars_in_trade"]),
                    tp_attach_attempts=int(row["tp_attach_attempts"]),
                )
            )
        return self._position_state_from_layers(layers)

    def _load_config_fingerprint_conn(self, conn: sqlite3.Connection) -> str | None:
        row = conn.execute(
            "SELECT value_json FROM bot_state WHERE key = 'config_fingerprint'"
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["value_json"])
        value = payload.get("fingerprint")
        return str(value) if isinstance(value, str) and value.strip() else None

    def _load_registry_entries_conn(
        self,
        conn: sqlite3.Connection,
        *,
        now: datetime,
    ) -> tuple[ExecutionRegistryEntry, ...]:
        current_iso = _to_utc_iso(now)
        conn.execute(
            "DELETE FROM execution_registry WHERE expires_at <= ?",
            (current_iso,),
        )
        rows = conn.execute(
            "SELECT * FROM execution_registry ORDER BY created_at"
        ).fetchall()
        entries = []
        for row in rows:
            entries.append(
                ExecutionRegistryEntry(
                    setup_id=str(row["setup_id"]),
                    status=RegistryStatus(str(row["status"])),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    expires_at=datetime.fromisoformat(row["expires_at"]),
                    ticket=int(row["ticket"]) if row["ticket"] is not None else None,
                )
            )
        return tuple(entries)

    def _save_runtime_counters_conn(
        self,
        conn: sqlite3.Connection,
        *,
        last_bar_time: datetime | None,
        consecutive_bar_errors: int,
    ) -> None:
        now_iso = _to_utc_iso(datetime.now(timezone.utc))
        conn.execute(
            """
            INSERT INTO runtime_counters (id, last_bar_time, consecutive_bar_errors, updated_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_bar_time = excluded.last_bar_time,
                consecutive_bar_errors = excluded.consecutive_bar_errors,
                updated_at = excluded.updated_at
            """,
            (_to_utc_iso(last_bar_time), consecutive_bar_errors, now_iso),
        )

    def _save_competition_context_conn(self, conn: sqlite3.Connection, ctx: CompetitionContext) -> None:
        conn.execute(
            """
            INSERT INTO competition_context (
                id, total_days, start_equity, starting_date, total_pnl_r,
                daily_pnl_r, session_pnl_r, session_loss_count,
                session_risk_committed_r, current_session, governor_state,
                days_elapsed, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                total_days = excluded.total_days,
                start_equity = excluded.start_equity,
                starting_date = excluded.starting_date,
                total_pnl_r = excluded.total_pnl_r,
                daily_pnl_r = excluded.daily_pnl_r,
                session_pnl_r = excluded.session_pnl_r,
                session_loss_count = excluded.session_loss_count,
                session_risk_committed_r = excluded.session_risk_committed_r,
                current_session = excluded.current_session,
                governor_state = excluded.governor_state,
                days_elapsed = excluded.days_elapsed,
                updated_at = excluded.updated_at
            """,
            (
                ctx.total_days,
                ctx.start_equity,
                ctx.starting_date.isoformat(),
                ctx.total_pnl_r,
                ctx.daily_pnl_r,
                ctx.session_pnl_r,
                ctx.session_loss_count,
                ctx.session_risk_committed_r,
                ctx.current_session,
                ctx.governor_state.name if hasattr(ctx.governor_state, "name") else str(ctx.governor_state),
                ctx.days_elapsed,
                _to_utc_iso(ctx.updated_at),
            ),
        )

    def _replace_position_layers_conn(self, conn: sqlite3.Connection, position: PositionState) -> None:
        conn.execute("DELETE FROM position_layers")
        for layer in position.layers:
            conn.execute(
                """
                INSERT INTO position_layers (
                    ticket, direction, entry_price, sl_price, tp_price, lot_size,
                    r_risk, initial_r_distance, open_time, layer_index, module,
                    setup_id, partial_taken, bars_in_trade, tp_attach_attempts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    layer.ticket,
                    layer.direction.name,
                    layer.entry_price,
                    layer.sl_price,
                    layer.tp_price,
                    layer.lot_size,
                    layer.r_risk,
                    layer.initial_r_distance,
                    _to_utc_iso(layer.open_time),
                    layer.layer_index,
                    layer.module.name,
                    layer.setup_id,
                    int(layer.partial_taken),
                    layer.bars_in_trade,
                    layer.tp_attach_attempts,
                ),
            )

    def _replace_registry_entries_conn(
        self,
        conn: sqlite3.Connection,
        entries: tuple[ExecutionRegistryEntry, ...],
    ) -> None:
        conn.execute("DELETE FROM execution_registry")
        for entry in entries:
            conn.execute(
                """
                INSERT INTO execution_registry (setup_id, status, created_at, expires_at, ticket)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entry.setup_id,
                    entry.status.value,
                    _to_utc_iso(entry.created_at),
                    _to_utc_iso(entry.expires_at),
                    entry.ticket,
                ),
            )

    def _log_regime_conn(self, conn: sqlite3.Connection, regime: RegimeResult, timestamp: datetime) -> None:
        conn.execute(
            """
            INSERT INTO regime_log (timestamp, regime_name, confidence, direction_bias, conflict_note, raw_scores_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _to_utc_iso(timestamp),
                regime.regime.name,
                regime.confidence,
                regime.direction_bias.name,
                regime.conflict_note,
                json.dumps(dict(regime.raw_scores), default=_json_default, sort_keys=True),
            ),
        )

    def _log_signal_conn(self, conn: sqlite3.Connection, signal: SignalScore) -> None:
        conn.execute(
            """
            INSERT INTO signal_log (timestamp, setup_id, module, direction, score, confidence_tier, entry_hint, invalidation_anchor, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _to_utc_iso(signal.signal_timestamp),
                signal.setup_id,
                signal.module.name,
                signal.direction.name,
                signal.score,
                signal.confidence_tier.value,
                signal.entry_hint,
                signal.invalidation_anchor,
                json.dumps(dict(signal.setup_metadata), default=_json_default, sort_keys=True),
            ),
        )

    def _log_risk_conn(self, conn: sqlite3.Connection, decision: RiskDecision, timestamp: datetime) -> None:
        conn.execute(
            """
            INSERT INTO risk_log (timestamp, action, reason, r_percent, effective_equity, lot_size, entry_price, invalidation_price, allow_pyramid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _to_utc_iso(timestamp),
                decision.action,
                decision.reason,
                decision.r_percent,
                decision.effective_equity,
                decision.lot_size,
                decision.entry_price,
                decision.invalidation_price,
                int(decision.allow_pyramid),
            ),
        )

    def _log_execution_conn(self, conn: sqlite3.Connection, result: ExecutionResult) -> None:
        conn.execute(
            """
            INSERT INTO execution_log (
                timestamp, setup_id, status, ticket, fill_price, fill_lot, sl_confirmed,
                tp_confirmed, retcode, retcode_class, slippage_ticks, latency_ms,
                attempt_count, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _to_utc_iso(result.timestamp),
                result.setup_id,
                result.status.value,
                result.ticket,
                result.fill_price,
                result.fill_lot,
                int(result.sl_confirmed),
                int(result.tp_confirmed),
                result.retcode,
                result.retcode_class.value,
                result.slippage_ticks,
                result.latency_ms,
                result.attempt_count,
                result.note,
            ),
        )

    def _log_lifecycle_conn(self, conn: sqlite3.Connection, result: LifecycleResult) -> None:
        for event in result.events:
            conn.execute(
                """
                INSERT INTO lifecycle_events (timestamp, ticket, action, note, pnl_r)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _to_utc_iso(event.timestamp),
                    event.ticket,
                    event.action.value,
                    event.note,
                    event.pnl_r,
                ),
            )

    def _log_governor_conn(
        self,
        conn: sqlite3.Connection,
        *,
        timestamp: datetime,
        state: str,
        aggression_bias: float,
        threshold_mod: float,
        budget_r: float,
        session_pause: bool,
        note: str,
        total_pnl_r: float | None,
        dd_pct: float | None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO governor_log (timestamp, governor_state, aggression_bias, threshold_mod, budget_r, session_pause, note, total_pnl_r, dd_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _to_utc_iso(timestamp),
                state,
                aggression_bias,
                threshold_mod,
                budget_r,
                int(session_pause),
                note,
                total_pnl_r,
                dd_pct,
            ),
        )

    def _upsert_bot_state_conn(self, conn: sqlite3.Connection, key: str, value_json: str) -> None:
        conn.execute(
            """
            INSERT INTO bot_state (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (key, value_json, _to_utc_iso(datetime.now(timezone.utc))),
        )

    def _upsert_meta(self, conn: sqlite3.Connection, key: str, value_text: str) -> None:
        conn.execute(
            """
            INSERT INTO persistence_meta (key, value_text, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_text = excluded.value_text,
                updated_at = excluded.updated_at
            """,
            (key, value_text, _to_utc_iso(datetime.now(timezone.utc))),
        )

    def _assert_schema_version(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT value_text FROM persistence_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            raise RuntimeError("Missing persistence schema_version metadata")
        actual = int(row["value_text"])
        if actual != SCHEMA_VERSION:
            raise RuntimeError(
                f"Unsupported SQLite schema version {actual}; expected {SCHEMA_VERSION}"
            )

    def _position_state_from_layers(self, layers: list[LayerState]) -> PositionState:
        position = PositionState(layers=list(layers))
        if not layers:
            return position
        position.direction = layers[0].direction
        position.module = layers[0].module
        position.phase = TradePhase.PYRAMIDED if len(layers) > 1 else TradePhase.ENTERED
        return position

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


__all__ = [
    "PersistedBootstrapState",
    "PersistedRuntimeSnapshot",
    "SCHEMA_VERSION",
    "SQLitePersistence",
]
