# WO-036 Fixed M5 Window Pre-Validation

## Baseline

- `f5f930c3bddf5d4f3b0a19adba89eef787e224f9`

## Working State

- Patch applied in working tree, not yet committed.
- Runtime DB artifact remains dirty and is not part of the patch:
  - `runtime/forward_live/db/tsp_v2_runtime.sqlite3`
- Existing evidence note from WO-035 remains present:
  - `docs/WO_035_M5_WINDOW_DESIGN_REVIEW.md`

## Source Change

- `tsp_v2/snapshots.py`
  - `SnapshotBuildConfig.m5_bars` changed from `MIN_BARS_BREAKOUT_M5 + 1` to fixed `90`.
- Test updates:
  - `tsp_v2/tests/test_mt5_bridge.py`
  - `tsp_v2/tests/test_snapshots.py`
  - `tsp_v2/tests/test_deployment.py`

## Baseline Test Results

- `python -m unittest tsp_v2.tests.test_mt5_bridge` -> PASS
- `python -m unittest tsp_v2.tests.test_market_adapter` -> PASS
- `python -m unittest tsp_v2.tests.test_snapshots` -> PASS
- `python -m unittest tsp_v2.tests.test_deployment` -> PASS
- `git diff --check` -> PASS

## Read-Only Probe

- Probe cycle time: `2026-07-29T12:00:03Z`
- Fetch method used for historical snapshot path: `copy_rates_range()`
- M1 request count remained `40`
- M5 request count changed to fixed `90`
- No future-window anomaly was observed.

### M1 / M5 Summary

| Symbol | TF | Requested | Returned | Closed | Future | Monotonic | Ready |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| GBPUSD | M1 | 40 | 37 | 36 | 1 | true | yes |
| GBPUSD | M5 | 90 | 90 | 89 | 1 | true | yes |
| GBPJPY | M1 | 40 | 40 | 39 | 1 | true | yes |
| GBPJPY | M5 | 90 | 90 | 89 | 1 | true | yes |
| EURUSD | M1 | 40 | 40 | 39 | 1 | true | yes |
| EURUSD | M5 | 90 | 87 | 86 | 1 | true | yes |

### Snapshot Readiness Result

| Symbol | Payload Health | M1 Closed | M5 Closed | M5 Minimum | Result |
| --- | --- | ---: | ---: | ---: | --- |
| GBPUSD | YELLOW | 36 | 89 | 70 | PASS |
| GBPJPY | GREEN | 39 | 89 | 70 | PASS |
| EURUSD | YELLOW | 39 | 86 | 70 | PASS |

## Interpretation

- The fixed 90-bar M5 request resolves the previous `EURUSD M5 available=68 < minimum=70` shortfall.
- M5 availability is now above the frozen minimum of 70 for all three symbols in the read-only probe.
- The M1 adaptive path remains healthy and still satisfies its minimum of 34 closed bars.
- The remaining `future_count = 1` on the latest bar is normal current-bar behavior, not the future-dated window anomaly from WO-026.

## Decision

- Ready for WO-037 forward validation after commit and final Git verification.
- No forward campaign was run in WO-036 pre-validation.
