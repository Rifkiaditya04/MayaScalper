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

It runs preflight once, then launches bounded runtime chunks. Non-zero exits
are logged and retried after a delay. Five consecutive failures stop the
supervisor by default. Create `runtime/forward_demo/STOP` to request a clean
stop before the next chunk. The supervisor log is separate from runtime logs:
`logs/forward_demo/forward_observation_supervisor.log`.

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
