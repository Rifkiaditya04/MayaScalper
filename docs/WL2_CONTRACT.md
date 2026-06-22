# WL2 Contract Note - `_process_is_alive()`

## Purpose

`_process_is_alive(pid)` is a platform-aware liveness probe used only to decide whether an existing deployment lock is still owned by a live process.

It is not:

- a PID validator
- a process health check
- a trading/runtime helper
- a filesystem parser

## Input Contract

Input:

```text
pid: int
```

Requirements:

- accepts an integer PID
- does not parse the lock file
- does not read or write filesystem state
- has no side effects

## Output Contract

Returns only:

```python
True
```

Meaning:

> there is sufficient evidence that the process with this PID is still alive

or

```python
False
```

Meaning:

> there is not sufficient evidence that the process with this PID is still alive

This does not mean the process is certainly dead. It means the caller does not have enough evidence to keep the lock.

## Allowed Side Effects

None.

The helper must not:

- emit telemetry
- modify the lock
- reconnect MT5
- mutate runtime state

## Platform Requirement

The implementation may differ by platform:

- Windows
- Linux
- macOS

but the contract must remain identical.

The blueprint must not depend on a single OS API.

## Exception Contract

### Exceptions that may map to `False`

Exceptions that, by platform contract, mean the process cannot be confirmed alive.

Examples may include:

- process not found
- invalid PID
- stale handle
- already exited

These examples are implementation-specific, not blueprint-specific.

### Exceptions that remain fatal

Exceptions that indicate:

- implementation bug
- programming error
- environment corruption
- permission or platform behavior that cannot be interpreted safely

must be propagated.

Do not collapse all exceptions into `False`.

## Determinism

For the same PID under the same system conditions:

```text
_process_is_alive(pid)
```

must produce a deterministic decision.

The helper must not:

- retry internally
- sleep
- poll

Retry, if any, belongs to the caller.

## Performance

The probe must be:

- lightweight
- synchronous
- free of network I/O
- free of disk I/O

It is called on the deployment preflight path.

## Regression Requirements

Minimum required tests:

- current process PID returns `True`
- `pid <= 0` returns `False`
- stale lock can be reclaimed
- active lock cannot be reclaimed
- platform-specific exception that means "not alive" maps to `False`
- exception that indicates a bug remains propagated

## Out of Scope

WL2 does not change:

- startup synchronization
- closed-M5 gate
- snapshots
- MT5 bridge
- execution
- reconciliation
- telemetry contract, unless needed for reclaim testing

