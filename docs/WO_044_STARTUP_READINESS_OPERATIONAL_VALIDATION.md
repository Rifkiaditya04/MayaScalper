# WO-044 Startup Readiness Operational Validation

## 1. Baseline

- `HEAD` before and after: `88d38665a907f77a1be47ac153c2d1c267646677`
- Workspace authority: the files currently present on disk in `D:\Maya\Scalper`
- Mode: documentation-only validation
- No production patch was made for WO-044

## 2. Workspace State

Observed at the start of WO-044:

- `git rev-parse HEAD` -> `88d38665a907f77a1be47ac153c2d1c267646677`
- `git diff --check` -> clean, with only LF/CRLF warnings in pre-existing dirty files
- `git status --short` -> pre-existing dirty runtime/doc artifacts remain

Pre-existing dirty state carried into WO-044:

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
- `?? runtime/forward_live/db/tsp_v2_runtime.sqlite3-shm`
- `?? runtime/forward_live/db/tsp_v2_runtime.sqlite3-wal`
- `?? runtime/forward_live/locks/`

WO-044 itself did not modify production code, test code, or runtime artifacts.

## 3. Operational Preparation

WO-044 was rerun after the MT5 application had been opened again by the user.

The operational intent was to see whether the H1 history state would recover enough for startup readiness to pass without any code change.

## 4. Campaign Metadata

- Campaign timestamp: `20260805T030349Z`
- Log path: `validation/V2-V03/logs/WO044_20260805T030349Z-forward-run.log`
- DB copy: `validation/V2-V03/db/WO044_20260805T030349Z.sqlite3`
- Campaign outcome: startup stopped before `runner_start`

## 5. Startup Readiness Results

The campaign still failed inside startup readiness with a rejected payload:

```json
{
  "stage": "payload_rejected",
  "symbol": "GBPJPY",
  "cycle_time_utc": "2026-08-05T03:03:06+00:00",
  "broker_time_utc": "2026-08-05T03:03:06+00:00",
  "payload_health": "RED",
  "requested_bars": {
    "H1": 40,
    "M1": 40,
    "M15": 40,
    "M5": 90
  },
  "returned_bars": {
    "H1": 4,
    "M1": 40,
    "M15": 13,
    "M5": 37
  },
  "payload_diagnostics": {
    "status": "RED",
    "policy": "STRICT_FAIL_LOUD",
    "missing_timeframes": ["M5", "M15", "H1"],
    "thin_timeframes": [],
    "details": {
      "H1": {"available": 4, "minimum": 34, "requested": 40},
      "M1": {"available": 40, "minimum": 34, "requested": 40},
      "M15": {"available": 13, "minimum": 34, "requested": 40},
      "M5": {"available": 37, "minimum": 70, "requested": 90}
    }
  }
}
```

Key readiness facts from WO-044:

- `H1 available = 4`
- `M15 available = 13`
- `M5 available = 37`
- `M1 available = 40`
- `payload_health = RED`
- `stage = payload_rejected`
- startup did not proceed beyond readiness

## 6. Comparison With WO-042

WO-042 and WO-044 both failed before runtime entry, but the failing payload moved.

| Item | WO-042 | WO-044 |
| --- | --- | --- |
| `cycle_time_utc` | `2026-08-03T08:35:14+00:00` | `2026-08-05T03:03:06+00:00` |
| `H1 available` | `9` | `4` |
| `M1 available` | `40` | `40` |
| `M15 available` | `34` | `13` |
| `M5 available` | `90` | `37` |
| `payload_health` | `RED` | `RED` |
| `stage` | `payload_rejected` | `payload_rejected` |
| `runner_start` | not reached | not reached |
| `runtime_started` | not reached | not reached |
| `runtime_cycle` | `0` | `0` |

Interpretation:

- The startup blocker persisted.
- WO-044 did not produce evidence that the H1 history state had recovered enough for startup readiness.
- Because startup still failed, WO-041 portfolio telemetry could not be exercised in this campaign.

## 7. Runtime Reachability

WO-044 did not reach:

- `deployment.runner_start`
- `runtime_started`
- `runtime_recovered`
- `runtime_cycle`
- `runtime.portfolio_selection`

Therefore:

- runtime execution lifecycle was not reachable in this campaign
- portfolio observability was not testable on live runtime

## 8. Portfolio Observability Status

WO-041 observability remained unexercised in WO-044 because the campaign stopped before runtime entry.

Observed counts in the WO-044 campaign DB:

- `deployment.market_data_readiness`: `1`
- `deployment.runner_start`: `0`
- `runtime_started`: `0`
- `runtime_recovered`: `0`
- `runtime_cycle`: `0`
- `runtime.portfolio_selection`: `0`

## 9. Decision

Classification:

- `BLOCKED — HISTORY_STILL_UNAVAILABLE`

Conclusion:

- The operational rerun did not clear startup readiness.
- The evidence still points to insufficient market-data history at startup, not to a portfolio-selection, risk, or execution issue.
- There is no basis in WO-044 to patch production code.

## 10. Recommendation

- Keep WO-041 unchanged.
- Keep the startup readiness contract unchanged.
- Continue with operational/history validation if another attempt is desired.
- Do not open a code patch unless a future campaign shows startup readiness is available but runtime still fails for a code-specific reason.
