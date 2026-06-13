"""Operator launcher for TSP V2 deployment runtime."""

from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path

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

    try:
        if args.command == "preflight":
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
