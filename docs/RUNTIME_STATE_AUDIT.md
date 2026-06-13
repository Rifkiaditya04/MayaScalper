# Runtime State Audit

Scope: `tsp_v2` runtime state, persistence, and live cycle reporting.

## Verdict

`last_processed_m5_close_utc` is **UNUSED** in the current runtime implementation.

It exists in the model, but no runtime path reads it, writes it, or enforces a gate with it.

## RuntimeState

Defined in `tsp_v2/models.py`.

Fields:
- `mode`
- `governor_state`
- `started_at_utc`
- `last_cycle_time_utc`
- `last_processed_m5_close_utc`
- `kill_reason`
- `health`

Usage:
- `last_cycle_time_utc` is **USED**
  - updated in `tsp_v2/live_runtime.py` after each cycle
  - persisted to runtime state as `runtime.last_cycle_time_utc`
- `last_processed_m5_close_utc` is **UNUSED**
  - defined in the model
  - not referenced by runtime, deployment, persistence, or recovery code

## LiveCycleReport

Defined in `tsp_v2/live_runtime.py`.

Fields:
- `cycle_time_utc`
- `broker_time_utc`
- `governor_state`
- `governor_reason`
- `selected_symbols`
- `signal_count`
- `execution_count`
- `execution_results`
- `reconciliation_ready`
- `market_health`
- `feed_health`
- `pace_state`

Usage:
- `cycle_time_utc` and `broker_time_utc` are **USED**
  - stored in runtime state
  - emitted in telemetry
- the other fields are **USED**
  - consumed by governance, execution, persistence, and telemetry

## Runtime Persistence

`SQLiteRuntimeStore` stores runtime state as a generic key/value table:
- table: `runtime_state`
- methods:
  - `store_runtime_state(state: Mapping[str, Any])`
  - `load_runtime_state()`

Current runtime keys written by `LiveRuntimeRunner._store_runtime_state()`:
- `runtime.mode`
- `runtime.governor_state`
- `runtime.last_cycle_time_utc`
- `runtime.last_broker_time_utc`
- `runtime.selected_symbols`
- `runtime.execution_count`
- `runtime.signal_count`
- `runtime.reconciliation_ready`
- `runtime.market_health`
- `runtime.feed_health`
- `runtime.pace_state`

Current deployment lifecycle keys written by `DeploymentManager`:
- `deployment.version`
- `deployment.schema_version`
- `deployment.config_fingerprint`
- `deployment.startup_time_utc`
- `deployment.mode`
- `deployment.profile`
- `deployment.dry_run`
- `deployment.shutdown_reason`
- `deployment.shutdown_time_utc`

Other persisted time fields in runtime DB:
- `execution_registry.cycle_time_utc`
- `execution_registry.expires_at_utc`
- `execution_events.event_time_utc`
- `positions.open_time_utc`
- `lifecycle_state.opened_at_utc`
- `lifecycle_state.thesis_expiry_utc`
- `governor_state.updated_at_utc`
- `account_state.updated_at_utc`
- `recovery_events.event_time_utc`
- `telemetry_index.event_time_utc`

## Operational Conclusion

The runtime currently persists cycle timestamps and broker timestamps, but it does **not** persist or consume a closed-M5 gate state.

So, at present:
- `last_processed_m5_close_utc` exists as a model field only
- it is not part of the runtime contract
- it cannot currently enforce "one closed M5 = one cycle"

