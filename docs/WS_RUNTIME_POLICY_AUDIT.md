# RP1 Runtime Policy Audit

## Objective

Audit the current runtime policy for `DeploymentRuntime._run_cycle()` and determine whether a snapshot failure on one cycle is intended to:

- stop the whole runtime, or
- fail only that cycle and continue on the next poll.

This audit does not change runtime behavior.

## Current Behavior

The current implementation is fail-fast at the runtime loop level.

In `tsp_v2/live_runtime.py`, `LiveRuntimeRunner.run()` has a single outer `try/except` around the live loop. Any exception that escapes `_run_cycle()` is converted into `runtime_error:<ClassName>`, emitted as telemetry, and re-raised:

- `tsp_v2/live_runtime.py:172-179`
- `tsp_v2/live_runtime.py:206-239`

Inside `_run_cycle()`, there is no local exception boundary around the snapshot / signal / execution path. The function:

- reads broker time,
- checks market health,
- queries broker positions and account,
- runs reconciliation,
- builds snapshots for every allowed symbol,
- applies the closed-M5 gate,
- classifies regimes,
- evaluates signals,
- evaluates risk,
- validates execution intent,
- submits orders,
- persists the execution registry,
- reconciles again,
- stores runtime state,
- returns a `LiveCycleReport`.

If any of those steps raises, the exception escapes the cycle and terminates the runtime:

- `tsp_v2/live_runtime.py:261-437`

The only built-in non-fatal cycle outcome is the closed-M5 gate skip:

- `tsp_v2/live_runtime.py:288-291`

In that case `_run_cycle()` returns `None`, `run()` sleeps for the poll interval, and the loop continues:

- `tsp_v2/live_runtime.py:175-180`

At deployment entrypoint level, `DeploymentRuntime.start()` also treats any uncaught exception during startup or runtime as fatal, closes resources, emits `startup_failure`, and re-raises:

- `tsp_v2/deployment.py:413-519`

## Fatal vs Recoverable Classification

### Fatal in current implementation

These are currently fatal because they are not handled inside `_run_cycle()`:

- `ConfigValidationError` from an unhealthy market adapter
- `ConfigValidationError` from snapshot validation, including `closed_bars_insufficient`
- `MT5BridgeError` that escapes into `run()`
- any broker/account/reconciliation exception that escapes `_run_cycle()`
- any signal/risk/execution exception that escapes `_run_cycle()`
- any generic unexpected exception in the live cycle

Evidence:

- `tsp_v2/live_runtime.py:261-437`
- `tsp_v2/live_runtime.py:209-239`

### Recoverable in current implementation

Only the closed-M5 gate skip is currently recoverable within the runtime loop:

- `tsp_v2/live_runtime.py:288-291`

Startup synchronization also retries on `closed_bars_insufficient`, but that is a startup policy, not a live-cycle policy:

- `tsp_v2/deployment.py:699-741`

## Blueprint Comparison

The validation program documents the overall goal as proving the runtime is stable, recoverable, and safe before contest use. It also says to fail loud on unexplained positions, duplicate orders, or recovery mismatch:

- `docs/VALIDATION_PROGRAM_V2.md:8-25`
- `docs/VALIDATION_PROGRAM_V2.md:87-105`

The architecture mapping says restart should resume only after a healthy snapshot build and after reconciliation completes:

- `docs/IMPLEMENTATION_ARCHITECTURE_MAPPING_V2.md:612-631`

However, neither document explicitly states the per-cycle policy for a mid-run snapshot failure:

- should the runtime stop entirely, or
- should it skip the failed cycle and keep polling?

So the current behavior is clearly implemented, but the policy is not explicitly spelled out in the blueprint docs we audited.

## Root Cause Hypothesis

The current runtime policy is not a hidden bug in `_run_cycle()`. It is the result of implementation structure:

1. `_run_cycle()` does not catch its own validation / snapshot / execution exceptions.
2. `run()` treats any escaped exception as fatal.
3. `start()` treats any exception escaping `run()` as startup failure / shutdown-triggering.

This means a one-cycle `closed_bars_insufficient` during `forward_live` currently stops the daemon.

## Questions Still Unresolved

- Should `forward_live` continue after a failed snapshot cycle?
- Is a snapshot failure a fatal deployment condition, or just a skipped cycle?
- Should the runtime distinguish between startup readiness failures and steady-state cycle failures?

Those questions are not answered explicitly by the current docs.

## Verdict

**Evidence masih belum cukup untuk mengubah runtime policy.**

Current implementation is fail-fast and deterministic. The docs support a strict validation posture, but they do not explicitly define a recoverable live-cycle policy for snapshot failures. If we want the daemon to survive a bad cycle and try again later, that needs a separate contract note before any patch.

