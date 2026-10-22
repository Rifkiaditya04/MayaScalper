# WO-047c Forward Lifecycle Observation

## Purpose

This work adds observability only. It does not alter regime classification,
signals, portfolio selection, risk, order validation, broker execution, or
lifecycle stop decisions.

For every verified open lifecycle position, runtime now writes one
`runtime.lifecycle_price_sample` event per lifecycle pass. The event includes
the entry, initial/current stop, initial R distance, mid price, direction, and
whether the stop had already reached breakeven/trailing state. It is emitted
before lifecycle evaluation and before any SLTP modification attempt, so a
sample is retained even when an SLTP update later fails.

## Supervisor

Use the bounded PowerShell supervisor after manually confirming that MT5 is
logged into the intended demo/forward account and connected:

```powershell
.\tools\run_forward_observation.ps1 -ChunkCycles 50 -MaxChunks 100
```

It runs a live, read-only preflight once, then launches bounded runtime chunks. The
preflight verifies the connected account's demo trade mode and reports its login,
server, trade mode, open-position count, and pending-order count. It has no order
creation, modification, or closing path. Non-zero exits
are logged and retried after a delay. Five consecutive failures stop the
supervisor by default. Create `runtime/forward_demo/STOP` to request a clean
stop before the next chunk. The supervisor log is separate from runtime logs:
`logs/forward_demo/forward_observation_supervisor.log`.

Child stdout and stderr are retained in
`logs/forward_demo/forward_observation_child.log`, and mirrored into the
supervisor log. `MaxChunks` counts successful chunks only; failed starts retry
until the consecutive-failure limit is reached and then exit non-zero.

The supervisor captures the child process exit code immediately after the
native child returns, before formatting its captured output through a
PowerShell logging pipeline, and keeps failure state in a separate variable. Its local,
MT5-free regression harness is runnable with:

```powershell
powershell -ExecutionPolicy Bypass -File tools\tests\test_forward_observation_supervisor.ps1
```

The harness verifies an always-failing child, an always-successful child, and
a child that fails once before succeeding. It uses
`tools/tests/forward_observation_stub.cmd` and never starts MT5 or the bot.

The default profile is `deploy/v2/configs/forward_demo.yaml`. It has an
isolated database, lock, runtime, log, and report namespace. It enables
execution only after preflight confirms the MT5 account reports demo mode.
Missing, contest, or real account modes fail closed. Operators must still
confirm the account and open positions manually before starting the supervisor.

## Analysis

The analyzer opens the runtime database read-only and writes a Markdown report:

```powershell
.\.venv\Scripts\python.exe tools\analyze_lifecycle_observation.py
```

It reports MAE/MFE and final observed R in initial-R units, worst-MAE timing,
breakeven/trailing observation, lifecycle modification failures, and final
observed state for each `position_ticket`. A "recovery" means that a later
sample reaches non-negative R after the worst observed MAE; it is not realized
PnL. A final observed R of at most -0.9 is only a marker for investigation,
not proof that a broker stop-loss filled.

## Operational Boundary

`deploy/v2/configs/forward_live.yaml` remains `allow_live_execution: false`.
The supervisor does not bypass it; it uses the separate demo-only profile.
`forward_demo.yaml` requires a broker-reported demo account before allowing
execution. No operator confirmation can bypass a missing, contest, or real
trade mode while that guard is enabled.

The supervisor selects the repository's MT5-enabled interpreter at
`../pradita/Scripts/python.exe`. If that interpreter is absent, the operator
must pass `-PythonPath` explicitly; it does not silently fall back to an
unverified Python environment.
