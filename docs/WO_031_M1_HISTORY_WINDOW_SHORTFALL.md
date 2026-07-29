# WO-031 - M1 History Window Shortfall

## Baseline

- `08e4a16c6b7ab694c6c2cda075209d4dfc096fb2`

## Source Verified

- [deploy/v2/configs/forward_live.yaml](/D:/Maya/Scalper/deploy/v2/configs/forward_live.yaml:4)
- [tsp_v2/config.py](/D:/Maya/Scalper/tsp_v2/config.py:315)
- [tsp_v2/deployment.py](/D:/Maya/Scalper/tsp_v2/deployment.py:646)
- [tsp_v2/market_data.py](/D:/Maya/Scalper/tsp_v2/market_data.py:17)
- [tsp_v2/adapters/market_adapter.py](/D:/Maya/Scalper/tsp_v2/adapters/market_adapter.py:52)
- [tsp_v2/adapters/mt5_bridge.py](/D:/Maya/Scalper/tsp_v2/adapters/mt5_bridge.py:787)
- [tsp_v2/snapshots.py](/D:/Maya/Scalper/tsp_v2/snapshots.py:52)

## Repository Facts

- `SnapshotBuildConfig.m1_bars` defaults to `40`.
- `build_market_snapshot()` passes `cycle_time_utc` into `provider.get_rates(...)` for historical fetches.
- `evaluate_payload_health()` uses `available = raw_counts.get(timeframe, 0)` and requires at least `34` closed bars for M1.
- `MT5Bridge.get_rates()` uses `copy_rates_range()` when `cycle_time_utc` is provided, and the explicit helper computes `lookback_bars = max(count + 2, 40)`.
- `_closed_bars()` remains unchanged and only filters by `close_time_utc <= cycle_time_utc`.

## WO-030 Artifact Recheck

- `validation/V2-V03/db/WO030_20260729T010751Z.sqlite3` shows startup rejection at `deployment.market_data_readiness` with `GBPUSD` `M1 available=14 < minimum=34`.
- `validation/V2-V03/logs/WO030_20260729T010751Z-forward-run.log` records the matching startup failure path and no runtime entry.

## Read-Only Comparative Probe

Probe cycle reference:

- `cycle_time_utc = 2026-07-29T02:06:11Z`

### Range probe results

| Symbol | 42 min closed | 60 min closed | 90 min closed | 120 min closed | Future count at 42 | Monotonic |
| ------ | ------------: | ------------: | ------------: | -------------: | -----------------: | --------- |
| GBPUSD | 19 | 25 | 35 | 47 | 0 | yes |
| GBPJPY | 30 | 42 | 64 | 85 | 0 | yes |
| EURUSD | 18 | 29 | 43 | 49 | 0 | yes |

### Additional MT5 time-based comparison

- `copy_rates_from(date_from=cycle_time-120m, count=120)` returned 120 monotonic bars for each symbol.
- This confirms the terminal can provide deeper historical data via time-based selection; the shortfall is specific to the current fixed 42-minute production window, not to a general inability to fetch M1 history.

## Conclusion

The 42-minute explicit time-bounded window introduced in WO-029 is not deterministically sufficient to satisfy the M1 readiness requirement of 34 closed bars under the observed market gaps.

The evidence points to:

- `WINDOW_DESIGN_PROBLEM` as the primary classification.
- Secondary context: `M1_HISTORY_WINDOW_SHORTFALL`.

This is **not** evidence for an M5 contract regression, and it is **not** evidence that Python closed-bar filtering is dropping bars.

## Decision

- Patch justification: **yes, for window sizing only**
- M5 contract change: **no**
- `_closed_bars()` change: **no**
- Production code changed in WO-031: **no**

The next design step, if approved, should be a narrow lookback-sizing adjustment or an adaptive historical window policy for M1 readiness, validated with the same read-only evidence pattern before any production rollout.
