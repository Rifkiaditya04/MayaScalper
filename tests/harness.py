"""Profile-based testing harness for TSP V1."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
import importlib
import json
import sys
import unittest


TEST_MODULES: tuple[str, ...] = (
    "tests.test_tsp_backtest",
    "tests.test_tsp_deploy",
    "tests.test_tsp_harness",
    "tests.test_tsp_state",
    "tests.test_tsp_config",
    "tests.test_tsp_data_pipeline",
    "tests.test_tsp_regime",
    "tests.test_tsp_signals",
    "tests.test_tsp_risk",
    "tests.test_tsp_competition",
    "tests.test_tsp_execution",
    "tests.test_tsp_position_manager",
    "tests.test_tsp_bot",
    "tests.test_tsp_persistence",
)

PROFILE_MODULES: dict[str, tuple[str, ...]] = {
    "unit": (
        "tests.test_tsp_harness",
        "tests.test_tsp_state",
        "tests.test_tsp_config",
        "tests.test_tsp_data_pipeline",
        "tests.test_tsp_regime",
        "tests.test_tsp_signals",
        "tests.test_tsp_risk",
        "tests.test_tsp_competition",
        "tests.test_tsp_execution",
        "tests.test_tsp_position_manager",
        "tests.test_tsp_backtest",
        "tests.test_tsp_deploy",
    ),
    "smoke": (
        "tests.test_tsp_bot",
        "tests.test_tsp_persistence",
    ),
    "all": TEST_MODULES,
}


@dataclass(frozen=True, slots=True)
class HarnessResult:
    profile: str
    tests_run: int
    failures: int
    errors: int
    skipped: int
    successful: bool


def _load_suite(module_names: tuple[str, ...]) -> unittest.TestSuite:
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for module_name in module_names:
        module = importlib.import_module(module_name)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite


def build_suite(profile: str) -> unittest.TestSuite:
    if profile not in PROFILE_MODULES:
        raise ValueError(f"Unknown test profile: {profile}")
    return _load_suite(PROFILE_MODULES[profile])


def iter_test_ids(suite: unittest.TestSuite) -> list[str]:
    ids: list[str] = []
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            ids.extend(iter_test_ids(test))
        else:
            ids.append(test.id())
    return ids


def run_profile(
    profile: str,
    *,
    verbosity: int = 2,
    failfast: bool = False,
) -> HarnessResult:
    suite = build_suite(profile)
    runner = unittest.TextTestRunner(verbosity=verbosity, failfast=failfast)
    result = runner.run(suite)
    return HarnessResult(
        profile=profile,
        tests_run=result.testsRun,
        failures=len(result.failures),
        errors=len(result.errors),
        skipped=len(result.skipped),
        successful=result.wasSuccessful(),
    )


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(description="TSP V1 testing harness")
    parser.add_argument(
        "profile",
        nargs="?",
        default="all",
        choices=tuple(PROFILE_MODULES),
        help="Test profile to execute",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List fully qualified test ids for the selected profile",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable summary JSON after execution",
    )
    parser.add_argument(
        "--failfast",
        action="store_true",
        help="Stop on the first failure",
    )
    parser.add_argument(
        "--verbosity",
        type=int,
        default=2,
        choices=(0, 1, 2),
        help="unittest runner verbosity",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return _run_from_args(args)


def _run_from_args(args: Namespace) -> int:
    suite = build_suite(args.profile)
    if args.list:
        for test_id in iter_test_ids(suite):
            print(test_id)
        return 0

    result = run_profile(
        args.profile,
        verbosity=args.verbosity,
        failfast=args.failfast,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "profile": result.profile,
                    "tests_run": result.tests_run,
                    "failures": result.failures,
                    "errors": result.errors,
                    "skipped": result.skipped,
                    "successful": result.successful,
                },
                sort_keys=True,
            )
        )
    return 0 if result.successful else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
