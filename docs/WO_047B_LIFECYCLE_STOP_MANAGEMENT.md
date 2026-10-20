# WO-047b Lifecycle Stop Management

## Scope

This work implements lifecycle management after WO-047a established a broker-side
initial stop loss. It does not alter entry selection, risk sizing, market-data
readiness, portfolio selection, or execution validation.

## Design

`evaluate_lifecycle()` is pure. At the configured `break_even_after_r` (default
`1.0R`) it moves the stop to entry. Once an already protected position reaches
`1.5R`, it trails by one initial R distance. Long stops use `max` and short stops
use `min`, so a stop can never be widened in the losing direction.

`SQLiteRuntimeStore` persists `LifecycleState` records in the existing
`lifecycle_state` table. Runtime creates a state only after a filled order can be
matched to one verified broker position by ticket plus symbol/direction and order
identity. A deal or order ticket is not assumed to be a position ticket. If this
cannot be resolved, the runtime emits `runtime.lifecycle_state_unavailable`; the
initial broker-side SL from WO-047a remains in force.

At the beginning of each runtime cycle, after reconciliation, runtime updates a
verified open position only when its calculated stop improves. A successful SLTP
request is persisted and emits `runtime.lifecycle_stop_updated`; failures emit
`runtime.lifecycle_modify_failed` and retain the prior persisted state. State is
removed only when a reconciliation-ready broker position set no longer contains
the ticket.

## Validation

Unit coverage verifies exact +1R breakeven, no trigger at 0.99R, +1.5R trailing,
non-widening long/short behavior, SQLite round-trip/update/delete, verified state
creation after a fill, and runtime SLTP modification through the bridge contract.

The +1R and +1.5R values are approved initial operational defaults, not
backtest-derived optimizations. They require forward/demo observation with the
broker-side stop active before any parameter tuning or WO-048 entry changes.
