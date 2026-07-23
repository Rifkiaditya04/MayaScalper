# WO-028 - M1 Bridge Fetch Semantics Diagnostic

## Baseline

- `67deee1e4a34398ef34f7172fd5c3e50dae983b9`

## References

- `validation/V2-V03/logs/WO026_20260722T115323Z-forward-run.log`
- `validation/V2-V03/db/WO026_20260722T115323Z.sqlite3`
- `validation/V2-V03/logs/WO028_raw_probe_20260723T040051Z.log`
- `validation/V2-V03/logs/WO028_compare_20260723T040232Z.log`

## Source Verified

- `deploy/v2/configs/forward_live.yaml`
- `tsp_v2/deployment.py`
- `tsp_v2/live_runtime.py`
- `tsp_v2/adapters/market_adapter.py`
- `tsp_v2/adapters/mt5_bridge.py`
- `tsp_v2/snapshots.py`

## Verified Source Facts

- `DeploymentRuntime._primary_symbol()` returns the first allowlist entry.
- `MT5MarketAdapter.get_broker_time()` uses the primary symbol tick timestamp.
- `MT5Bridge.get_rates()` uses `copy_rates_from_pos(...)` and normalizes the returned sequence without re-sorting it.
- `_normalize_rates()` preserves input order and only derives `close_time_utc`.
- `build_market_snapshot()` derives `future_count` from `timestamp > cycle_utc`.
- `_closed_bars()` only filters by `close_time_utc <= cycle_time_utc`.

## WO-026 Evidence Reused

The blocking campaign evidence remains:

- `cycle_time_utc = 2026-07-22T11:53:20Z`
- `latest_tick_timestamp_utc = 2026-07-22T11:53:12Z`
- `m1_latest_closed_bar_close_time_utc = 2026-07-22T11:53:00Z`
- `m1_latest_raw_open_time_utc = 2026-07-22T12:20:00Z`
- `m1_latest_raw_close_time_utc = 2026-07-22T12:21:00Z`
- `broker_time_minus_latest_raw_bar_seconds = -1660.0`
- `closed_bar_count = 12`
- `future_count = 27`

## Read-Only Probe Summary

The direct MT5 probe showed that raw `copy_rates_from_pos()` results are monotonic and that the latest window returned by the terminal is what the bridge normalizes.

### Current live probe snapshot

| Symbol | TF | Method | Requested | Returned | First UTC | Last UTC | Monotonic | Gap anomaly | Tick UTC | Status |
| ------ | -- | ------ | --------: | -------: | --------- | -------- | --------- | ----------- | -------- | ------ |
| GBPUSD | M1 | pos | 40 | 40 | 2026-07-23T05:49:00Z | 2026-07-23T07:02:00Z | yes | yes | 2026-07-23T04:00:49Z | future-dated vs tick |
| GBPJPY | M1 | pos | 40 | 40 | 2026-07-23T06:03:00Z | 2026-07-23T06:48:00Z | yes | yes | 2026-07-23T04:00:49Z | future-dated vs tick |
| EURUSD | M1 | pos | 40 | 40 | 2026-07-23T05:25:00Z | 2026-07-23T06:48:00Z | yes | yes | 2026-07-23T04:00:40Z | future-dated vs tick |

### Explicit-time comparison on GBPJPY M1

| Method | Requested | Returned | First UTC | Last UTC | Status |
| ------ | --------: | -------: | --------- | -------- | ------ |
| `copy_rates_from(pos-aware)` | 40 | 40 | 2026-07-22T11:01:00Z | 2026-07-22T11:40:00Z | normal historical slice |
| `copy_rates_from(pos-naive)` | 40 | 40 | 2026-07-22T03:55:00Z | 2026-07-22T04:39:00Z | timezone-context shifted |
| `copy_rates_range(aware)` | window 11:40-12:05 | 26 | 2026-07-22T11:40:00Z | 2026-07-22T12:05:00Z | normal historical slice |
| `copy_rates_range(naive)` | window 11:40-12:05 | 19 | 2026-07-22T04:43:00Z | 2026-07-22T05:05:00Z | timezone-context shifted |

## Conclusion

Evidence localizes the anomaly to the MT5 data window consumed by the production bridge path, but the strongest classification is now:

- `M1_BRIDGE_FETCH_SEMANTICS`

Secondary taxonomy remains:

- `M1_SYMBOL_DATA_AVAILABILITY`

The key boundary statement is:

> The anomalous `GBPJPY M1` window is already present before Python normalization or `_closed_bars()` filtering, but the time-bounded MT5 probes show that explicit window selection returns a normal historical slice. That means the symptom is tied to the bridge fetch semantics / window selection path, not to M5 contract logic, UTC conversion, symbol aliasing, or re-sorting in Python.

What is still **not** proven:

- the precise upstream MT5 mechanism that makes the `copy_rates_from_pos()` window diverge from the explicit-time probes
- whether the terminal history cache, chart state, or position-based fetch semantics is the root upstream cause

## Closure

- No production code was changed in WO-028.
- M5 contract remains frozen.
- This note closes the read-only diagnostic branch and records the strongest supported classification for the observed `GBPJPY M1` anomaly.
