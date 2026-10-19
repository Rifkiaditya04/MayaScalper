# WO-045 MT5 History Acquisition Characterization

## 1. Baseline

- `HEAD`: `88d38665a907f77a1be47ac153c2d1c267646677`
- Workspace authority: the source files currently on disk in `D:\Maya\Scalper`
- Mode: documentation-only characterization
- No production patch was made for WO-045

## 2. Repository State

Observed before and after this work:

- `git rev-parse HEAD` -> `88d38665a907f77a1be47ac153c2d1c267646677`
- `git show --stat --oneline HEAD` -> `88d3866 Set fixed M5 history window to 90 bars`
- `git status --short` -> pre-existing dirty runtime/doc artifacts remain
- `git diff --check` -> clean apart from LF/CRLF warnings in pre-existing dirty files

Pre-existing dirty state carried into WO-045:

- `M runtime/forward_live/db/tsp_v2_runtime.sqlite3`
- `M tsp_v2/live_runtime.py`
- `M tsp_v2/portfolio.py`
- `M tsp_v2/tests/test_deployment.py`
- `M tsp_v2/tests/test_portfolio.py`
- `?? docs/WO_035_M5_WINDOW_DESIGN_REVIEW.md`
- `?? docs/WO_038_EXECUTION_LIFECYCLE_AUDIT.md`
- `?? docs/WO_039_PORTFOLIO_SELECTION_ELIGIBILITY_AUDIT.md`
- `?? docs/WO_040_PORTFOLIO_SELECTION_OBSERVABILITY_DESIGN.md`
- `?? docs/WO_041_PORTFOLIO_SELECTION_OBSERVABILITY_IMPLEMENTATION.md`
- `?? docs/WO_042_PORTFOLIO_SELECTION_OBSERVABILITY_FORWARD_VALIDATION.md`
- `?? docs/WO_043_H1_STARTUP_READINESS_ROOT_CAUSE_AUDIT.md`
- `?? docs/WO_044_STARTUP_READINESS_OPERATIONAL_VALIDATION.md`
- `?? runtime/forward_live/db/tsp_v2_runtime.sqlite3-shm`
- `?? runtime/forward_live/db/tsp_v2_runtime.sqlite3-wal`
- `?? runtime/forward_live/locks/`

WO-045 itself did not modify production code, test code, or runtime artifacts.

## 3. Historical Discovery

I searched `docs/`, `validation/`, and `runtime/` for:

- `history`
- `startup`
- `market_data_readiness`
- `payload_rejected`
- `snapshot_ready`
- `startup_sync`
- `copy_rates`
- `copy_rates_range`
- `copy_rates_from_pos`
- `history availability`
- `terminal sync`
- `warmup`
- `GBPUSD`
- `GBPJPY`
- `H1`
- `M15`
- `M5`

Most relevant prior documents:

| Document | Finding | Still Relevant? | Status |
| --- | --- | --- | --- |
| [`docs/WO_042_PORTFOLIO_SELECTION_OBSERVABILITY_FORWARD_VALIDATION.md`](./WO_042_PORTFOLIO_SELECTION_OBSERVABILITY_FORWARD_VALIDATION.md) | Exact startup block for WO-042: `GBPUSD H1 available=9 < minimum=34`, `stage=payload_rejected` | Yes | SAME ISSUE |
| [`docs/WO_043_H1_STARTUP_READINESS_ROOT_CAUSE_AUDIT.md`](./WO_043_H1_STARTUP_READINESS_ROOT_CAUSE_AUDIT.md) | Root-cause characterization of the H1 startup shortfall; concluded operational/history availability was most likely | Yes | SAME ISSUE |
| [`docs/WO_044_STARTUP_READINESS_OPERATIONAL_VALIDATION.md`](./WO_044_STARTUP_READINESS_OPERATIONAL_VALIDATION.md) | Operational rerun still failed startup, but the shortage became multi-timeframe (`M5`, `M15`, `H1`) | Yes | SAME ISSUE |
| [`docs/WO_035_M5_WINDOW_DESIGN_REVIEW.md`](./WO_035_M5_WINDOW_DESIGN_REVIEW.md) | M5 history-window design discussion; not startup-specific | Only indirectly | RELATED |
| [`docs/WO_038_EXECUTION_LIFECYCLE_AUDIT.md`](./WO_038_EXECUTION_LIFECYCLE_AUDIT.md) | Runtime reached execution lifecycle in a later campaign; not about startup acquisition | Limited | HISTORICAL ONLY |
| [`docs/WO_039_PORTFOLIO_SELECTION_ELIGIBILITY_AUDIT.md`](./WO_039_PORTFOLIO_SELECTION_ELIGIBILITY_AUDIT.md) | Portfolio selection observability gap; unrelated to startup history | No | NOT RELEVANT |
| [`docs/WO_040_PORTFOLIO_SELECTION_OBSERVABILITY_DESIGN.md`](./WO_040_PORTFOLIO_SELECTION_OBSERVABILITY_DESIGN.md) | Observability design for selection telemetry | No | NOT RELEVANT |
| [`docs/WO_041_PORTFOLIO_SELECTION_OBSERVABILITY_IMPLEMENTATION.md`](./WO_041_PORTFOLIO_SELECTION_OBSERVABILITY_IMPLEMENTATION.md) | Selection telemetry implementation | No | NOT RELEVANT |
| [`docs/WS_M1_AUDIT.md`](./WS_M1_AUDIT.md) | Recurrent history-gap pattern on M1 | Related pattern only | RELATED |
| [`docs/WS_M1_CHARACTERIZATION.md`](./WS_M1_CHARACTERIZATION.md) | Raw history availability can differ across symbols/timeframes in the same runtime | Related pattern only | RELATED |
| [`docs/WS_RUNTIME_POLICY_AUDIT.md`](./WS_RUNTIME_POLICY_AUDIT.md) | Startup and runtime are fail-fast; useful context for why readiness failure stops the pipeline | Relevant behavior, not root cause | RELATED |

Conclusion from discovery:

- This issue class has been seen before in WO-042/043/044.
- I did not find evidence of a prior code defect isolated specifically as an MT5 history-acquisition bug for the startup blocker.
- The strongest prior evidence already points to startup history availability / sync timing rather than a selection, risk, or execution problem.

## 4. Workspace Source Audit

Audited source in the current workspace:

- [`tsp_v2/deployment.py`](../tsp_v2/deployment.py)
- [`tsp_v2/snapshots.py`](../tsp_v2/snapshots.py)
- [`tsp_v2/market_data.py`](../tsp_v2/market_data.py)
- [`tsp_v2/adapters/market_adapter.py`](../tsp_v2/adapters/market_adapter.py)
- [`tsp_v2/adapters/mt5_bridge.py`](../tsp_v2/adapters/mt5_bridge.py)
- [`tsp_v2/persistence.py`](../tsp_v2/persistence.py)
- [`tsp_v2/tests/test_mt5_bridge.py`](../tsp_v2/tests/test_mt5_bridge.py)
- [`tsp_v2/tests/test_deployment.py`](../tsp_v2/tests/test_deployment.py)
- [`tsp_v2/tests/test_snapshots.py`](../tsp_v2/tests/test_snapshots.py)

Key source facts:

- `DeploymentRuntime.start()` calls `_wait_for_startup_snapshot_readiness()` before `runner_start`. See [`deployment.py:397-461`](../tsp_v2/deployment.py:397).
- `_wait_for_startup_snapshot_readiness()` obtains broker time from `market_adapter.get_broker_time()` and then calls `build_market_snapshot(..., cycle_time_utc=broker_time)` for the primary symbol. See [`deployment.py:683-797`](../tsp_v2/deployment.py:683).
- `build_market_snapshot()` fetches raw bars for `M1`, `M5`, `M15`, and `H1`, then evaluates raw availability with `evaluate_payload_health()` before `_closed_bars()`. See [`snapshots.py:197-230`](../tsp_v2/snapshots.py:197), [`snapshots.py:239-330`](../tsp_v2/snapshots.py:239), [`snapshots.py:403-462`](../tsp_v2/snapshots.py:403).
- `evaluate_payload_health()` is fail-loud on `available < minimum`; it does not wait for closed-bar filtering. See [`snapshots.py:403-462`](../tsp_v2/snapshots.py:403).
- `MarketAdapter.get_rates()` is a thin passthrough that preserves `cycle_time_utc`. See [`market_adapter.py:52-72`](../tsp_v2/adapters/market_adapter.py:52).
- `MT5Bridge.get_rates()` uses `copy_rates_range()` when `cycle_time_utc` is provided, otherwise `copy_rates_from_pos()`. See [`mt5_bridge.py:791-916`](../tsp_v2/adapters/mt5_bridge.py:791).
- For non-M1 timeframes, the bridge uses a single fixed window of `max(count + 2, 40)` bars. For M1, it uses adaptive expansion with initial lookback 42 minutes, step 24 minutes, and max 120 minutes. See [`mt5_bridge.py:997-1080`](../tsp_v2/adapters/mt5_bridge.py:997).
- `store_telemetry_index()` persists telemetry as `topic + payload_json`. See [`persistence.py:372-378`](../tsp_v2/persistence.py:372).
- `get_latest_tick()` is a separate path and is not the startup availability bottleneck. See [`mt5_bridge.py:484-659`](../tsp_v2/adapters/mt5_bridge.py:484).

## 5. Startup Call Graph

```text
Deployment.start()
  -> _wait_for_startup_snapshot_readiness()
    -> market_adapter.get_broker_time()
      -> bridge.get_latest_tick(primary_symbol)
    -> build_market_snapshot(primary_symbol, cycle_time_utc=broker_time)
      -> provider.get_rates("M1", 40, cycle_time_utc=broker_time)
      -> provider.get_rates("M5", 90, cycle_time_utc=broker_time)
      -> provider.get_rates("M15", 40, cycle_time_utc=broker_time)
      -> provider.get_rates("H1", 40, cycle_time_utc=broker_time)
      -> evaluate_payload_health()
      -> either payload_rejected or snapshot_ready
    -> if payload rejected, startup_sync_failed and stop before runner_start
```

Important consequence:

- startup readiness is decided before runtime entry
- selection, risk, and execution do not run until readiness passes
- no adjustment to selection telemetry can change this blocker

## 6. MT5 Acquisition Flow

### API selection

- Legacy path without `cycle_time_utc`: `copy_rates_from_pos()`
- Explicit startup/snapshot path with `cycle_time_utc`: `copy_rates_range()`

### Parameter shape

For explicit startup acquisition:

- `symbol`
- `timeframe`
- `count`
- `cycle_time_utc`

For the explicit range window:

- `requested_end_utc = cycle_time_utc`
- `requested_start_utc = cycle_time_utc - timedelta(minutes=window_minutes)`
- non-M1 window: `window_minutes = timeframe_minutes * max(count + 2, 40)`
- M1 adaptive window: initial 42 minutes, step 24 minutes, max 120 minutes

### Retry behavior

- Each fetch window path retries up to 20 times with 0.5 second sleeps between empty responses.
- If an IPC-loss condition is detected, the bridge attempts one IPC recovery and then retries the same fetch path.
- There is no silent fallback from explicit range fetch back to `copy_rates_from_pos()`.

### What is and is not assumed

Supported by source:

- broker time is authoritative and normalized to UTC
- `cycle_time_utc` is the end boundary for history fetches
- raw availability is checked before closed-bar filtering

Not supported by source:

- automatic symbol chart opening
- explicit terminal warm-up
- a retry loop that waits for history to materialize across all timeframes at startup

## 7. Campaign Comparison (WO-042 vs WO-044)

| Campaign | M1 | M5 | M15 | H1 | Interpretation |
| --- | ---: | ---: | ---: | ---: | --- |
| WO-042 | 40 | 90 | 34 | 9 | H1 isolated as the failing timeframe in that startup window |
| WO-044 | 40 | 37 | 13 | 4 | Multi-timeframe shortage on the startup window; M1 remained healthy |

Key artifact facts:

- WO-042 readiness payload: `stage=payload_rejected`, `payload_health=RED`, `H1.available=9`, `M15.available=34`, `M5.available=90`
- WO-044 readiness payload: `stage=payload_rejected`, `payload_health=RED`, `H1.available=4`, `M15.available=13`, `M5.available=37`
- In both campaigns, startup stopped before `runner_start`
- In both campaigns, `runtime_cycle` and `runtime.portfolio_selection` were not reached

Interpretation:

- The startup-blocking condition is not stable in one single timeframe pattern.
- The recurring pattern is insufficient history availability at startup for the higher timeframes.
- M1 staying full in WO-044 is consistent with the existing adaptive M1 path being healthier than the higher timeframe fixed-window acquisition at that moment.

## 8. Acquisition Assumption Audit

| Assumption | Status | Evidence |
| --- | --- | --- |
| Terminal is already logged in | NOT OBSERVABLE | Source expects a live broker connection, but there is no explicit login readiness proof in the startup snapshot path. |
| Symbol is visible | NOT SUPPORTED | `query_symbol_contract()` and `get_latest_tick()` require usable market access, but there is no startup chart/visibility enforcement in the source. |
| History is already available | NOT SUPPORTED | The readiness gate fails when raw bars are below minimum, which shows the code does not guarantee that history already exists. |
| History is automatically downloaded in time | NOT SUPPORTED | Startup has no separate warm-up mechanism for higher timeframes beyond the readiness loop. |
| Chart has been opened | NOT SUPPORTED | No source path opens charts; this is operational, not coded. |
| Terminal has been idle long enough to fill history | NOT SUPPORTED | The code does not model this; it is an operational requirement only if the terminal needs time to sync. |
| Broker time is authoritative | SUPPORTED | The startup gate uses `market_adapter.get_broker_time()` and passes it into snapshot building. |
| `cycle_time_utc` is the end boundary | SUPPORTED | The bridge uses `requested_end_utc = cycle_time_utc` for explicit range fetches. |

## 9. Candidate Root Cause Matrix

| Candidate | Status | Evidence |
| --- | --- | --- |
| Terminal has not downloaded history yet | PARTIALLY SUPPORTED | Startup readiness sees raw H1/M15/M5 shortages in WO-044; later campaigns can show healthy payloads. |
| Symbol has not synced yet | PARTIALLY SUPPORTED | The source does not enforce symbol/chart sync, and the observed shortages are conditional. |
| Startup occurred too early | SUPPORTED | The failure happens at startup and later evidence shows the same code path can read healthy history under other conditions. |
| `copy_rates_range()` limitation | NOT SUPPORTED | The bridge and tests show the range path is functional; the blocker is raw availability at startup. |
| Broker history not yet available | PARTIALLY SUPPORTED | Raw availability is below minimum in the blocking campaigns. |
| Warm-up not finished | SUPPORTED | Best-fit explanation for the conditional shortages across higher timeframes. |
| Snapshot builder bug | NOT SUPPORTED | The builder behaves as designed: it rejects deficient payloads before runtime. |
| Market adapter bug | NOT SUPPORTED | The adapter is a passthrough for `cycle_time_utc` and does not rewrite the window. |
| MT5 bridge bug | NOT SUPPORTED | The same bridge implementation later returns healthy history in other runs. |
| Deployment bug | NOT SUPPORTED | The deployer is correctly fail-loud on readiness rejection. |
| Weekend/session boundary | NOT OBSERVABLE | Plausible, but not proven by current artifacts. |
| Broker time alignment | NOT OBSERVABLE | No direct evidence that alignment alone caused the shortages. |

## 10. Characterization Probe

There is no dedicated reusable live-probe utility in the workspace that I could use to repeatedly query MT5 history outside the existing campaign flow.

Observed characterization therefore comes from:

- the campaign artifacts from WO-042 and WO-044
- the source paths above
- the existing mock-based tests

So for this WO:

- **Not observable with existing workspace tooling** as a separate live-probe utility
- read-only characterization is still available from the campaign logs/DB that already exist

## 11. Operational vs Code Assessment

Assessment:

- Primary classification: **Operational procedure**
- Secondary classification: **startup history warm-up / synchronization timing**
- Code defect classification: **not supported by current evidence**
- Evidence completeness: **sufficient to avoid a code patch for now, not sufficient to isolate a bridge defect**

Why operational is the best fit:

- The startup gate is designed to fail when raw availability is below minimum.
- The failures occur before runtime entry and before any selection/risk/execution path.
- The same code path later succeeds in other historical startup rows, which argues against a permanent code regression.
- The source does not include an explicit terminal warm-up strategy for higher timeframes.

What still is not proven:

- whether the missing history is caused by MT5 terminal sync lag, broker-side history availability, weekend/session boundary, or timing of startup relative to market state

## 12. Regression Boundary

WO-045 does **not** propose changes to:

- portfolio
- selection
- risk
- execution
- telemetry
- M5 contract
- snapshot semantics
- startup policy
- `_closed_bars()`
- MT5 bridge behavior

This is evidence-only characterization.

## 13. Decision / Recommendation

Decision:

- The startup history shortfall is best characterized as an **operational history-acquisition problem** at this stage.
- There is no current basis for a production patch.
- The strongest next step, if needed, is a controlled operational validation of history warm-up timing before startup.

Recommendation:

1. Keep startup readiness contracts unchanged.
2. If another campaign is run, warm up the terminal/history first and capture the resulting payloads.
3. Only open a code patch if a warmed terminal still fails the same way under the same source path.

Bottom line:

> WO-045 shows that history acquisition during startup is the bottleneck, but the current evidence does not prove a code defect. The safest next action is operational validation, not implementation.
