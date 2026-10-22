"""Operator launcher for TSP V2 deployment runtime."""

from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .config import load_config
from .config_schema import ConfigValidationError
from .deployment import DeploymentRuntime, run_preflight, run_shutdown


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(description="TSP V2 deployment launcher")
    parser.add_argument(
        "command",
        choices=("preflight", "start", "shutdown"),
        help="Deployment command to execute.",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the governed profile YAML file.",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional .env file to load before config materialization.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip live broker checks and only validate the deployment chain.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="For preflight only, perform read-only live broker checks.",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=1,
        help="Maximum live cycles to run when start is not a dry run.",
    )
    parser.add_argument(
        "--reason",
        default="normal_shutdown",
        help="Shutdown reason for the shutdown command.",
    )
    parser.add_argument(
        "--emergency",
        action="store_true",
        help="Mark shutdown as emergency.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(
        config_path=Path(args.config),
        env_path=Path(args.env_file) if args.env_file else None,
    )
    current_time_utc = datetime.now(tz=timezone.utc)

    if args.live and args.command != "preflight":
        parser.error("--live is only supported by the preflight command")
    if args.live and args.dry_run:
        parser.error("--live cannot be combined with --dry-run")

    try:
        if args.command == "preflight":
            if args.live:
                return _run_live_preflight(config, current_time_utc=current_time_utc)
            report = run_preflight(
                config,
                dry_run=True,
                current_time_utc=current_time_utc,
            )
            _print_preflight(report)
            return 0 if report.ok else 1
        if args.command == "start":
            runtime = DeploymentRuntime(config)
            report = runtime.start(
                dry_run=args.dry_run,
                current_time_utc=current_time_utc,
                max_cycles=args.max_cycles if not args.dry_run else 1,
            )
            _print_startup(report)
            runtime.shutdown(reason="launcher_exit")
            return 0
        if args.command == "shutdown":
            report = run_shutdown(
                config,
                reason=args.reason,
                emergency=args.emergency,
                current_time_utc=current_time_utc,
            )
            _print_shutdown(report)
            return 0
    except ConfigValidationError as exc:
        print(f"DEPLOYMENT BLOCKED | reason={exc}")
        return 1

    parser.error(f"Unsupported command: {args.command}")
    return 2


def _print_preflight(report) -> None:
    lock_status = "unavailable"
    if report.lock_snapshot is not None:
        lock_status = "reclaimed" if report.lock_snapshot.reclaimed else "validated"
    print(
        "PRECHECK "
        f"{'OK' if report.ok else 'BLOCKED'} | "
        f"mode={report.mode} | profile={report.profile} | "
        f"lock={lock_status} | broker_ready={report.broker_ready} | "
        f"news_state={report.news_state} | reason={report.blocked_reason or 'none'}"
    )


def _run_live_preflight(config, *, current_time_utc: datetime) -> int:
    """Run broker checks only; this path deliberately has no order operations."""
    runtime = DeploymentRuntime(config)
    probe = runtime._build_live_probe()
    try:
        report = runtime.preflight(
            dry_run=False,
            probe=probe,
            current_time_utc=current_time_utc,
        )
        _print_live_account_summary(probe, report.blocked_reason)
        _print_preflight(report)
        return 0 if report.ok else 1
    finally:
        probe.close()


def _print_live_account_summary(probe: Any, blocked_reason: str) -> None:
    """Print non-secret account diagnostics for an operator's live preflight."""
    try:
        account = dict(probe.account_snapshot())
        positions = tuple(probe.live_positions())
        pending_orders = tuple(probe.bridge.query_orders())
    except Exception as exc:
        print(
            "LIVE ACCOUNT | "
            f"unavailable={type(exc).__name__} | blocked_reason={blocked_reason or 'none'}"
        )
        return

    trade_mode = account.get("trade_mode")
    positions_without_stop = sum(1 for position in positions if _position_has_no_stop(position))
    print(
        "LIVE ACCOUNT | "
        f"login={account.get('login', 'unknown')} | "
        f"server={account.get('server', 'unknown')} | "
        f"trade_mode={trade_mode!r} ({_trade_mode_label(trade_mode)}) | "
        f"open_positions={len(positions)} | positions_without_sl={positions_without_stop} | "
        f"pending_orders={len(pending_orders)} | "
        f"blocked_reason={blocked_reason or 'none'}"
    )


def _trade_mode_label(trade_mode: Any) -> str:
    labels: Mapping[int, str] = {0: "demo", 1: "contest", 2: "real"}
    if isinstance(trade_mode, bool) or not isinstance(trade_mode, int):
        return "unknown"
    return labels.get(trade_mode, "unknown")


def _position_has_no_stop(position: Mapping[str, Any]) -> bool:
    try:
        return float(position.get("sl", 0.0)) <= 0.0
    except (TypeError, ValueError):
        return True


def _print_startup(report) -> None:
    print(
        "STARTUP OK | "
        f"mode={report.metadata.mode} | profile={report.metadata.profile} | "
        f"dry_run={report.metadata.dry_run} | "
        f"bootstrap_ready={report.bootstrap_report.ready_to_resume} | "
        f"live_cycles={report.live_report.cycles_completed if report.live_report is not None else 0}"
    )


def _print_shutdown(report) -> None:
    print(
        "SHUTDOWN OK | "
        f"reason={report.reason} | emergency={report.emergency} | "
        f"lock_released={report.lock_released}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
