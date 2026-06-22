# WS-M1 Contract Note

## Purpose

`M1 snapshot ready` means the runtime has enough valid M1 history to build a deterministic snapshot for the current cycle.

In the current implementation, that means:

- the M1 raw payload exists,
- it normalizes successfully,
- `_closed_bars()` can be applied against `cycle_time_utc`,
- and the resulting closed M1 set has at least `34` bars.

## Input Contract

Valid inputs are:

- a symbol that is allowed by the deployment config,
- a timezone-aware UTC `cycle_time_utc`,
- raw M1 rates returned by the provider for that symbol,
- and the same raw payload shape expected by `build_market_snapshot()`.

The contract does not require the input raw bars to be complete in any abstract market-data sense beyond what the current validator checks.

## Output Contract

M1 snapshot is considered valid when:

```text
len(_closed_bars(normalized_m1_rates, cycle_time_utc)) >= 34
```

and the raw payload is structurally valid enough for snapshot construction to continue.

If the closed set is below minimum, the snapshot is invalid and the runtime emits:

```text
closed_bars_insufficient
```

## Non-goals

This contract does not:

- decide how many raw bars should be requested from MT5,
- explain why future bars appear in raw windows,
- normalize provider ordering,
- repair missing bars,
- or change runtime orchestration.

## Determinism

For the same raw payload and the same `cycle_time_utc`, the closed-bar result must be deterministic.

If the payload and cycle time are unchanged, the validation result must not fluctuate.

## Out of Scope

This workstream does not change:

- `requested_bars`
- `minimum_closed_bar_count`
- `_closed_bars()`
- `build_market_snapshot()`
- `MT5Bridge`
- runtime policy
- startup synchronization
- execution
- reconciliation

