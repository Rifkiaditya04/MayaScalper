# WO-035 M5 Window Design Review

## Baseline

- `f5f930c3bddf5d4f3b0a19adba89eef787e224f9`

## Scope Reviewed

- `tsp_v2/adapters/mt5_bridge.py`
- `tsp_v2/adapters/market_adapter.py`
- `tsp_v2/market_data.py`
- `tsp_v2/snapshots.py`
- `tsp_v2/live_runtime.py`
- `tsp_v2/regime.py`
- `tsp_v2/signals.py`
- `tsp_v2/risk.py`
- `tsp_v2/tests/test_mt5_bridge.py`
- `tsp_v2/tests/test_snapshots.py`
- `tsp_v2/tests/test_deployment.py`
- `docs/WO_034_FORWARD_VALIDATION.md` evidence set
- `validation/V2-V03/logs/WO034_20260729T112547Z-forward-run.log`
- `validation/V2-V03/db/WO034_20260729T112547Z.sqlite3`

## Source Map

### M5 fetch contract

- `SnapshotBuildConfig.m5_bars = MIN_BARS_BREAKOUT_M5 + 1` in [`tsp_v2/snapshots.py`](../tsp_v2/snapshots.py)
- `MIN_BARS_BREAKOUT_M5 = 70` in [`tsp_v2/snapshots.py`](../tsp_v2/snapshots.py)
- `build_market_snapshot()` requests `M5` via `provider.get_rates(..., cycle_time_utc=cycle_utc)` in [`tsp_v2/snapshots.py`](../tsp_v2/snapshots.py)
- `MT5Bridge.get_rates()` uses `copy_rates_range()` when `cycle_time_utc` is present; adaptive expansion is M1-only in [`tsp_v2/adapters/mt5_bridge.py`](../tsp_v2/adapters/mt5_bridge.py)

### Readiness and downstream lifecycle

- `evaluate_payload_health()` gates on raw `available` counts before `_closed_bars()` in [`tsp_v2/snapshots.py`](../tsp_v2/snapshots.py)
- `_closed_bars()` remains the frozen cutoff `close_time_utc <= cycle_time_utc` in [`tsp_v2/snapshots.py`](../tsp_v2/snapshots.py)
- `MarketSnapshot` is built from closed bars only, and the downstream decision stack consumes `snapshot.bars_m5` in [`tsp_v2/snapshots.py`](../tsp_v2/snapshots.py), [`tsp_v2/regime.py`](../tsp_v2/regime.py), [`tsp_v2/signals.py`](../tsp_v2/signals.py), and [`tsp_v2/risk.py`](../tsp_v2/risk.py)
- `live_runtime.py` emits `deployment.runner_start`, `runtime_started`, `runtime_recovered`, and `runtime_cycle` after startup readiness in [`tsp_v2/live_runtime.py`](../tsp_v2/live_runtime.py)

## WO-034 Evidence

### Campaign summary

- Baseline stayed on `f5f930c3bddf5d4f3b0a19adba89eef787e224f9`
- The forward run reached `deployment.runner_start`, `runtime_started`, and `runtime_recovered`
- M1 readiness was healthy for `GBPUSD` and `GBPJPY`
- Startup stopped when `EURUSD` failed M5 readiness with `available = 68 < minimum = 70`

### Read-only M5 probe at the same cycle time

Cycle reference: `2026-07-29T11:24:57Z`

| Symbol | Request | Returned | Closed | Future | Monotonic |
| --- | ---: | ---: | ---: | ---: | --- |
| GBPUSD | 71 | 71 | 70 | 1 | True |
| GBPUSD | 90 | 90 | 89 | 1 | True |
| GBPUSD | 120 | 118 | 117 | 1 | True |
| GBPJPY | 71 | 73 | 70 | 1 | True |
| GBPJPY | 90 | 92 | 89 | 1 | True |
| GBPJPY | 120 | 120 | 119 | 1 | True |
| EURUSD | 71 | 68 | 67 | 1 | True |
| EURUSD | 90 | 87 | 86 | 1 | True |
| EURUSD | 120 | 115 | 114 | 1 | True |

### Interpretation

- The shortfall is **not** a contract regression: M5 still closes at 70 and `_closed_bars()` is unchanged.
- The shortfall is **not** a future-dated anomaly: all probe windows were monotonic and the terminal close was the expected current-forming bar, not a shifted future window.
- The shortfall is **not** an absolute lack of M5 history: deeper windows immediately increased available raw bars for `EURUSD` from 68 to 87 and 115.

## Lifecycle Assessment

### M5 path in the current design

1. Production snapshot requests `M5` with a fixed `m5_bars` value.
2. Bridge fetch for M5 is explicit-time `copy_rates_range()` and non-adaptive.
3. Raw counts are checked in `evaluate_payload_health()` before the closed-bar gate.
4. Closed bars are then derived by `_closed_bars()` and the 70-bar contract is enforced.
5. Downstream regime, signal, and risk code consume `snapshot.bars_m5` rather than raw MT5 payloads.

### Effect of widening the M5 fetch window

- Widening the window changes the historical context that feeds ATR/EMA-based calculations, because `snapshot.bars_m5` is the closed-bar series passed downstream.
- The downstream code does not appear to use raw, unfiltered MT5 bars directly.
- Therefore, any M5 window change is behaviorally visible, even if the contract threshold remains frozen.

## Design Options

### Option A: Fixed larger M5 window

Candidate sizes observed to work in read-only probe:

- `90` raw bars
- `120` raw bars

Pros:

- Smallest source-surface change.
- Simple to reason about.
- Deterministic and easy to test.
- No new retry loop.

Cons:

- Still a hard-coded assumption.
- Less robust if a future campaign window becomes sparser than the observed one.
- Can shift indicator history by including more older bars.

### Option B: Adaptive M5 window

Pros:

- More robust against market-window shortfalls.
- Can bound startup retries and fail closed with diagnostics.
- Conceptually symmetric with the M1 solution already landed.

Cons:

- Larger code surface.
- More moving parts than needed if a fixed window is sufficient.
- More runtime latency/complexity than a fixed margin.
- Would require careful validation that M5-specific behavior remains frozen.

## Recommendation

- **Patch is justified** if the goal is to make M5 readiness resilient across campaign windows that exhibit shortfalls like the one observed for `EURUSD`.
- **Smallest design surface** is a **fixed larger M5 window** rather than an adaptive M5 loop.
- The fixed-window option is preferable as the next design step because the data already proves that a deeper window exposes enough M5 history, and the current failure is a window-sizing problem rather than a contract or cutoff defect.
- If a future review requires stronger robustness than a single fixed window, adaptive M5 can be revisited as a second-step design, but that is a larger change than the evidence currently requires.

## Risk Assessment

- M5 contract stays frozen at 70 closed bars.
- `_closed_bars()` stays frozen.
- `get_latest_tick()` stays frozen.
- The main regression risk is that extra historical M5 bars can slightly change EMA/ATR-based strategy inputs, because the downstream pipeline consumes `snapshot.bars_m5` as a closed-bar series.
- That risk is acceptable only if the next patch is validated with read-only probes before any forward campaign.

## Files Likely to Change If Approved

- `tsp_v2/snapshots.py`
- `tsp_v2/tests/test_snapshots.py`
- `tsp_v2/tests/test_deployment.py`
- `docs/WO_035_M5_WINDOW_DESIGN_REVIEW.md` or a follow-up patch note

## Final Decision

- **Patch justified:** yes, for M5 window sizing only.
- **Recommended design:** fixed larger M5 window as the smallest source change.
- **No evidence** was found for a M5 contract defect, a `_closed_bars()` defect, or a future-window regression.
- **No production code was changed** in WO-035.
