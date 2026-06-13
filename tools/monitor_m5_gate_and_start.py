from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tsp_v2.adapters.market_adapter import MT5MarketAdapter
from tsp_v2.adapters.mt5_bridge import MT5Bridge
from tsp_v2.snapshots import MIN_BARS_BREAKOUT_M5, build_market_snapshot


DEFAULT_CONFIG = Path("deploy/v2/configs/forward_live.yaml")
DEFAULT_ENV = Path(".env")
DEFAULT_LOG = Path("logs/monitor_m5_gate_and_start.log")


@dataclass(slots=True)
class TelemetrySnapshot:
    stage: str | None
    symbol: str | None
    timeframe: str | None
    returned_bars: int | None
    closed_bar_count: int | None
    minimum_closed_bar_count: int | None
    cycle_time_utc: str | None
    payload_health: str | None
    raw_payload: dict[str, Any]

    @property
    def gate_open(self) -> bool:
        if self.closed_bar_count is None or self.minimum_closed_bar_count is None:
            return False
        return self.closed_bar_count >= self.minimum_closed_bar_count

    @property
    def is_m5(self) -> bool:
        return self.timeframe == "M5"


def _load_legacy_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    mapping = {
        "MT5_LOGIN": "TSP_V2_MT5_LOGIN",
        "MT5_PASSWORD": "TSP_V2_MT5_PASSWORD",
        "MT5_SERVER": "TSP_V2_MT5_SERVER",
        "MT5_TERMINAL_PATH": "TSP_V2_MT5_TERMINAL_PATH",
        "TSP_MT5_LOGIN": "TSP_V2_MT5_LOGIN",
        "TSP_MT5_PASSWORD": "TSP_V2_MT5_PASSWORD",
        "TSP_MT5_SERVER": "TSP_V2_MT5_SERVER",
        "TSP_MT5_TERMINAL_PATH": "TSP_V2_MT5_TERMINAL_PATH",
    }
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in mapping:
            continue
        mapped_key = mapping[key]
        if not os.environ.get(mapped_key):
            os.environ[mapped_key] = value.strip().strip('"').strip("'")


def _prepare_runtime_env(env_path: Path) -> None:
    _load_legacy_env(env_path)


def _read_latest_telemetry(sqlite_path: Path) -> TelemetrySnapshot | None:
    if not sqlite_path.exists():
        return None
    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT payload_json
            FROM telemetry_index
            WHERE topic = 'deployment.market_data_readiness'
            ORDER BY rowid DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    payload = json.loads(row["payload_json"])
    return TelemetrySnapshot(
        stage=payload.get("stage"),
        symbol=payload.get("symbol"),
        timeframe=payload.get("timeframe"),
        returned_bars=_as_int(payload.get("returned_bars")),
        closed_bar_count=_as_int(payload.get("closed_bar_count")),
        minimum_closed_bar_count=_as_int(payload.get("minimum_closed_bar_count")),
        cycle_time_utc=payload.get("cycle_time_utc"),
        payload_health=payload.get("payload_health"),
        raw_payload=payload,
    )


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _run_command(args: list[str]) -> int:
    proc = subprocess.run(args, check=False)
    return int(proc.returncode)


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _log_line(log_path: Path, message: str) -> None:
    _ensure_parent(log_path)
    timestamp = datetime.now(timezone.utc).isoformat()
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {message}\n")


def _m5_window(broker_time: datetime) -> tuple[datetime, datetime, datetime]:
    cycle_open = broker_time.replace(minute=(broker_time.minute // 5) * 5, second=0, microsecond=0)
    cycle_close = cycle_open + timedelta(minutes=5)
    safe_after = cycle_close + timedelta(seconds=5)
    return cycle_open, cycle_close, safe_after


def _print_snapshot(index: int, snapshot: TelemetrySnapshot | None) -> None:
    if snapshot is None:
        print(f"[{index}] telemetry=NONE")
        return
    print(
        f"[{index}] stage={snapshot.stage} symbol={snapshot.symbol} timeframe={snapshot.timeframe} "
        f"returned_bars={snapshot.returned_bars} closed_bar_count={snapshot.closed_bar_count} "
        f"minimum_closed_bar_count={snapshot.minimum_closed_bar_count} cycle_time_utc={snapshot.cycle_time_utc} "
        f"payload_health={snapshot.payload_health} gate_open={snapshot.gate_open}"
    )


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _next_m5_close_wait(snapshot: TelemetrySnapshot | None, minimum_wait_seconds: int = 5) -> int:
    if snapshot is None or not snapshot.is_m5:
        return 15
    cycle_time = _parse_utc(snapshot.cycle_time_utc)
    if cycle_time is None:
        return 15
    minutes_to_add = 5 - (cycle_time.minute % 5)
    if minutes_to_add == 0:
        minutes_to_add = 5
    next_close = cycle_time.replace(second=0, microsecond=0) + timedelta(minutes=minutes_to_add)
    wait_until = next_close + timedelta(seconds=minimum_wait_seconds)
    now = datetime.now(timezone.utc)
    wait_seconds = int((wait_until - now).total_seconds())
    return max(wait_seconds, minimum_wait_seconds)


def _wait_for_m5_close(market_adapter: MT5MarketAdapter, *, sample_idx: int, log_path: Path) -> tuple[datetime, datetime, datetime]:
    while True:
        broker_time = market_adapter.get_broker_time()
        cycle_open, cycle_close, safe_after = _m5_window(broker_time)
        if broker_time >= safe_after:
            return broker_time, cycle_open, cycle_close
        wait_seconds = max(1, int((safe_after - broker_time).total_seconds()))
        message = (
            f"[{sample_idx}] wait_for_m5_close broker_time={broker_time.isoformat()} "
            f"cycle_open={cycle_open.isoformat()} cycle_close={cycle_close.isoformat()} "
            f"safe_after={safe_after.isoformat()} wait_seconds={wait_seconds}"
        )
        print(message)
        _log_line(log_path, message)
        time.sleep(wait_seconds)


def _build_live_sample(
    *,
    config: Any,
    market_adapter: MT5MarketAdapter,
    symbol: str,
    broker_time: datetime,
) -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []

    def diagnostics_hook(payload: dict[str, Any]) -> None:
        diagnostics.append(payload)

    try:
        snapshot = build_market_snapshot(
            market_adapter,
            config=config,
            symbol=symbol,
            cycle_time_utc=broker_time,
            diagnostics_hook=diagnostics_hook,
        )
    except Exception as exc:
        payload = diagnostics[-1] if diagnostics else {}
        return {
            "stage": payload.get("stage", "bridge_error"),
            "symbol": payload.get("symbol", symbol),
            "timeframe": payload.get("timeframe", "M5"),
            "cycle_time_utc": payload.get("cycle_time_utc", broker_time.isoformat()),
            "returned_bars": payload.get("returned_bars", 0),
            "closed_bar_count": payload.get("closed_bar_count", 0),
            "minimum_closed_bar_count": payload.get("minimum_closed_bar_count", MIN_BARS_BREAKOUT_M5),
            "payload_health": payload.get("payload_health"),
            "gate_open": False,
            "exception_class": exc.__class__.__name__,
            "message": str(exc),
            "payload": payload,
        }

    returned_counts = snapshot.payload_diagnostics.get("returned_counts", {})
    closed_counts = snapshot.payload_diagnostics.get("closed_counts", {})
    return {
        "stage": "snapshot_ready",
        "symbol": snapshot.symbol,
        "timeframe": "M5",
        "cycle_time_utc": snapshot.cycle_time_utc.isoformat(),
        "returned_bars": returned_counts.get("M5", len(snapshot.bars_m5)),
        "closed_bar_count": closed_counts.get("M5", len(snapshot.bars_m5)),
        "minimum_closed_bar_count": MIN_BARS_BREAKOUT_M5,
        "payload_health": snapshot.payload_health.value,
        "gate_open": len(snapshot.bars_m5) >= MIN_BARS_BREAKOUT_M5,
        "payload": {
            "requested_counts": snapshot.payload_diagnostics.get("requested_counts", {}),
            "returned_counts": returned_counts,
            "closed_counts": closed_counts,
            "payload_health": snapshot.payload_health.value,
        },
    }


def main() -> int:
    config_path = DEFAULT_CONFIG
    env_path = DEFAULT_ENV
    max_samples = 10
    start_max_cycles = 10
    log_path = DEFAULT_LOG

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    _prepare_runtime_env(env_path)

    # keep config loading identical to the governed launcher
    from tsp_v2.config import load_config

    config = load_config(config_path=config_path, env_path=env_path)
    sqlite_path = config.persistence.sqlite_path
    py = sys.executable
    primary_symbol = config.symbols.allowlist[0]
    bridge = MT5Bridge(
        terminal_path=config.secrets.mt5_terminal_path,
        login=config.secrets.mt5_login,
        password=config.secrets.mt5_password,
        server=config.secrets.mt5_server,
    )
    connect_status = bridge.connect()
    if not connect_status.ok:
        message = f"WATCHER_CONNECT_FAILED path={bridge.last_connect_path} message={connect_status.message}"
        print(message)
        _log_line(log_path, message)
        return 1
    market_adapter = MT5MarketAdapter(bridge=bridge, primary_symbol=primary_symbol)

    try:
        print(f"WATCHER_START sqlite_path={sqlite_path}")
        _log_line(log_path, f"WATCHER_START sqlite_path={sqlite_path}")
        for sample_idx in range(1, max_samples + 1):
            broker_time, cycle_open, cycle_close = _wait_for_m5_close(market_adapter, sample_idx=sample_idx, log_path=log_path)
            print(f"=== SAMPLE {sample_idx}/{max_samples} ===")
            _log_line(log_path, f"=== SAMPLE {sample_idx}/{max_samples} ===")

            sample = _build_live_sample(
                config=config,
                market_adapter=market_adapter,
                symbol=primary_symbol,
                broker_time=broker_time,
            )
            sample_line = (
                f"[{sample_idx}] broker_time={broker_time.isoformat()} "
                f"cycle_open={cycle_open.isoformat()} cycle_close={cycle_close.isoformat()} "
                f"stage={sample['stage']} symbol={sample['symbol']} timeframe={sample['timeframe']} "
                f"returned_bars={sample['returned_bars']} closed_bar_count={sample['closed_bar_count']} "
                f"minimum_closed_bar_count={sample['minimum_closed_bar_count']} payload_health={sample['payload_health']} "
                f"gate_open={sample['gate_open']}"
            )
            print(sample_line)
            _log_line(log_path, sample_line)

            if sample["gate_open"]:
                print("GATE_OPEN -> launching preflight")
                _log_line(log_path, "GATE_OPEN -> launching preflight")
                preflight_code = _run_command(
                    [
                        py,
                        "-m",
                        "tsp_v2.run_v2",
                        "preflight",
                        "--config",
                        str(config_path),
                        "--env-file",
                        str(env_path),
                    ]
                )
                print(f"preflight_exit={preflight_code}")
                _log_line(log_path, f"preflight_exit={preflight_code}")
                if preflight_code != 0:
                    return preflight_code

                print("GATE_OPEN -> launching start")
                _log_line(log_path, "GATE_OPEN -> launching start")
                start_code = _run_command(
                    [
                        py,
                        "-m",
                        "tsp_v2.run_v2",
                        "start",
                        "--config",
                        str(config_path),
                        "--env-file",
                        str(env_path),
                        "--max-cycles",
                        str(start_max_cycles),
                    ]
                )
                print(f"start_exit={start_code}")
                _log_line(log_path, f"start_exit={start_code}")
                latest_telemetry = _read_latest_telemetry(sqlite_path)
                if latest_telemetry is not None:
                    telemetry_line = (
                        f"latest_telemetry stage={latest_telemetry.stage} symbol={latest_telemetry.symbol} "
                        f"timeframe={latest_telemetry.timeframe} returned_bars={latest_telemetry.returned_bars} "
                        f"closed_bar_count={latest_telemetry.closed_bar_count} "
                        f"minimum_closed_bar_count={latest_telemetry.minimum_closed_bar_count} "
                        f"cycle_time_utc={latest_telemetry.cycle_time_utc} "
                        f"payload_health={latest_telemetry.payload_health}"
                    )
                    print(telemetry_line)
                    _log_line(log_path, telemetry_line)
                return start_code

        print("WATCHER_TIMEOUT")
        _log_line(log_path, "WATCHER_TIMEOUT")
        return 1
    finally:
        try:
            bridge.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
