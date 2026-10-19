# WO-040 Portfolio Selection Observability Design

## 1. Baseline / Git Reference

Baseline Git reference for this review:

```text
88d38665a907f77a1be47ac153c2d1c267646677
```

This is used only as a Git reference point. The source of truth for the analysis below is the **current workspace contents on disk**, not the commit snapshot.

## 2. Workspace Authority and Repository State

Workspace authority:
- The files currently present in `D:\Maya\Scalper` are the authority for this review.
- If a workspace file differs from `HEAD`, the workspace version takes precedence.
- This review does not treat `HEAD` as the content authority for source inspection.

Repository state observed during the review:
- `git rev-parse HEAD` -> `88d38665a907f77a1be47ac153c2d1c267646677`
- `git diff --check` -> clean
- `git status --short` shows pre-existing dirty state unrelated to WO-040:
  - `M runtime/forward_live/db/tsp_v2_runtime.sqlite3`
  - `?? docs/WO_035_M5_WINDOW_DESIGN_REVIEW.md`
  - `?? docs/WO_038_EXECUTION_LIFECYCLE_AUDIT.md`
  - `?? docs/WO_039_PORTFOLIO_SELECTION_ELIGIBILITY_AUDIT.md`
  - `?? runtime/forward_live/db/tsp_v2_runtime.sqlite3-shm`
  - `?? runtime/forward_live/db/tsp_v2_runtime.sqlite3-wal`
  - `?? runtime/forward_live/locks/`

No production code was modified by WO-040 design review.

## 3. Problem Statement

WO-039 established that the runtime can produce an accepted signal (`signal_count=1`) while still yielding `selected_signal_count=0`.

The open question is not how to make selection more permissive. The question is:

> Which selection gate rejected the accepted candidate, and how do we record that deterministically without changing selection behavior?

The current workspace does not persist the reason a candidate was not selected.

## 4. Source Scope

Audited source files in the workspace:
- [`tsp_v2/portfolio.py`](../tsp_v2/portfolio.py)
- [`tsp_v2/live_runtime.py`](../tsp_v2/live_runtime.py)
- [`tsp_v2/persistence.py`](../tsp_v2/persistence.py)
- [`tsp_v2/models.py`](../tsp_v2/models.py)
- [`tsp_v2/signals.py`](../tsp_v2/signals.py)
- [`tsp_v2/tests/test_portfolio.py`](../tsp_v2/tests/test_portfolio.py)
- [`tsp_v2/tests/test_deployment.py`](../tsp_v2/tests/test_deployment.py)

Relevant source facts:
- `SignalDecision` contains `setup_id`, `signal_family`, `symbol`, `direction`, `score`, `threshold`, `expires_at_utc`, `rationale`, and `lineage`.
- `SignalEvaluation` contains `accepted`, `decision`, `reject_reason`, `signal_key`, and `diagnostics`.
- `PortfolioContext` contains `active_positions`, `current_time_utc`, `execution_health`, `spread_health`, `latency_health`, `replacement_superiority`, `max_positions`, and `symbol_cooldown_until_utc`.
- `store_telemetry_index()` in persistence is generic and only stores `topic + payload_json`.

## 5. Current Selection Flow

Current runtime flow in the workspace:

```text
MarketSnapshot
  -> classify_regime()
  -> evaluate_signals()
  -> accepted_signals
  -> rank_opportunities()
  -> selected_signals
  -> evaluate_risk()
  -> validate_execution_intent()
  -> execution_adapter.execute()
```

Key source excerpts:

```python
accepted_signals = [evaluation.decision for evaluation in signal_evaluations if evaluation.accepted and evaluation.decision is not None]
signal_decisions = tuple(accepted_signals)
selected_signals = rank_opportunities(
    list(signal_decisions),
    context=PortfolioContext(
        active_positions=self._broker_positions_as_snapshots(broker_positions),
        current_time_utc=broker_time,
        execution_health=market_status.health,
        spread_health=_aggregate_health(snapshot.spread_health for snapshot in snapshots.values()),
        latency_health=_aggregate_health(snapshot.latency_health for snapshot in snapshots.values()),
        symbol_cooldown_until_utc={},
    ),
)
```

```python
for signal in selected_signals:
    risk = evaluate_risk(...)
    if risk.action not in {RiskAction.ENTER, RiskAction.SCALE, RiskAction.PYRAMID} or risk.sized_volume <= 0.0:
        self._emit_telemetry("execution_intent_rejected", ...)
        continue

    validation = validate_execution_intent(...)
    if not validation.accepted or validation.intent is None:
        self._emit_telemetry("execution_intent_rejected", ...)
        continue

    result = self.execution_adapter.execute(validation.intent, at_utc=broker_time)
```

## 6. Current Observability Gap

What is visible today:
- aggregate `runtime_cycle` telemetry
- `selected_signal_count`
- `execution_count`
- final `selected_symbols`
- signal-layer accept/reject in-memory during runtime

What is not visible today:
- why a specific accepted signal was removed by portfolio selection
- which selection filter fired first
- whether a candidate was rejected by expiry, cooldown, health, correlation, replacement, or capacity

WO-037 DB evidence shows:
- `signal_count = 1`
- `selected_signal_count = 0`
- `selected_symbols = []`
- `execution_count = 0`

But the database does not tell us why the candidate was not selected.

## 7. Design Goals

1. Explain why an accepted signal did not become a selected signal.
2. Keep `rank_opportunities()` behavior unchanged.
3. Keep selection logic deterministic and short-circuiting.
4. Preserve `runtime_cycle` as an aggregate event.
5. Add minimal observability without changing trade behavior.
6. Avoid a new DB schema.
7. Keep telemetry persistence on the existing `topic + payload_json` mechanism.

## 8. Explicit Non-Goals

The following are out of scope for WO-040:
- changing ranking order
- changing expiry semantics
- changing cooldown semantics
- changing health gating
- changing correlation budget behavior
- changing replacement behavior
- changing duplicate-symbol behavior
- changing `MAX_CONCURRENT_POSITIONS`
- changing risk logic
- changing execution validation
- changing execution adapter behavior
- changing M1 or M5 market data behavior
- changing snapshot readiness behavior
- changing `_closed_bars()`

## 9. Instrumentation Location Decision

Recommended design:
- keep telemetry emission in the runtime layer
- keep `portfolio.py` free of telemetry side effects
- if a helper is needed to compute reasoned selection traces, keep it pure and return trace data only

Why not put telemetry directly in `portfolio.py`?
- `rank_opportunities()` is a selection function, not a persistence function
- it currently returns only selected signals
- adding telemetry emission there would mix selection policy with storage concerns
- runtime already has the cycle context needed to correlate telemetry with `runtime_cycle`

Can this be done without changing `rank_opportunities()` behavior?
- Yes, behavior can remain unchanged
- The runtime can emit candidate-level telemetry after obtaining selection trace data
- If a pure helper is introduced to compute the trace, it should not alter the selected list

Recommended implementation boundary for the next patch:
- runtime emits telemetry
- selection logic stays deterministic
- any helper that computes rejection reasons must remain pure

## 10. Telemetry Topic and Payload Design

Recommended topic:

```text
runtime.portfolio_selection
```

Reason:
- it matches the existing runtime-style topic namespace
- it clearly belongs to the live runtime cycle
- it is stable enough for downstream analysis

Minimum payload:
- `cycle_time_utc`
- `event_time_utc` is already written by persistence
- `symbol`
- `setup_id`
- `score`
- `threshold`
- `selected`
- `first_rejection_reason`

Recommended optional context fields:
- `broker_time_utc`
- `signal_key`
- `candidate_rank`
- `active_positions_count`
- `selected_count_before_candidate`
- `execution_health`
- `spread_health`
- `latency_health`

Selected candidate payload:
- `selected = true`
- `first_rejection_reason = null` or absent

Rejected candidate payload:
- `selected = false`
- `first_rejection_reason = <stable enum value>`

The design should not rely on `event_time_utc` alone for cycle correlation. `cycle_time_utc` should be stored explicitly in payload.

## 11. Stable Rejection Reason Enum

The actual source order in `rank_opportunities()` is short-circuit and should drive the reason enum order:

1. `CAPACITY_LIMIT_REACHED`
2. `EXPIRED`
3. `SYMBOL_COOLDOWN`
4. `DUPLICATE_SYMBOL`
5. `EXECUTION_HEALTH`
6. `SPREAD_HEALTH`
7. `LATENCY_HEALTH`
8. `CORRELATION_BUDGET`
9. `REPLACEMENT_NOT_ALLOWED`
10. `SELECTED` is not a rejection reason; it is represented by `selected = true`

Why `CAPACITY_LIMIT_REACHED` is required:
- `rank_opportunities()` stops iterating once `len(selected) >= MAX_CONCURRENT_POSITIONS`
- candidates beyond capacity are not evaluated by later filters
- without an explicit capacity reason, those candidates would remain ambiguous

Why `SELECTED` is not a rejection reason:
- the payload already has a boolean `selected`
- a selected candidate should not be forced into a rejection enum

## 12. First-Rejection Semantics

The current implementation is short-circuit by design:
- each candidate is checked in a fixed order
- the first failing branch stops selection for that candidate
- later hypothetical failures are not computed

Therefore the telemetry should record:
- `selected = false`
- only the first actual rejection reason

Example:
- if a candidate fails expiry, telemetry should say `first_rejection_reason = EXPIRED`
- telemetry should not also list correlation or replacement as additional reasons for that same candidate

## 13. Runtime Correlation / Cycle Association

Current runtime correlation options:
- `cycle_time_utc` from the runtime cycle
- `symbol`
- `setup_id`

What is not available today:
- a dedicated cycle identifier in telemetry

Recommendation:
- use `cycle_time_utc` as the primary cycle join key
- use `setup_id` + `symbol` as candidate identity
- do not claim perfect correlation from `event_time_utc` alone

Risk note:
- `event_time_utc` is write time from persistence, not the market cycle time
- there can be slight timing differences between cycle execution and telemetry write time

## 14. Test Plan

The design-only review implies the following patch validation plan later.

| Scenario | Expected selection result | Expected telemetry | Expected reason |
| --- | --- | --- | --- |
| expired candidate | rejected | one `runtime.portfolio_selection` event | `EXPIRED` |
| cooldown active | rejected | one event | `SYMBOL_COOLDOWN` |
| duplicate symbol in batch | rejected | one event | `DUPLICATE_SYMBOL` |
| execution health not green | rejected | one event | `EXECUTION_HEALTH` |
| spread health not green | rejected | one event | `SPREAD_HEALTH` |
| latency health not green | rejected | one event | `LATENCY_HEALTH` |
| correlation budget exhausted | rejected | one event | `CORRELATION_BUDGET` |
| replacement not allowed | rejected | one event | `REPLACEMENT_NOT_ALLOWED` |
| candidate selected | selected | one event | null / absent |
| multiple candidates, mixed outcome | mixed | one event per candidate | per candidate first reason |
| first rejection semantics | rejected on first failing branch | no extra reasons | first branch only |
| selection invariance | same selected output | telemetry only | no change |
| ranking order unchanged | same order | telemetry only | no change |
| persistence through telemetry_index | events stored | `topic + payload_json` | as above |

## 15. Expected Implementation Scope

Likely production files for the next patch:
- `tsp_v2/live_runtime.py`
- possibly a pure selection-trace helper in `tsp_v2/portfolio.py` if we choose to avoid duplicating selection filters in runtime

Likely test files:
- `tsp_v2/tests/test_portfolio.py`
- `tsp_v2/tests/test_deployment.py`

Files that should not need changes for this observability patch:
- `tsp_v2/risk.py`
- `tsp_v2/execution.py`
- M1/M5 snapshot logic
- `_closed_bars()`
- persistence schema

Documentation file:
- this design note only

## 16. Regression Boundary

The next implementation must not change:
- ranking order
- expiry semantics
- cooldown semantics
- health gating
- correlation budget
- replacement rules
- duplicate-symbol behavior
- `MAX_CONCURRENT_POSITIONS`
- risk logic
- execution validation
- execution adapter behavior
- M1 market-data behavior
- M5 market-data behavior
- snapshot readiness contract
- `_closed_bars()` behavior

WO-040 must remain observability-only.

## 17. Decision / Recommendation

Decision:
- **patch justified**

Reason:
- WO-039 proved the gap is real
- the workspace currently cannot explain why `selected_signal_count=0`
- `rank_opportunities()` short-circuits and returns only selected signals
- current telemetry does not preserve candidate-level rejection reasons

Recommended observability pattern for the patch that follows:
- emit one candidate-level selection telemetry event per accepted signal entering portfolio selection
- keep runtime aggregate telemetry unchanged
- keep `rank_opportunities()` behavior unchanged
- record the first rejection reason only

This review does not change selection behavior, risk behavior, execution behavior, or market-data behavior.
