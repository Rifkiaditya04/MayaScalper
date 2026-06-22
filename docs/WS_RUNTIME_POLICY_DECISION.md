# WS-RP2 Runtime Failure Policy Decision

## Decision

The runtime policy for the steady-state live loop is **fail-fast**.

That means any unhandled failure in the live cycle stops the runtime, except for the intentional closed-M5 gate skip path.

## Why Fail-Fast

### 1. Determinism

Fail-fast keeps the runtime state easy to reason about.

If a cycle fails, the process does not continue in a partially known state.

### 2. Broker Truth Safety

The runtime is broker-connected and reconciliation-driven.

If snapshot, reconciliation, execution, persistence, or bridge logic fails unexpectedly, continuing the loop can hide a real state mismatch.

### 3. Validation Consistency

The validation program already emphasizes fail-loud behavior for unexplained positions, duplicate orders, and recovery mismatches.

Fail-fast is aligned with that posture.

### 4. Recovery Clarity

A stopped runtime is explicit.

The operator or orchestrator can restart from a known boundary after the failure is diagnosed.

That is safer than silently skipping unknown failures.

## Consequences

### Contest and Forward-Test Impact

- Positive: failures are visible immediately.
- Negative: the daemon may stop on a single invalid cycle and require restart.

This is acceptable for the current validation and characterization phase because it prevents hidden divergence.

### Live Trading Impact

- Positive: the system avoids continuing after an unclassified fault.
- Negative: availability is lower than a self-healing daemon that keeps polling through errors.

If a more forgiving policy is later desired, it must be introduced explicitly and separately.

### Determinism Impact

- Positive: the runtime remains deterministic and auditable.
- Positive: no silent exception swallowing.
- Positive: no hidden partial cycles.

### Recovery Impact

- Positive: restart semantics stay simple.
- Positive: failed state is visible in telemetry and shutdown reason.
- Negative: the runtime does not attempt automatic cycle-level recovery for fatal faults.

## Why Not Recoverable-by-Default

A recoverable-by-default runtime would require explicit policy for:

- which exceptions are benign,
- which ones are transient,
- which ones are safe to retry,
- and how many retries are allowed before escalation.

That policy is not currently established in the blueprint docs.

So the safer and currently supported choice is to keep the live loop fail-fast.

## Blueprint Alignment

This decision matches the current implementation structure:

- `_run_cycle()` is the unit of live work.
- `run()` treats unhandled cycle exceptions as fatal.
- `start()` treats unhandled runtime exceptions as startup/shutdown failures.

The only explicit non-fatal cycle behavior is closed-M5 gate skipping.

## Out of Scope

This decision does not change:

- code paths
- telemetry schema
- snapshot thresholds
- startup synchronization
- M1 characterization
- execution state machine
- MT5 bridge behavior

