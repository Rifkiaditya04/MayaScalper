# WO-038 Execution Lifecycle Audit

## Baseline
`88d38665a907f77a1be47ac153c2d1c267646677`

## Scope
- Source files audited:
  - [`tsp_v2/live_runtime.py`](../tsp_v2/live_runtime.py)
  - [`tsp_v2/signals.py`](../tsp_v2/signals.py)
  - [`tsp_v2/portfolio.py`](../tsp_v2/portfolio.py)
  - [`tsp_v2/risk.py`](../tsp_v2/risk.py)
  - [`tsp_v2/execution.py`](../tsp_v2/execution.py)
  - [`tsp_v2/snapshots.py`](../tsp_v2/snapshots.py)
  - [`tsp_v2/deployment.py`](../tsp_v2/deployment.py)
  - [`tsp_v2/adapters/market_adapter.py`](../tsp_v2/adapters/market_adapter.py)
  - [`tsp_v2/adapters/mt5_bridge.py`](../tsp_v2/adapters/mt5_bridge.py)
- Campaign artifacts:
  - `validation/V2-V03/logs/WO037_20260729T121541Z-forward-run.log`
  - `validation/V2-V03/db/WO037_20260729T121541Z.sqlite3`

## Runtime Entry
- `deployment.runner_start` is present in WO-037.
- `runtime_started` is present in WO-037.
- `runtime_recovered` is present in WO-037.
- `runtime_cycle` is present and repeated.

Source map:
- `live_runtime.py:157-199` emits `runtime_started`, `runtime_recovered`, and `runtime_cycle`.
- `live_runtime.py:261-438` executes the cycle, including signal ranking, risk evaluation, validation, and execution attempt.
- `live_runtime.py:479-492` persists `runtime_cycle` telemetry.

## Signal
- Latest WO-037 `runtime_cycle` row:
  - `signal_count = 1`
  - `execution_count = 0`
  - `selected_symbols = []`
- Latest WO-037 `runtime_cycle` telemetry index row also shows:
  - `selected_signal_count = 0`
  - `execution_count = 0`
- There are no WO-037 `execution_intent_rejected` or `execution_validation_rejected` telemetry topics in `telemetry_index`.

Source map:
- `signals.py:67-134` accepts/rejects signal candidates and returns `SignalEvaluation`.
- `portfolio.py:22-66` ranks accepted signals and can return an empty selection.
- `live_runtime.py:307-319` builds `accepted_signals` and `selected_signals`.
- `live_runtime.py:421-432` stores `signal_count` and `execution_count` in `LiveCycleReport`.

Interpretation:
- `signal_count=1` does not imply an execution attempt.
- In WO-037, the accepted signal did not survive portfolio selection, so execution never entered the risk/validation branch.

## Risk / Execution
- No fresh WO-037 `execution_attempt` was observed.
- No fresh WO-037 `execution_filled` was observed.
- No fresh WO-037 `execution_rejected` was observed.
- The only `execution_rejected` topic in the DB belongs to older history (`2026-06-19`), not the WO-037 campaign window.

Source map:
- `risk.py:75-237` can block or size a selected signal, but it is only reached after portfolio selection.
- `execution.py:402-503` builds and validates execution intent after risk passes.
- `live_runtime.py:344-393` only calls `evaluate_risk()`, `validate_execution_intent()`, and `execution_adapter.execute()` for `selected_signals`.

## Lifecycle Classification
`SIGNAL_NOT_ELIGIBLE`

## Conclusion
WO-037 validated startup, broker reconciliation, market data readiness, and repeated runtime cycles. The campaign did not produce a fresh execution event because the latest cycle shows an accepted signal (`signal_count=1`) but no selected signal (`selected_signal_count=0`, `selected_symbols=[]`), so the runtime never entered the risk/execution branch. This is not evidence of an execution-layer rejection; it is evidence that the signal did not make it through portfolio selection.

Baseline tests rerun on this audit:
- `python -m unittest tsp_v2.tests.test_mt5_bridge` PASS
- `python -m unittest tsp_v2.tests.test_market_adapter` PASS
- `python -m unittest tsp_v2.tests.test_snapshots` PASS
- `python -m unittest tsp_v2.tests.test_deployment` PASS
- Additional lifecycle tests:
  - `python -m unittest tsp_v2.tests.test_signals` PASS
  - `python -m unittest tsp_v2.tests.test_risk` PASS
  - `python -m unittest tsp_v2.tests.test_execution` PASS
  - `python -m unittest tsp_v2.tests.test_portfolio` PASS

Final assessment:
- `execution_count=0` in WO-037 is explained by a signal that was accepted but not selected for execution.
- There is no evidence in WO-037 of `execution_filled`, `execution_rejected`, or an execution-layer defect.
- No production patch is justified by WO-038.
