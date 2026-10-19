# WO-039 Portfolio Selection Eligibility Audit

## Baseline
`88d38665a907f77a1be47ac153c2d1c267646677`

## Repository State
- `HEAD` verified at the WO-037/WO-038 baseline.
- Working tree is dirty only because of runtime artifacts and prior untracked evidence notes.
- No production source was changed for WO-039.

Observed Git state at audit time:
- `runtime/forward_live/db/tsp_v2_runtime.sqlite3` modified
- `runtime/forward_live/db/tsp_v2_runtime.sqlite3-shm` untracked
- `runtime/forward_live/db/tsp_v2_runtime.sqlite3-wal` untracked
- `runtime/forward_live/locks/` untracked
- `docs/WO_035_M5_WINDOW_DESIGN_REVIEW.md` untracked
- `docs/WO_038_EXECUTION_LIFECYCLE_AUDIT.md` untracked

## Source Map
- `tsp_v2/live_runtime.py:307-319` builds `accepted_signals` and passes them into `rank_opportunities()`.
- `tsp_v2/live_runtime.py:321-393` applies governor, risk, validation, and execution only to `selected_signals`.
- `tsp_v2/live_runtime.py:421-432` records `selected_symbols`, `signal_count`, and `execution_count` in the cycle report.
- `tsp_v2/signals.py:67-134` accepts or rejects signal candidates and returns `SignalEvaluation`.
- `tsp_v2/portfolio.py:22-66` sorts accepted signals and filters them by expiry, cooldown, execution/spread/latency health, correlation budget, and replacement rules.
- `tsp_v2/risk.py:75-237` only runs after portfolio selection and can block or size the selected signal.
- `tsp_v2/execution.py:444-503` validates execution intent only after risk passes.
- `tsp_v2/snapshots.py:52-239` builds the snapshot that feeds regime/signal generation.

## WO-037 Lifecycle Evidence
Relevant WO-037 evidence from `validation/V2-V03/db/WO037_20260729T121541Z.sqlite3`:

- `deployment.runner_start` exists and reports `ready_to_resume=true`.
- `runtime_started` exists.
- `runtime_recovered` exists.
- `runtime_cycle` exists repeatedly.
- Latest `runtime_cycle` row:
  - `event_time_utc = 2026-07-29T12:40:09+00:00`
  - `signal_count = 1`
  - `selected_signal_count = 0`
  - `selected_symbols = []`
  - `execution_count = 0`
- `positions` table is empty.
- `account_state` shows `trade_allowed=true`, `trade_expert=true`, and no open-risk footprint in local state.
- `governor_state` is `NORMAL`.

Telemetry coverage in the WO-037 DB:
- Present:
  - `runtime_cycle`
  - `deployment.runner_start`
  - `runtime_started`
  - `runtime_recovered`
  - `deployment.market_data_readiness`
  - `deployment.snapshot_timing`
  - `execution_rejected` only from older history, not WO-037
- Not present:
  - signal-level telemetry topic with candidate identity / score / reject reason
  - portfolio-selection telemetry topic with explicit selection reject reason
  - execution attempt telemetry for WO-037

## Concrete Signal Trace
What can be traced concretely from the available evidence:

1. `live_runtime.py` collects `signal_evaluations`.
2. Accepted signal evaluations become `accepted_signals`.
3. `rank_opportunities()` receives those accepted signals.
4. `rank_opportunities()` can return an empty list after applying:
   - expiration
   - symbol cooldown
   - execution health
   - spread health
   - latency health
   - correlation budget
   - replacement constraints
5. WO-037’s latest cycle recorded `signal_count=1` but `selected_signal_count=0`.
6. Because `selected_signals` was empty, the runtime never entered:
   - `evaluate_risk()`
   - `validate_execution_intent()`
   - `execution_adapter.execute()`

What is not observable from the DB/log:
- the concrete signal identity
- the signal direction
- the signal score/threshold
- the exact portfolio-selection rejection reason

## Portfolio Selection Decision
Evidence-supported conclusion:
- A signal existed at the signal layer (`signal_count=1`).
- That signal did not survive portfolio selection (`selected_signal_count=0`).
- No execution attempt was made.
- No fresh `execution_filled` or `execution_rejected` event was produced by WO-037.

Observed constraints that were *not* evidenced as the blocker:
- no local open positions
- governor state was `NORMAL`
- broker reconciliation was `GREEN`

Because the artifacts do not store the selection rejection reason, the exact reason remains unobserved.

## Selection Rejection Reason
`NOT OBSERVABLE`

There is no DB or log field in the WO-037 artifacts that records why the accepted signal was dropped by `rank_opportunities()`. The source exposes several possible filters, but the artifact set does not identify which one fired.

## Classification
`PORTFOLIO_SELECTION_OBSERVABILITY_GAP`

Rationale:
- `signal_count=1` proves the signal layer produced at least one accepted signal.
- `selected_signal_count=0` proves portfolio selection removed everything before risk/execution.
- The reason for removal is not captured in WO-037 telemetry.
- There is no evidence of a signal-pipeline defect from the current artifacts.

## Decision
- No production patch is justified by WO-039.
- The next useful step would be to add observability to portfolio selection if we want to distinguish:
  - cooldown
  - expiry
  - health gating
  - correlation gating
  - replacement gating
  - duplicate-symbol gating
- Until then, WO-037 should be interpreted as:
  - runtime healthy
  - one signal accepted
  - zero signals selected for execution
  - no execution attempt
