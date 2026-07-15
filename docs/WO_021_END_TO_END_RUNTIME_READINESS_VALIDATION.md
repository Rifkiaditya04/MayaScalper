# WO-021 - End-to-End Runtime Readiness Validation

## Metadata

- Status: Audit Complete, live forward rerun pending
- Objective type: Readiness validation
- Evidence baseline: 2026-07-15
- Related docs:
  - `docs/WO_017_M5_READINESS_CHARACTERIZATION.md`
  - `docs/WO_018_RUNTIME_READINESS_POLICY_REVIEW.md`
  - `docs/DR_001_BROKER_RECONCILIATION_MISSING_BROKER_MISCLASSIFICATION.md`
  - `docs/PATCH_DR001A_BROKER_RECONCILIATION_CLASSIFICATION_FIX.md`
  - `docs/WS_019_RUNTIME_FAILURE_INVENTORY.md`
  - `docs/WS_020_RUNTIME_EXCEPTION_COVERAGE_REVIEW.md`
  - `docs/MA5_EXECUTION_EVIDENCE.md`
  - `docs/EVIDENCE_REGISTER.md`

---

## Objective

Membuktikan bahwa seluruh perubahan governance yang sudah dibekukan:

- WO-017
- WO-018
- DR-001 / PATCH-DR001A
- WS-019
- WS-020

benar-benar membentuk runtime readiness yang dapat diaudit pada forward campaign berikutnya.

Dokumen ini bukan karakterisasi baru.

Dokumen ini adalah audit end-to-end atas dependency readiness, cycle flow, telemetry, dan evidence before/after patch.

---

## D1 - Readiness Matrix

| Component | Source | Current Rule | Evidence |
| --- | --- | --- | --- |
| Bootstrap reconciliation | `tsp_v2/recovery/bootstrap.py:39-124` | `ready_to_resume = not unresolved_setup_ids or allow_flatten_unresolved` | Bootstrap report exposes `ready_to_resume` and records `deployment_start` / `bootstrap` recovery events |
| Broker reconciliation | `tsp_v2/recovery/reconcile.py:140-145`, `352-389`, `413-437` | `critical_divergence` keeps `health_state.broker_reconciliation` non-GREEN when missing broker / orphan / divergence exists | Before patch, July DB shows `missing_broker_count = 2`, `ready_to_resume = false`, `broker_reconciliation = RED` |
| Market snapshot | `tsp_v2/snapshots.py:52-190`, `240-258`, `418-479`, `572-679` | `ConfigValidationError` on partial payload, invalid tick/rates, insufficient closed bars, malformed datetime / ATR / ADX | July campaign shows repeated M1 / M5 `closed_bars_insufficient` failures, no tick-staleness failures in the July DB |
| M5 gate | `tsp_v2/live_runtime.py:560-574`, `576-583` | `deployment.closed_m5_gate` returns `skip` when current close is not newer than last processed close | Category A path is non-fatal and keeps runtime alive |
| Execution lifecycle | `tsp_v2/live_runtime.py:343-423`, `tsp_v2/adapters/mt5_bridge.py:1000-1029`, `tsp_v2/live_runtime.py:503-557` | `validate_execution_intent()` may reject; `execute()` may return `API_HUNG` without raising; execution telemetry and registry are persisted | MA5 evidence and DR-001 both confirm execution events and registry rows are auditable |
| Runtime stop / observability | `tsp_v2/live_runtime.py:206-249` | `runtime_error` is emitted before re-raise; `runtime_stopped` is emitted from `finally` | Every fatal runtime exception path examined in WS-020 has a telemetry outcome |

---

## D2 - Trace One Cycle

The steady-state cycle is ordered as follows:

```text
run()
  -> runtime_started
  -> runtime_recovered
  -> _run_cycle()
       -> market_status check
       -> reconcile (pre-snapshot)
       -> build_market_snapshot() for each allowlisted symbol
       -> M5 gate
       -> signal evaluation
       -> governor evaluation
       -> risk validation
       -> validate_execution_intent()
       -> execution_adapter.execute()
       -> store execution telemetry / registry
       -> reconcile (post-execution)
       -> store runtime snapshot/state
       -> return LiveCycleReport
  -> runtime_cycle
  -> runtime_stopped (on exit path)
```

### Points that can stop the cycle

- `ConfigValidationError` at market-status check: `live_runtime.py:263-265`
- `BrokerReconciliationRuntime.reconcile()` failure before snapshot: `live_runtime.py:269-273`
- `build_market_snapshot()` failure: `live_runtime.py:277-285`, `snapshots.py:52-190`, `240-258`, `418-479`, `572-679`
- `ConfigValidationError` from M5 close lookup: `live_runtime.py:576-583`
- execution adapter exception: `live_runtime.py:390-392`
- post-execution reconciliation failure: `live_runtime.py:408-412`
- `KeyboardInterrupt` / generic exception in `run()`: `live_runtime.py:206-249`

### Points that skip but do not stop

- M5 gate `decision = "skip"` returns `None` and the loop sleeps / continues: `live_runtime.py:560-574`, `live_runtime.py:175-180`
- invalid risk or rejected execution intent continues to the next signal: `live_runtime.py:356-388`

### Points that continue after a recoverable result

- `send_order()` returns `MT5TradeResult` for `API_HUNG` without raising: `tsp_v2/adapters/mt5_bridge.py:1000-1029`
- `validate_execution_intent()` rejection is handled as a normal continue path: `live_runtime.py:369-388`

---

## D3 - Telemetry Matrix

| Runtime Mode | Expected Telemetry | Source |
| --- | --- | --- |
| Normal runtime | `deployment.startup`, `deployment.startup_sync`, `runtime_started`, `runtime_recovered`, `runtime_cycle`, `deployment.market_data_readiness`, `deployment.closed_m5_gate`, `execution_filled` / `execution_rejected` / `execution_submitted`, `reconciliation_started`, `reconciliation_completed`, `runtime_stopped` | `tsp_v2/deployment.py`, `tsp_v2/live_runtime.py`, `tsp_v2/recovery/reconcile.py` |
| Skip cycle | `deployment.closed_m5_gate` with `decision = "skip"` | `tsp_v2/live_runtime.py:560-574` |
| Fatal runtime | `runtime_error`, `runtime_stopped`; for `MT5BridgeError` also `deployment.market_data_readiness` with `stage = "bridge_error"` | `tsp_v2/live_runtime.py:206-249` |
| Recovery / bootstrap | `deployment.startup_sync`, `reconciliation_started`, `reconciliation_completed`, `deployment_start`, `runtime_started`, `runtime_recovered` | `tsp_v2/deployment.py:481-519`, `tsp_v2/recovery/bootstrap.py:39-124`, `tsp_v2/live_runtime.py:148-167` |

Observation:

- `runtime_cycle` is emitted both as a stored telemetry snapshot and as part of the runtime state snapshot path: `tsp_v2/live_runtime.py:478-490`, `tsp_v2/live_runtime.py:183-199`
- `runtime_stopped` is emitted from `finally`, so it is the last guaranteed observability event on a stopped runtime: `tsp_v2/live_runtime.py:240-249`

---

## D4 - Evidence Before / After PATCH-DR001A

### Before PATCH-DR001A

The July forward_live DB snapshot shows:

- `health_state.component = broker_reconciliation`
- `health_state.state = RED`
- `missing_broker_count = 2`
- `ready_to_resume = false`
- `orphan_position_count = 0`

The same DB snapshot also shows:

- `execution_registry` contains 2 `EXPIRED` rows with `broker_ticket = NULL`
- `execution_events` in the July window are `0`
- `recovery_events` for `broker_reconciliation/conflict` are recurrent and repeat the same `missing_broker_count = 2` outcome

This is the pre-patch operational baseline that motivated DR-001.

### After PATCH-DR001A

The patched reconciliation rule is now explicit:

- `EXPIRED` / `CANCELLED` entries without `broker_ticket` are skipped in the missing-broker loop: `tsp_v2/recovery/reconcile.py:140-145`
- the patch is deliberately narrow and does not change persistence lifecycle or runtime policy: `docs/PATCH_DR001A_BROKER_RECONCILIATION_CLASSIFICATION_FIX.md:14-20, 34-40, 59-61`

Regression evidence:

- `tsp_v2/tests/test_broker_reconciliation.py:232-299` proves two terminal entries without broker tickets are not counted as `MISSING_BROKER`
- the same test proves an active entry without broker ticket is still counted
- `python -m unittest tsp_v2.tests.test_broker_reconciliation` passed
- `python -m unittest discover tsp_v2.tests` passed (`132` tests)

### What this means

The code-level after-state is verified.

What is not present in the repo snapshot is a fresh post-patch forward_live DB campaign showing the new runtime state on a live rerun. The existing July DB remains a pre-patch snapshot, so it should not be used to claim post-patch live readiness.

---

## D5 - Regression Check

### WO-017

- `tsp_v2/snapshots.py` still fail-louds on insufficient closed bars and malformed snapshot payloads.
- `docs/WO_017_M5_READINESS_CHARACTERIZATION.md` remains a characterization document, not a contract rewrite.
- No code path in WO-021 changes M1/M5 semantics.

### WO-018

- Runtime fail-fast policy remains the current baseline for escaped cycle exceptions.
- `docs/WO_018_RUNTIME_READINESS_POLICY_REVIEW.md` remains closed and unchanged by this audit.
- No runtime recovery policy is introduced here.

### DR-001

- The defect patch is narrow and regression-covered.
- Test evidence confirms the accounting fix.
- No persistence lifecycle change was introduced.

---

## Verdict

### What the evidence confirms

- `ready_to_resume` is explicit and traceable through bootstrap / reconciliation code.
- The steady-state cycle has clear stop, skip, and continue boundaries.
- Telemetry coverage exists for normal, skip, fatal, and recovery paths.
- Before PATCH-DR001A, July forward_live evidence showed a persistent broker-reconciliation red state caused by terminal entries without broker tickets.
- After PATCH-DR001A, the reconciliation classification fix is verified by regression tests.

### What remains incomplete

- The repository snapshot does not contain a fresh post-patch forward_live campaign artifact.
- Therefore, a live claim that the July campaign is now green after the patch would be overstated.

### Final judgment

WO-021 is complete as an end-to-end audit of readiness dependencies, cycle flow, telemetry coverage, and before/after patch behavior.

The only remaining operational step is a fresh forward_live rerun after PATCH-DR001A, so the live DB can be refreshed and `ready_to_resume` can be rechecked against current broker truth.
