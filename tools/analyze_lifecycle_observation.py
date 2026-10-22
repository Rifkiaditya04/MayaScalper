"""Read-only MAE/MFE analysis for lifecycle telemetry persisted by the forward runtime."""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from statistics import mean, median
from typing import Any, Iterable


TOPICS = (
    "runtime.lifecycle_state_created",
    "runtime.lifecycle_price_sample",
    "runtime.lifecycle_stop_updated",
    "runtime.lifecycle_modify_failed",
    "runtime.lifecycle_state_closed",
)


@dataclass(slots=True)
class PositionObservation:
    ticket: int
    created_at_utc: datetime | None = None
    samples: list[dict[str, Any]] = field(default_factory=list)
    stop_updates: list[dict[str, Any]] = field(default_factory=list)
    modify_failures: list[dict[str, Any]] = field(default_factory=list)
    closed_at_utc: datetime | None = None


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_ticket(payload: dict[str, Any]) -> int | None:
    try:
        ticket = int(payload.get("position_ticket"))
    except (TypeError, ValueError):
        return None
    return ticket if ticket > 0 else None


def load_observations(db_path: Path) -> dict[int, PositionObservation]:
    """Load lifecycle events through a read-only SQLite connection."""
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    placeholders = ", ".join("?" for _ in TOPICS)
    observations: dict[int, PositionObservation] = {}
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute(
            f"""
            SELECT event_time_utc, topic, payload_json
            FROM telemetry_index
            WHERE topic IN ({placeholders})
            ORDER BY id ASC
            """,
            TOPICS,
        )
        for event_time, topic, raw_payload in rows:
            try:
                payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            ticket = _as_ticket(payload)
            if ticket is None:
                continue
            observation = observations.setdefault(ticket, PositionObservation(ticket=ticket))
            timestamp = _parse_utc(payload.get("cycle_time_utc")) or _parse_utc(event_time)
            if topic == "runtime.lifecycle_state_created":
                observation.created_at_utc = timestamp
            elif topic == "runtime.lifecycle_price_sample":
                payload["_timestamp"] = timestamp
                observation.samples.append(payload)
            elif topic == "runtime.lifecycle_stop_updated":
                observation.stop_updates.append(payload)
            elif topic == "runtime.lifecycle_modify_failed":
                observation.modify_failures.append(payload)
            elif topic == "runtime.lifecycle_state_closed":
                observation.closed_at_utc = timestamp
    return observations


def _r_progress(sample: dict[str, Any]) -> float | None:
    try:
        entry = float(sample["entry_price"])
        price = float(sample["current_price"])
        distance = float(sample["initial_r_distance"])
    except (KeyError, TypeError, ValueError):
        return None
    if distance <= 0.0:
        return None
    direction = str(sample.get("direction", "")).upper()
    return (price - entry) / distance if direction == "LONG" else (entry - price) / distance


def summarize_observation(observation: PositionObservation) -> dict[str, Any]:
    samples = []
    for sample in observation.samples:
        r_value = _r_progress(sample)
        if r_value is not None:
            samples.append((sample, r_value))
    samples.sort(key=lambda item: item[0].get("_timestamp") or datetime.min.replace(tzinfo=timezone.utc))
    first = samples[0][0] if samples else {}
    r_values = [item[1] for item in samples]
    mae_r = min(r_values) if r_values else None
    mfe_r = max(r_values) if r_values else None
    final_r = r_values[-1] if r_values else None
    mae_index = r_values.index(mae_r) if mae_r is not None else None
    entry_time = observation.created_at_utc or (samples[0][0].get("_timestamp") if samples else None)
    mae_minutes = None
    recovered_after_mae = False
    if mae_index is not None and entry_time is not None:
        mae_time = samples[mae_index][0].get("_timestamp")
        if isinstance(mae_time, datetime):
            mae_minutes = (mae_time - entry_time).total_seconds() / 60.0
        recovered_after_mae = any(value >= 0.0 for _, value in samples[mae_index + 1 :])
    direction = str(first.get("direction", ""))
    breakeven_reached = any(bool(sample.get("trail_active")) for sample, _ in samples)
    trailing_reached = bool(mfe_r is not None and mfe_r >= 1.5)
    return {
        "ticket": observation.ticket,
        "symbol": first.get("symbol", ""),
        "setup_id": first.get("setup_id", ""),
        "direction": direction,
        "sample_count": len(samples),
        "mae_r": mae_r,
        "mfe_r": mfe_r,
        "final_r": final_r,
        "mae_minutes_after_entry": mae_minutes,
        "breakeven_reached": breakeven_reached,
        "trailing_reached": trailing_reached,
        "modify_failure_count": len(observation.modify_failures),
        "status": "CLOSED" if observation.closed_at_utc is not None else "OPEN_OR_UNRESOLVED",
        "recovered_after_mae": recovered_after_mae,
    }


def render_markdown(summaries: Iterable[dict[str, Any]], *, db_path: Path) -> str:
    rows = list(summaries)
    measured = [row for row in rows if row["mae_r"] is not None]
    mae_values = [float(row["mae_r"]) for row in measured]
    recovered_deep_mae = [
        row for row in measured if float(row["mae_r"]) < -0.5 and bool(row["recovered_after_mae"])
    ]
    likely_initial_stop = [row for row in measured if float(row["final_r"]) <= -0.9]
    failures = sum(int(row["modify_failure_count"]) for row in rows)
    lines = [
        "# Lifecycle Observation Summary",
        "",
        f"Source DB: `{db_path}`",
        "",
        "## Per Trade",
        "",
        "| Ticket | Symbol | Setup | Direction | Samples | MAE (R) | MFE (R) | Final R | Worst MAE Minutes | Breakeven | Trailing | Modify Failures | Status |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {ticket} | {symbol} | {setup_id} | {direction} | {sample_count} | {mae} | {mfe} | {final_r} | {mae_minutes} | {breakeven} | {trailing} | {failures} | {status} |".format(
                ticket=row["ticket"],
                symbol=row["symbol"] or "-",
                setup_id=row["setup_id"] or "-",
                direction=row["direction"] or "-",
                sample_count=row["sample_count"],
                mae=_fmt(row["mae_r"]),
                mfe=_fmt(row["mfe_r"]),
                final_r=_fmt(row["final_r"]),
                mae_minutes=_fmt(row["mae_minutes_after_entry"]),
                breakeven="yes" if row["breakeven_reached"] else "no",
                trailing="yes" if row["trailing_reached"] else "no",
                failures=row["modify_failure_count"],
                status=row["status"],
            )
        )
    percent = (len(recovered_deep_mae) / len(measured) * 100.0) if measured else 0.0
    lines.extend(
        [
            "",
            "## Aggregate",
            "",
            f"- Trades observed: {len(rows)}",
            f"- Trades with price samples: {len(measured)}",
            f"- Average MAE: {_fmt(mean(mae_values) if mae_values else None)} R",
            f"- Median MAE: {_fmt(median(mae_values) if mae_values else None)} R",
            f"- Trades with MAE below -0.5R and a later recovery to >= 0R: {len(recovered_deep_mae)} ({percent:.1f}%)",
            f"- Positions with final observed R <= -0.9: {len(likely_initial_stop)}",
            f"- lifecycle_modify_failed events: {failures}",
            "",
            "A position is considered recovered here only when a sample after its worst MAE reaches non-negative R. Final observed R <= -0.9 is an observational marker only; it does not prove a broker stop-loss fill or realized PnL.",
        ]
    )
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    return "-" if value is None else f"{float(value):.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a read-only lifecycle MAE/MFE observation report")
    parser.add_argument("--db", type=Path, default=Path("runtime/forward_demo/db/tsp_v2_runtime.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("reports/forward_demo/lifecycle_observation_summary.md"))
    args = parser.parse_args()
    if not args.db.exists():
        parser.error(f"database does not exist: {args.db}")
    summaries = [summarize_observation(item) for item in load_observations(args.db).values()]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_markdown(summaries, db_path=args.db), encoding="utf-8")
    print(f"Wrote {args.output} for {len(summaries)} observed positions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
