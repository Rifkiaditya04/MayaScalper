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
supervisor by default. Create `runtime/forward_live/STOP` to request a clean
stop before the next chunk. The supervisor log is separate from runtime logs:
`logs/forward_live/forward_observation_supervisor.log`.

## Analysis

The analyzer opens the runtime database read-only and writes a Markdown report:

```powershell
.\.venv\Scripts\python.exe tools\analyze_lifecycle_observation.py
```

It reports MAE/MFE in initial-R units, worst-MAE timing, breakeven/trailing
observation, lifecycle modification failures, and final observed state for
each `position_ticket`. A "recovery" means that a later sample reaches
non-negative R after the worst observed MAE; it is not realized PnL.

## Operational Boundary

`deploy/v2/configs/forward_live.yaml` remains `allow_live_execution: false`.
The supervisor must not be used to bypass that control. With execution disabled,
the observation can record lifecycle telemetry for pre-existing demo positions
but cannot create new strategy trades. Any future decision to enable demo
execution requires a separate explicit safety gate.
