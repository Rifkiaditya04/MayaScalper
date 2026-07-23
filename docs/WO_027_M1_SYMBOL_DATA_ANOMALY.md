# WO-027 - M1 Symbol Data Anomaly Closure

## Baseline

- `e8fbf89ec1468fde7ab9240ef2dc91928fcc799e`

## References

- `validation/V2-V03/logs/WO026_20260722T115323Z-forward-run.log`
- `validation/V2-V03/db/WO026_20260722T115323Z.sqlite3`

## Source Verified

- `deploy/v2/configs/forward_live.yaml`
- `tsp_v2/deployment.py`
- `tsp_v2/live_runtime.py`
- `tsp_v2/adapters/market_adapter.py`
- `tsp_v2/adapters/mt5_bridge.py`
- `tsp_v2/snapshots.py`

## Verified Source Facts

- `DeploymentRuntime._primary_symbol()` returns the first allowlist entry.
- `MT5MarketAdapter.get_broker_time()` delegates to the primary symbol tick timestamp.
- `MT5Bridge.get_rates()` fetches `copy_rates_from_pos(...)`, retries on empty records, and normalizes the returned sequence without re-sorting it.
- `_normalize_rates()` preserves the input order and only derives UTC timestamps and `close_time_utc`.
- `build_market_snapshot()` computes `future_count` from `bar["timestamp"] > cycle_utc`.
- `_closed_bars()` filters only by `close_time_utc <= cycle_time_utc`.

## Campaign Matrix

| Symbol | TF | Result | Closed | Future | Status |
| ------ | -- | ------ | -----: | -----: | ------ |
| GBPUSD | M5 | normal | 70 | 0 | pass |
| GBPJPY | M5 | diagnostic only | 64 | 6 | not blocker |
| GBPJPY | M1 | insufficient | 12 | 27 | blocker |
| GBPUSD | M1 | not captured in WO-026 campaign diagnostics | - | - | not used for blocker classification |

## Key Facts

- `cycle_time_utc = 2026-07-22T11:53:20Z`
- `latest_tick_timestamp_utc = 2026-07-22T11:53:12Z`
- `m1_latest_closed_bar_close_time_utc = 2026-07-22T11:53:00Z`
- `m1_latest_raw_open_time_utc = 2026-07-22T12:20:00Z`
- `m1_latest_raw_close_time_utc = 2026-07-22T12:21:00Z`
- `broker_time_minus_latest_raw_bar_seconds = -1660.0`

## Interpretation

Evidence shows the anomaly is already present in the `GBPJPY M1` data window that reaches the runtime pipeline from the MT5 path. The inspected source does not show Python normalization, sorting, symbol aliasing, or the M5 contract creating that anomaly.

Primary classification:

- `GBPJPY_MARKET_DATA_TIMESTAMP_ANOMALY`

Secondary taxonomy:

- `M1_SYMBOL_DATA_AVAILABILITY`

Evidence is sufficient to localize the symptom to the upstream MT5/data window received for `GBPJPY M1`, but not sufficient to isolate the upstream mechanism that produced the future-dated bars.

## Closure

- M5 contract remains frozen.
- No production code changes are made in WO-027.
- This note closes the diagnostic evidence for the observed `GBPJPY M1` anomaly and defers upstream MT5/data-window root cause isolation to a separate probe workstream.
