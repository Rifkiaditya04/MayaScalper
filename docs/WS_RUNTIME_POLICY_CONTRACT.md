# WS-RP2 Runtime Failure Policy Contract

## Purpose

This contract defines the steady-state runtime failure policy for `LiveRuntimeRunner.run()` and `LiveRuntimeRunner._run_cycle()`.

Its scope is the broker-connected live loop after startup synchronization has already completed.

This contract does **not** change runtime behavior. It documents the policy that the current implementation already follows.

## Policy Boundary

Inside the steady-state live loop, the runtime is **fail-fast** by default.

Only the closed-M5 gate skip is recoverable within the loop:

- if `_run_cycle()` returns `None`, the runtime continues to the next poll

Every other unhandled failure category listed below is treated as fatal to the runtime process.

## Failure Classification

### ConfigValidationError

Decision: `STOP RUNTIME`

Reason:

- This includes invalid market readiness, invalid snapshot state, invalid runtime invariants, or any other validation failure that escapes `_run_cycle()`.
- In the current implementation, these failures are treated as fatal and are emitted as `runtime_error`.

### MT5BridgeError

Decision: `STOP RUNTIME`

Reason:

- A bridge error means the broker-facing adapter could not complete its contract.
- In the current implementation, escaped bridge errors are classified as runtime fatal.

### Snapshot Failure

Decision: `STOP RUNTIME`

Reason:

- A failed snapshot means the runtime cannot safely establish a valid market view for that cycle.
- Snapshot failure is treated as a fatal runtime condition in the current implementation.

### Reconciliation Failure

Decision: `STOP RUNTIME`

Reason:

- Reconciliation is part of the authoritative broker-truth workflow.
- An unexpected reconciliation failure means the runtime cannot safely prove state consistency.

### Execution Failure

Decision: `STOP RUNTIME`

Reason:

- Execution failure is a broker-facing runtime fault, not a benign cycle skip.
- Any unexpected execution exception escaping the cycle is fatal to the runtime.

### Persistence Failure

Decision: `STOP RUNTIME`

Reason:

- Persisted runtime state is required for deterministic restart behavior.
- A persistence failure means the runtime cannot safely guarantee recovery semantics.

### Telemetry Failure

Decision: `STOP RUNTIME`

Reason:

- Telemetry is part of the auditable runtime contract.
- If telemetry emission itself fails in a way that escapes the cycle, the runtime stops rather than silently hiding the failure.

### Unexpected Exception

Decision: `STOP RUNTIME`

Reason:

- Any unexpected exception is treated as an unclassified runtime fault.
- The runtime fails loud rather than continuing in an unknown state.

## Recoverable Runtime Path

The only steady-state recoverable path is:

- closed-M5 gate returns `None`

That is not a failure. It is a normal skip path that sleeps until the next poll.

## Startup vs Runtime

This contract does **not** redefine startup synchronization.

Startup readiness retries are handled by deployment orchestration, not by the steady-state runtime policy defined here.

## Out of Scope

This contract does not change:

- `_run_cycle()` implementation
- `run()` exception handling
- startup synchronization
- snapshot validation thresholds
- market data retrieval
- MT5 bridge behavior
- reconciliation logic
- execution logic

