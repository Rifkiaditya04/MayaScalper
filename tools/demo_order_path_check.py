"""Explicit, demo-only broker check for market SL and SLTP modification.

This tool deliberately sends one minimum-volume demo order only when the
operator supplies --i-understand-this-sends-demo-orders. It is not part of
the deployment runtime and must never be scheduled by the observation tools.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from typing import Any, Mapping, MutableMapping
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "deploy" / "v2" / "configs" / "forward_demo.yaml"
DEFAULT_ENV = REPO_ROOT / ".env"
MAX_TICK_AGE_SECONDS = 5.0


class DemoOrderPathSafetyError(RuntimeError):
    """Raised before an order when a non-negotiable safety guard fails."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one guarded demo SL/SLTP broker path check.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument(
        "--i-understand-this-sends-demo-orders",
        action="store_true",
        help="Required acknowledgement: this command sends and closes one demo market order.",
    )
    return parser


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise DemoOrderPathSafetyError("Broker tick timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def validate_demo_order_path_preconditions(
    bridge: Any,
    *,
    symbol: str,
    now_utc: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Read-only checks shared by the command and its offline tests."""
    account = dict(bridge.query_account())
    trade_mode = account.get("trade_mode")
    if isinstance(trade_mode, bool) or not isinstance(trade_mode, int) or trade_mode != 0:
        raise DemoOrderPathSafetyError("Demo order-path check requires trade_mode=0 (demo)")
    positions = tuple(bridge.query_positions())
    if positions:
        raise DemoOrderPathSafetyError("Demo order-path check requires no open positions")
    pending_orders = tuple(bridge.query_orders())
    if pending_orders:
        raise DemoOrderPathSafetyError("Demo order-path check requires no pending orders")

    canonical_symbol = symbol.strip().upper()
    if not canonical_symbol:
        raise DemoOrderPathSafetyError("Symbol must be non-empty")
    contract = dict(bridge.query_symbol_contract(canonical_symbol))
    tick = dict(bridge.latest_tick(canonical_symbol))
    tick_time = tick.get("time", tick.get("timestamp"))
    if not isinstance(tick_time, datetime):
        raise DemoOrderPathSafetyError("Latest tick did not include a datetime timestamp")
    reference_time = _ensure_utc(now_utc or datetime.now(tz=timezone.utc))
    tick_age_seconds = max(0.0, (reference_time - _ensure_utc(tick_time)).total_seconds())
    if tick_age_seconds > MAX_TICK_AGE_SECONDS:
        raise DemoOrderPathSafetyError(f"Latest tick is stale: {tick_age_seconds:.3f}s")
    return account, contract, tick


def _price_digits(contract: Mapping[str, Any]) -> int:
    return max(0, int(contract.get("digits", 5) or 5))


def _rounded_price(price: float, contract: Mapping[str, Any]) -> float:
    return round(float(price), _price_digits(contract))


def _initial_stop_loss(*, ask: float, contract: Mapping[str, Any]) -> float:
    point = float(contract.get("point", 0.0) or 0.0)
    stop_level_points = float(contract.get("trade_stops_level", 0.0) or 0.0)
    if point <= 0.0:
        raise DemoOrderPathSafetyError("Contract point must be positive")
    distance = max(3.0 * stop_level_points * point, 200.0 * point)
    return _rounded_price(ask - distance, contract)


def _protective_stop_loss(*, bid: float, initial_stop: float, contract: Mapping[str, Any]) -> float:
    point = float(contract.get("point", 0.0) or 0.0)
    stop_level_points = float(contract.get("trade_stops_level", 0.0) or 0.0)
    if point <= 0.0:
        raise DemoOrderPathSafetyError("Contract point must be positive")
    candidate = _rounded_price(bid - max(3.0 * stop_level_points * point, 100.0 * point), contract)
    if candidate <= initial_stop or candidate >= bid:
        raise DemoOrderPathSafetyError("Cannot derive a more protective valid stop loss from the current tick")
    return candidate


def _event(label: str, *, started: datetime, result: Any, **fields: Any) -> None:
    elapsed_ms = (datetime.now(tz=timezone.utc) - started).total_seconds() * 1000.0
    request = dict(getattr(result, "request", {}) or {})
    response = dict(getattr(result, "response", {}) or {})
    payload = {
        "event": label,
        "elapsed_ms": round(elapsed_ms, 1),
        "ok": bool(getattr(result, "ok", getattr(result, "accepted", False))),
        "ticket": getattr(result, "ticket", None),
        "retcode": response.get("retcode") or getattr(result, "failure_class", None),
        "comment": response.get("comment") or getattr(result, "message", None),
        "filling_mode": request.get("type_filling"),
        **fields,
    }
    print("ORDER_PATH_EVENT | " + " | ".join(f"{key}={value}" for key, value in payload.items()))


def _find_position(bridge: Any, ticket: int) -> dict[str, Any]:
    positions = tuple(bridge.query_positions(ticket=ticket))
    if len(positions) != 1:
        raise RuntimeError(f"Expected exactly one filled position for ticket {ticket}; found {len(positions)}")
    return dict(positions[0])


def _cleanup_test_run_positions(
    bridge: Any,
    *,
    symbol: str,
    setup_id: str | None,
    position_tickets: set[int],
) -> None:
    """Best-effort cleanup that never replaces the failure that triggered it."""
    try:
        positions = tuple(bridge.query_positions(symbol=symbol))
    except Exception as exc:
        print(f"ORDER_PATH_CRITICAL | cleanup_query_failed={type(exc).__name__}: {exc}")
        return
    for position in positions:
        ticket = position.get("ticket")
        try:
            if ticket is None:
                raise RuntimeError("Broker position did not contain a ticket")
            position_ticket = int(ticket)
            position_comment = str(position.get("comment", ""))
            is_owned_ticket = position_ticket in position_tickets
            is_owned_comment = bool(setup_id and setup_id in position_comment)
            if not (is_owned_ticket or is_owned_comment):
                continue
            started = datetime.now(tz=timezone.utc)
            result = bridge.close_position(position_ticket, comment="DEMO_ORDER_PATH_CHECK_FINALLY")
            _event("finally_close_position", started=started, result=result)
        except Exception as exc:
            print(f"ORDER_PATH_CRITICAL | finally_close_failed={type(exc).__name__}: {exc}")


def _run_connected_order_path_check(
    bridge: Any,
    *,
    symbol: str,
    preconditions: tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None = None,
    cleanup_scope: MutableMapping[str, Any] | None = None,
) -> int:
    """Exercise the broker path after an already-connected bridge passed safety checks."""
    from tsp_v2.adapters.execution_adapter import MT5ExecutionAdapter
    from tsp_v2.enums import Direction, SignalFamily
    from tsp_v2.models import ExecutionIntent

    account, contract, tick = preconditions or validate_demo_order_path_preconditions(bridge, symbol=symbol)
    bid = float(tick["bid"])
    ask = float(tick["ask"])
    volume = float(contract.get("volume_min", 0.0) or 0.0)
    if volume <= 0.0:
        raise DemoOrderPathSafetyError("Contract volume_min must be positive")
    initial_stop = _initial_stop_loss(ask=ask, contract=contract)
    now_utc = datetime.now(tz=timezone.utc)
    intent = ExecutionIntent(
        setup_id=f"demo-order-path-{uuid4().hex[:12]}",
        signal_family=SignalFamily.MICRO_IMPULSE,
        symbol=symbol.strip().upper(),
        direction=Direction.LONG,
        decision_price=(bid + ask) / 2.0,
        sized_volume=volume,
        stop_loss_price=initial_stop,
        submission_uuid=uuid4().hex,
        cycle_time_utc=now_utc,
    )
    if cleanup_scope is not None:
        cleanup_scope["setup_id"] = intent.setup_id
    print(
        "ORDER_PATH_PRECHECK | "
        f"login={account.get('login')} | symbol={intent.symbol} | volume={volume} | "
        f"stop_level_points={contract.get('trade_stops_level')} | spread={ask - bid} | "
        f"filling_mode={contract.get('filling_mode')} | trade_exemode={contract.get('trade_exemode')} | "
        f"initial_sl={initial_stop}"
    )
    adapter = MT5ExecutionAdapter(bridge=bridge)
    started = datetime.now(tz=timezone.utc)
    execution = adapter.execute(intent, at_utc=now_utc)
    _event(
        "market_buy",
        started=started,
        result=execution,
        fill_price=execution.response.get("price"),
        requested_price=intent.decision_price,
    )
    if execution.ticket is None:
        raise RuntimeError("Demo market order did not return a broker ticket")
    position_ticket = int(execution.ticket)
    if cleanup_scope is not None:
        cleanup_scope["position_tickets"].add(position_ticket)
    if not execution.filled:
        raise RuntimeError("Demo market order did not fill; SLTP verification cannot continue")
    position = _find_position(bridge, position_ticket)
    actual_initial_sl = float(position.get("sl", 0.0) or 0.0)
    if abs(actual_initial_sl - initial_stop) > max(float(contract.get("point", 0.0) or 0.0) / 2.0, 1e-10):
        raise RuntimeError(f"Broker position SL mismatch: expected {initial_stop}, got {actual_initial_sl}")

    current_tick = dict(bridge.latest_tick(intent.symbol))
    protective_stop = _protective_stop_loss(
        bid=float(current_tick["bid"]),
        initial_stop=initial_stop,
        contract=contract,
    )
    started = datetime.now(tz=timezone.utc)
    modified = bridge.modify_position_sltp(position_ticket, symbol=intent.symbol, sl=protective_stop)
    _event("modify_sltp", started=started, result=modified, requested_sl=protective_stop)
    if not modified.ok:
        raise RuntimeError(f"Demo SLTP modification failed: {modified.message}")
    position = _find_position(bridge, position_ticket)
    actual_protective_sl = float(position.get("sl", 0.0) or 0.0)
    if abs(actual_protective_sl - protective_stop) > max(float(contract.get("point", 0.0) or 0.0) / 2.0, 1e-10):
        raise RuntimeError(f"Broker modified SL mismatch: expected {protective_stop}, got {actual_protective_sl}")

    started = datetime.now(tz=timezone.utc)
    closed = bridge.close_position(position_ticket, comment="DEMO_ORDER_PATH_CHECK_CLOSE")
    _event("close_position", started=started, result=closed)
    if not closed.ok or bridge.query_positions(ticket=position_ticket):
        raise RuntimeError("Demo position did not close cleanly")
    print("ORDER_PATH_OK | account_flat=True")
    return 0


def _run_order_path_with_bridge(bridge: Any, *, symbol: str) -> int:
    """Connect, exercise, and always clean up one symbol through a concrete bridge."""
    canonical_symbol = symbol.strip().upper()
    cleanup_scope: dict[str, Any] = {"preconditions_passed": False, "setup_id": None, "position_tickets": set()}
    try:
        status = bridge.connect()
        if not status.ok:
            raise DemoOrderPathSafetyError(f"MT5 connection failed: {status.message}")
        preconditions = validate_demo_order_path_preconditions(bridge, symbol=canonical_symbol)
        cleanup_scope["preconditions_passed"] = True
        return _run_connected_order_path_check(
            bridge,
            symbol=canonical_symbol,
            preconditions=preconditions,
            cleanup_scope=cleanup_scope,
        )
    finally:
        if cleanup_scope["preconditions_passed"]:
            _cleanup_test_run_positions(
                bridge,
                symbol=canonical_symbol,
                setup_id=cleanup_scope["setup_id"],
                position_tickets=cleanup_scope["position_tickets"],
            )
        try:
            bridge.disconnect()
        except Exception as exc:
            print(f"ORDER_PATH_CRITICAL | disconnect_failed={type(exc).__name__}: {exc}")


def run_order_path_check(*, config_path: Path, env_path: Path, symbol: str) -> int:
    """Run the guarded demo sequence. Call only after explicit operator approval."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from tsp_v2.adapters.mt5_bridge import MT5Bridge
    from tsp_v2.config import load_config

    config = load_config(config_path=config_path, env_path=env_path)
    bridge = MT5Bridge(
        terminal_path=config.secrets.mt5_terminal_path,
        login=config.secrets.mt5_login,
        password=config.secrets.mt5_password,
        server=config.secrets.mt5_server,
    )
    return _run_order_path_with_bridge(bridge, symbol=symbol)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.i_understand_this_sends_demo_orders:
        print("ORDER_PATH_BLOCKED | explicit --i-understand-this-sends-demo-orders acknowledgement is required")
        return 2
    try:
        return run_order_path_check(config_path=args.config, env_path=args.env_file, symbol=args.symbol)
    except DemoOrderPathSafetyError as exc:
        print(f"ORDER_PATH_BLOCKED | reason={exc}")
        return 2
    except Exception as exc:
        print(f"ORDER_PATH_FAILED | error={type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
