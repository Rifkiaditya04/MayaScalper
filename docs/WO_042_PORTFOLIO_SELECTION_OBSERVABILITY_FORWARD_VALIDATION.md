# WO-042 Portfolio Selection Observability Forward Validation

## 1. Baseline

```text
HEAD before campaign = 88d38665a907f77a1be47ac153c2d1c267646677
HEAD after campaign  = 88d38665a907f77a1be47ac153c2d1c267646677
```

## 2. Workspace State

Workspace authority:
- the files currently present in `D:\Maya\Scalper` are the authority for this validation
- `HEAD` was used only as a Git reference

Pre-existing dirty state carried into WO-042:
- `M runtime/forward_live/db/tsp_v2_runtime.sqlite3`
- `?? docs/WO_035_M5_WINDOW_DESIGN_REVIEW.md`
- `?? docs/WO_038_EXECUTION_LIFECYCLE_AUDIT.md`
- `?? docs/WO_039_PORTFOLIO_SELECTION_ELIGIBILITY_AUDIT.md`
- `?? docs/WO_040_PORTFOLIO_SELECTION_OBSERVABILITY_DESIGN.md`
- `?? docs/WO_041_PORTFOLIO_SELECTION_OBSERVABILITY_IMPLEMENTATION.md`
- `?? runtime/forward_live/db/tsp_v2_runtime.sqlite3-shm`
- `?? runtime/forward_live/db/tsp_v2_runtime.sqlite3-wal`
- `?? runtime/forward_live/locks/`

WO-042 added no production behavior changes.

## 3. WO-041 Implementation Verification

Source files inspected before the campaign:
- `tsp_v2/portfolio.py`
- `tsp_v2/live_runtime.py`
- `tsp_v2/persistence.py`
- `tsp_v2/tests/test_portfolio.py`
- `tsp_v2/tests/test_deployment.py`
- `tsp_v2/tests/test_telemetry.py`
- `docs/WO_041_PORTFOLIO_SELECTION_OBSERVABILITY_IMPLEMENTATION.md`

Implementation behavior confirmed before the campaign:
- `rank_opportunities()` remains pure and unchanged in result behavior
- `trace_rank_opportunities()` produces one trace per candidate
- selected candidates emit `selected=true` and `first_rejection_reason=null`
- rejected candidates emit a stable `first_rejection_reason`
- runtime emits `runtime.portfolio_selection`
- aggregate `runtime_cycle` telemetry is still emitted separately

## 4. Baseline Tests

| Suite | Result |
| --- | --- |
| `python -m unittest tsp_v2.tests.test_portfolio tsp_v2.tests.test_deployment tsp_v2.tests.test_telemetry` | PASS |
| `python -m unittest tsp_v2.tests.test_mt5_bridge tsp_v2.tests.test_market_adapter tsp_v2.tests.test_snapshots tsp_v2.tests.test_signals tsp_v2.tests.test_risk tsp_v2.tests.test_execution` | PASS |

## 5. Campaign Metadata

Campaign timestamp:
- `20260803T083502Z`

Campaign transcript:
- `validation/V2-V03/logs/WO042_20260803T083502Z-forward-run.log`

Campaign DB copy:
- `validation/V2-V03/db/WO042_20260803T083502Z.sqlite3`

Campaign stop condition:
- launcher exited with `Start failed with exit code 1`
- runtime did not reach `runner_start`

## 6. Runtime Evidence

Observed from the campaign DB and transcript:
- `deployment.runner_start` from WO-042: not present
- `runtime_started` from WO-042: not present
- `runtime_recovered` from WO-042: not present
- `runtime_cycle` from WO-042: not present

The campaign stopped inside startup readiness before a runtime cycle could begin.

The blocking telemetry payload was:

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

## 7. Portfolio Selection Telemetry

Total `runtime.portfolio_selection` events in the WO-042 campaign DB:
- `0`

Counts observed:
- selected count: `0`
- rejected count: `0`

Reason values observed:
- none

Reason for no telemetry:
- the campaign never reached `runtime_started`
- therefore no runtime cycle was executed
- therefore no accepted signals were evaluated by portfolio selection in WO-042

## 8. Representative Candidate Traces

No representative candidate traces were produced in this campaign, because the runtime never reached a selection cycle.

## 9. Signal-to-Selection Correlation

No WO-042 runtime cycle was emitted, so there is no campaign-cycle `signal_count` / `selected_signal_count` pair to correlate against selection telemetry.

## 10. Persistence Verification

Verified in the campaign DB:
- `deployment.market_data_readiness` was persisted
- `runtime.portfolio_selection` was not present
- `runtime_cycle` was not present

This is consistent with startup failing before runtime entry.

## 11. Execution Evidence

No execution lifecycle events were produced in WO-042.

Observed:
- `execution_intent_rejected`: not present in WO-042
- `execution_filled`: not present in WO-042
- `execution_rejected`: not present in WO-042

Reason:
- startup readiness failed before the runtime loop could begin

## 12. Selection Invariance

Selection invariance was verified by:
- WO-041 unit tests passing
- no source changes beyond the WO-041 observability implementation

WO-042 itself did not reach the selection phase, so it did not alter or exercise selection behavior during the campaign.

## 13. VPN / Network Assessment

No evidence that VPN state affected the WO-042 observability validation.

The campaign failed on payload readiness with `H1 available=9 < minimum=34`, which is a market-data readiness block, not a demonstrated VPN/network failure.

## 14. Classification

```text
BLOCKED — STARTUP_READINESS
```

Secondary characterization:
- `M1` and `M5` were healthy in the readiness payload
- the gating failure came from `H1` availability on `GBPUSD`
- `M15` was thin but still at minimum

## 15. Decision

WO-042 did not validate `runtime.portfolio_selection` in a live runtime cycle because the campaign stopped before `runner_start`.

Recommended next step:
- do not patch observability
- treat this as a startup-readiness / market-data availability block
- if a later campaign window provides full startup readiness, rerun validation and expect `runtime.portfolio_selection` to appear only once the runtime reaches `runtime_cycle`

## 16. Phase B — Baseline Validation

Timestamp: `2026-08-05T01:11:59.2960974Z`

Workspace verification:
- `HEAD`: `88d38665a907f77a1be47ac153c2d1c267646677`
- `git diff --check`: clean
- `git status --short`: unchanged from the known pre-existing dirty workspace state

Baseline test suites:
- `python -m unittest tsp_v2.tests.test_portfolio` -> PASS
- `python -m unittest tsp_v2.tests.test_deployment` -> PASS
- `python -m unittest tsp_v2.tests.test_telemetry` -> PASS
- `python -m unittest tsp_v2.tests.test_mt5_bridge tsp_v2.tests.test_market_adapter tsp_v2.tests.test_snapshots tsp_v2.tests.test_signals tsp_v2.tests.test_risk tsp_v2.tests.test_execution` -> PASS

Conclusion:
Phase B confirmed that the WO-041 observability implementation remains regression-clean under the available baseline test suites. The WO-042 forward campaign remains blocked exclusively by startup market-data readiness (`GBPUSD H1 available=9 < minimum=34`). No selection observability behavior was exercised in the blocked campaign.

## 17. Phase B Final State

- workspace authority used: yes
- production files changed by Phase B: `none`
- test files changed by Phase B: `none`
- commit: `none`
- `git diff --check`: clean
- `runtime.portfolio_selection` observed in WO-042 campaign: `no`
- execution lifecycle observed in WO-042 campaign: `no`
- WO-042 classification remains: `BLOCKED - STARTUP_READINESS`

## 16. Final Summary

- workspace authority used: yes
- production files changed: `none` by WO-042
- test files changed: `none` by WO-042
- commit: `none`
- `git diff --check`: clean
- `runtime.portfolio_selection` observed: `no`
- execution lifecycle observed: `no`
- campaign classification: `BLOCKED — STARTUP_READINESS`
