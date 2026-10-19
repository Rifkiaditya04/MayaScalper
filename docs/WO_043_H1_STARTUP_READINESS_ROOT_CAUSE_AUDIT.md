# WO-043 H1 Startup Readiness Root Cause Audit

## 1. Baseline

- `HEAD`: `88d38665a907f77a1be47ac153c2d1c267646677`
- Workspace authority: the files currently on disk in `D:\Maya\Scalper`
- Mode: documentation-only
- No production patch was made for WO-043

## 2. Repository State

Observed at audit time:

- `git rev-parse HEAD` -> `88d38665a907f77a1be47ac153c2d1c267646677`
- `git diff --check` -> clean, with only LF/CRLF warnings in pre-existing dirty files
- `git status --short` -> pre-existing dirty runtime/doc artifacts remain

Pre-existing dirty state carried into WO-043:

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
- `?? runtime/forward_live/db/tsp_v2_runtime.sqlite3-shm`
- `?? runtime/forward_live/db/tsp_v2_runtime.sqlite3-wal`
- `?? runtime/forward_live/locks/`

WO-043 itself did not modify production code, test code, or runtime artifacts.

## 3. Historical Discovery

I searched `docs/` and `validation/` for:

- `H1`
- `startup readiness`
- `market_data_readiness`
- `payload_rejected`
- `available`
- `minimum`
- `requested`
- `GBPUSD`
- `history`
- `copy_rates`
- `rates_fetch`
- `snapshot`
- `STRICT_FAIL_LOUD`

The most relevant documents were:

| Document | Finding | Still Relevant? | Status |
| --- | --- | --- | --- |
| [`docs/WO_042_PORTFOLIO_SELECTION_OBSERVABILITY_FORWARD_VALIDATION.md`](./WO_042_PORTFOLIO_SELECTION_OBSERVABILITY_FORWARD_VALIDATION.md) | Exact H1 blocker for WO-042: `GBPUSD H1 available=9 < minimum=34`, `stage=payload_rejected` | Yes | SAME ISSUE |
| [`docs/WO_035_M5_WINDOW_DESIGN_REVIEW.md`](./WO_035_M5_WINDOW_DESIGN_REVIEW.md) | M5 window sizing and downstream snapshot semantics; not H1-specific | Only indirectly | RELATED |
| [`docs/WO_038_EXECUTION_LIFECYCLE_AUDIT.md`](./WO_038_EXECUTION_LIFECYCLE_AUDIT.md) | Runtime reached execution lifecycle in prior campaign; not about startup H1 readiness | Limited | HISTORICAL ONLY |
| [`docs/WO_039_PORTFOLIO_SELECTION_ELIGIBILITY_AUDIT.md`](./WO_039_PORTFOLIO_SELECTION_ELIGIBILITY_AUDIT.md) | Portfolio selection observability gap; not startup readiness | No | NOT RELEVANT |
| [`docs/WO_040_PORTFOLIO_SELECTION_OBSERVABILITY_DESIGN.md`](./WO_040_PORTFOLIO_SELECTION_OBSERVABILITY_DESIGN.md) | Observability design for selection telemetry; not readiness | No | NOT RELEVANT |
| [`docs/WO_041_PORTFOLIO_SELECTION_OBSERVABILITY_IMPLEMENTATION.md`](./WO_041_PORTFOLIO_SELECTION_OBSERVABILITY_IMPLEMENTATION.md) | Selection telemetry implementation; not readiness | No | NOT RELEVANT |
| [`docs/WO_042_PORTFOLIO_SELECTION_OBSERVABILITY_FORWARD_VALIDATION.md`](./WO_042_PORTFOLIO_SELECTION_OBSERVABILITY_FORWARD_VALIDATION.md) | Phase A/B evidence for the exact startup block and baseline regression checks | Yes | SAME ISSUE |
| [`docs/WS_M1_AUDIT.md`](./WS_M1_AUDIT.md) | Similar history-availability / closed-bar insufficiency characterization, but for M1 | Related pattern only | RELATED |
| [`docs/WS_M1_CHARACTERIZATION.md`](./WS_M1_CHARACTERIZATION.md) | Recurrent M1 shortfall and history gap patterns | Related pattern only | RELATED |
| [`docs/WO_017_M5_READINESS_CHARACTERIZATION.md`](./WO_017_M5_READINESS_CHARACTERIZATION.md) | Startup readiness and snapshot contract characterization on other timeframes | Historical context only | HISTORICAL ONLY |
| [`docs/WS_RUNTIME_POLICY_AUDIT.md`](./WS_RUNTIME_POLICY_AUDIT.md) | Confirms startup/readiness failures are fail-fast in current policy | Relevant to behavior, not root cause | RELATED |

Conclusion from discovery:

- The exact H1 blocker in WO-043 appears in WO-042, not as a previously isolated code defect.
- The broader class of startup-readiness / market-data insufficiency has been documented before.
- I did not find a prior H1-specific root-cause writeup that identifies a code bug for this exact symptom.

## 4. Workspace Source Audit

Audited source in the current workspace:

- [`tsp_v2/deployment.py`](../tsp_v2/deployment.py)
- [`tsp_v2/snapshots.py`](../tsp_v2/snapshots.py)
- [`tsp_v2/adapters/market_adapter.py`](../tsp_v2/adapters/market_adapter.py)
- [`tsp_v2/adapters/mt5_bridge.py`](../tsp_v2/adapters/mt5_bridge.py)
- [`tsp_v2/market_data.py`](../tsp_v2/market_data.py)
- [`deploy/v2/configs/forward_live.yaml`](../deploy/v2/configs/forward_live.yaml)

Key source facts:

- `deploy/v2/configs/forward_live.yaml` allowlist is `GBPUSD`, `GBPJPY`, `EURUSD`, and `DeploymentRuntime._primary_symbol()` returns the first allowlist entry, so startup readiness is checked first on `GBPUSD`. See [`deployment.py:692-717`](../tsp_v2/deployment.py:692) and [`deployment.py:950-953`](../tsp_v2/deployment.py:950).
- `_wait_for_startup_snapshot_readiness()` builds a snapshot for `primary_symbol` only and uses `build_market_snapshot(..., cycle_time_utc=broker_time)` as the gate. See [`deployment.py:683-797`](../tsp_v2/deployment.py:683).
- `build_market_snapshot()` requests raw rates for all timeframes, including `H1`, then evaluates raw availability before `_closed_bars()`. See [`snapshots.py:197-230`](../tsp_v2/snapshots.py:197), [`snapshots.py:239-330`](../tsp_v2/snapshots.py:239), [`snapshots.py:403-462`](../tsp_v2/snapshots.py:403).
- `evaluate_payload_health()` fails loud on raw `available < minimum` and labels the payload `STRICT_FAIL_LOUD`. See [`snapshots.py:418-441`](../tsp_v2/snapshots.py:418).
- `MarketAdapter.get_rates()` is a passthrough to the bridge and preserves `cycle_time_utc`. See [`market_adapter.py:58-72`](../tsp_v2/adapters/market_adapter.py:58).
- `MT5Bridge.get_rates()` uses explicit time-bounded `copy_rates_range()` when `cycle_time_utc` is present. For non-M1 timeframes, it uses a fixed lookback window of `max(count + 2, 40)` bars. For M1, only the M1 path is adaptive. See [`mt5_bridge.py:791-1080`](../tsp_v2/adapters/mt5_bridge.py:791).
- `get_latest_tick()` remains a separate path and is not the blocker here. See [`mt5_bridge.py:484-659`](../tsp_v2/adapters/mt5_bridge.py:484).

Source reconstruction:

```text
Deployment.start()
  -> _wait_for_startup_snapshot_readiness()
    -> broker_time = market_adapter.get_broker_time()
    -> build_market_snapshot(primary_symbol=GBPUSD, cycle_time_utc=broker_time)
      -> provider.get_rates("H1", 40, cycle_time_utc=broker_time)
        -> MT5Bridge.get_rates(..., cycle_time_utc=broker_time)
          -> copy_rates_range()
          -> evaluate_payload_health()
          -> payload_rejected when raw H1 available < 34
```

Important consequence:

- This failure happens before the startup retry branch that handles `closed_bars_insufficient`.
- The run does not reach `runtime_started` when the payload is rejected on raw H1 availability.

## 5. Startup Flow

Observed startup flow in the current implementation:

1. `DeploymentRuntime.start()` connects to MT5 and calls `_wait_for_startup_snapshot_readiness()`.
2. `_wait_for_startup_snapshot_readiness()` probes broker time, then builds a snapshot for the primary symbol only.
3. `build_market_snapshot()` fetches `M1`, `M5`, `M15`, and `H1` raw rates.
4. `evaluate_payload_health()` checks raw counts before closed-bar filtering.
5. If any timeframe is below minimum, `build_market_snapshot()` emits `stage=payload_rejected` and raises `ConfigValidationError`.
6. `DeploymentRuntime.start()` sees the failure, emits `startup_sync_failed`, and aborts before `runner_start`.

For WO-042, the primary-symbol H1 payload failed at step 4/5.

## 6. H1 Fetch Flow

The H1 fetch flow for startup readiness is:

```text
primary_symbol = GBPUSD
cycle_time_utc = broker_time
requested_bars(H1) = 40
minimum_closed_bar_count(H1) = 34
provider.get_rates("H1", 40, cycle_time_utc=broker_time)
  -> copy_rates_range()
  -> raw returned bars = 9
evaluate_payload_health()
  -> available = 9
  -> minimum = 34
  -> payload_health = RED
```

This is not a closed-bar failure. It is a raw availability failure.

The WO-042 campaign DB captured:

- `requested_bars.H1 = 40`
- `returned_bars.H1 = 9`
- `payload_diagnostics.details.H1.available = 9`
- `payload_diagnostics.details.H1.minimum = 34`
- `payload_diagnostics.missing_timeframes = ["H1"]`
- `payload_health = RED`
- `stage = payload_rejected`

## 7. Readiness Decision Path

Decision path in source:

```text
raw rates fetched
  -> evaluate_payload_health()
  -> if available < minimum
       -> payload_rejected
       -> startup_sync_failed
       -> stop before runtime_started
  -> else if closed bars below minimum
       -> waiting_for_snapshot_readiness
       -> retry later
```

WO-043 hit the first branch, not the retry branch.

That is why the startup did not progress to `runtime_started`, `runtime_recovered`, or `runtime_cycle`.

## 8. Artifact Findings

WO-042 campaign artifact:

- Log: [`validation/V2-V03/logs/WO042_20260803T083502Z-forward-run.log`](../validation/V2-V03/logs/WO042_20260803T083502Z-forward-run.log)
- DB: [`validation/V2-V03/db/WO042_20260803T083502Z.sqlite3`](../validation/V2-V03/db/WO042_20260803T083502Z.sqlite3)

Observed telemetry from the campaign DB:

- `deployment.startup_sync` -> `startup_sync_failed`
- `deployment.market_data_readiness` -> `payload_rejected`
- `runtime_started` -> not present
- `runtime_recovered` -> not present
- `runtime_cycle` -> not present
- `runtime.portfolio_selection` -> not present
- execution events -> not present

Representative blocking payload:

```json
{
  "stage": "payload_rejected",
  "symbol": "GBPUSD",
  "cycle_time_utc": "2026-08-03T08:35:14+00:00",
  "broker_time_utc": "2026-08-03T08:35:14+00:00",
  "payload_health": "RED",
  "requested_bars": {
    "H1": 40,
    "M1": 40,
    "M15": 40,
    "M5": 90
  },
  "returned_bars": {
    "H1": 9,
    "M1": 40,
    "M15": 34,
    "M5": 90
  },
  "payload_diagnostics": {
    "status": "RED",
    "policy": "STRICT_FAIL_LOUD",
    "missing_timeframes": ["H1"],
    "thin_timeframes": ["M15"],
    "details": {
      "H1": {"available": 9, "minimum": 34, "requested": 40},
      "M1": {"available": 40, "minimum": 34, "requested": 40},
      "M15": {"available": 34, "minimum": 34, "requested": 40},
      "M5": {"available": 90, "minimum": 70, "requested": 90}
    }
  }
}
```

Cross-check against the same campaign:

- M1 and M5 were healthy in the payload.
- M15 was thin but still met minimum.
- only H1 failed.

Historical comparison:

- Later startup-sync rows in the same DB show normal H1 availability again, for example `H1 = 38/39` on later dates and `snapshot_ready = true`.
- That means the implementation can successfully read H1 history under other conditions.
- The WO-043 symptom is therefore conditional, not a permanent code regression.

## 9. Candidate Root Cause Matrix

| Candidate | Status | Evidence |
| --- | --- | --- |
| Broker does not have H1 history yet | SUPPORTED | `returned_bars.H1 = 9` on startup while other timeframes were healthy. |
| Terminal has not synced H1 history | SUPPORTED | Same source path later produces H1 = 38/39 in other runs, so the issue is conditional and can clear. |
| History is still downloading | PARTIALLY SUPPORTED | The code fails on raw availability before runtime starts; the artifact is consistent with incomplete history availability. |
| Startup occurred too early | PARTIALLY SUPPORTED | The blocker happened at startup time and later windows show healthy H1, but the artifact alone does not prove a clock-only issue. |
| Window request is wrong | NOT SUPPORTED | `requested_bars.H1 = 40` is the frozen contract; source and artifacts agree. |
| Minimum bars are wrong | NOT SUPPORTED | Minimum H1 is 34 by contract and matches the readiness policy. |
| Closed-bar calculation is wrong | NOT SUPPORTED | Failure happened at `payload_rejected` on raw availability before closed-bar gating. |
| Broker time alignment is wrong | NOT OBSERVABLE | No direct evidence that alignment alone caused `available = 9`. |
| Weekend/session boundary | NOT OBSERVABLE | Plausible, but not proven by the current artifact set. |
| Symbol visibility | NOT SUPPORTED | GBPUSD is visible and works in later runs. |
| MT5 API limitation | PARTIALLY SUPPORTED | The H1 range call returned only 9 bars in one startup window, but later windows on the same code path returned 38/39. |
| Snapshot builder bug | NOT SUPPORTED | The snapshot builder behaved as designed: it rejected a deficient payload. |
| Market adapter bug | NOT SUPPORTED | The adapter passes `cycle_time_utc` through unchanged; no evidence of a pass-through defect. |
| MT5 bridge bug | NOT SUPPORTED | The same bridge returns healthy H1 counts in later runs. |
| Deployment readiness bug | NOT SUPPORTED | `STRICT_FAIL_LOUD` is intentional and matches the implemented policy. |
| Operational timing | SUPPORTED | The failure occurred at startup, and the same path later succeeds when H1 history is available. |

## 10. Operational vs Code Assessment

Assessment:

- Primary classification: **operational / market-data availability**
- Secondary classification: **startup history not yet available or not yet synced for H1 at the time of the WO-042 startup**
- Code defect classification: **not supported by current evidence**

Why this is operational:

- The source is intentionally fail-loud when raw availability is below minimum.
- The same source path later succeeds with H1 = 38/39 on later startup-sync rows.
- The blocker is isolated to the primary symbol at startup, before runtime begins.
- No evidence shows that the H1 branch is miscomputed or that the minimum is wrong.

Patch recommendation:

- **No code patch is justified from WO-043 evidence alone.**
- The next operational step is to ensure the MT5 terminal has current H1 history for GBPUSD before launching another startup campaign.
- If the same H1 shortage repeats after explicit terminal/history warm-up, then a follow-up WO should revisit the terminal/history acquisition path, not the selection/execution pipeline.

## 11. Pipeline Impact

Impact chain:

```text
Startup
  -> blocked
  -> Runtime
  -> not reached
  -> Signal
  -> not reached
  -> Portfolio
  -> not reached
  -> Risk
  -> not reached
  -> Execution
  -> not reached
```

Scope of impact:

- The blocker stops the entire pipeline at startup.
- It is not a portfolio-selection issue.
- It is not an execution issue.
- It is not an M5 issue.
- It is not a regression in WO-041 observability.

## 12. Regression Boundary

WO-043 does not propose changes to:

- WO-041 selection observability
- portfolio selection logic
- risk logic
- execution logic
- execution adapter behavior
- M5 window sizing
- snapshot semantics
- telemetry schema
- `_closed_bars()`

This audit is evidence-only.

## 13. Decision / Recommendation

Decision:

- The immediate blocker is **startup market-data availability for GBPUSD H1**.
- The most likely root cause is **operational history availability / terminal sync timing**, not a code defect.
- The current evidence does **not** justify a production patch.

Recommendation:

1. Verify the MT5 terminal has current GBPUSD H1 history loaded before the next campaign.
2. Re-run startup validation only after confirming history availability.
3. If H1 again returns `available < minimum` under a warmed terminal, open a follow-up WO focused on terminal/history acquisition or bridge fetch semantics.

Bottom line:

> WO-043 identifies a startup-readiness block caused by insufficient GBPUSD H1 history at the moment of startup. The evidence points to an operational availability problem, not a code regression, and no patch is justified yet.
