# WO-041 Portfolio Selection Observability Implementation

## 1. Baseline

Git reference at start of WO-041:

```text
88d38665a907f77a1be47ac153c2d1c267646677
```

This note is based on the current workspace contents on disk. `HEAD` is only a Git reference point.

## 2. Workspace Authority

Workspace authority:
- The files currently present in `D:\Maya\Scalper` are the authority for this implementation review.
- Where the workspace differs from `HEAD`, the workspace contents are the source of truth for analysis.
- No reset, restore, clean, checkout, or stash operations were used.

Pre-existing workspace state observed before WO-041 changes:
- `M runtime/forward_live/db/tsp_v2_runtime.sqlite3`
- `?? docs/WO_035_M5_WINDOW_DESIGN_REVIEW.md`
- `?? docs/WO_038_EXECUTION_LIFECYCLE_AUDIT.md`
- `?? docs/WO_039_PORTFOLIO_SELECTION_ELIGIBILITY_AUDIT.md`
- `?? docs/WO_040_PORTFOLIO_SELECTION_OBSERVABILITY_DESIGN.md`
- `?? runtime/forward_live/db/tsp_v2_runtime.sqlite3-shm`
- `?? runtime/forward_live/db/tsp_v2_runtime.sqlite3-wal`
- `?? runtime/forward_live/locks/`

## 3. Pre-Patch Repository State

Repository state before the WO-041 implementation patch:

```text
git rev-parse HEAD = 88d38665a907f77a1be47ac153c2d1c267646677
git diff --check   = clean
git status --short = dirty pre-existing runtime/doc artifacts only
```

No production code was intentionally changed before the WO-041 implementation patch.

## 4. Source Verification

Source files audited from the workspace:
- `tsp_v2/portfolio.py`
- `tsp_v2/live_runtime.py`
- `tsp_v2/persistence.py`
- `tsp_v2/tests/test_portfolio.py`
- `tsp_v2/tests/test_deployment.py`
- supporting model definitions in `tsp_v2/models.py`

Verified source facts:
- `rank_opportunities()` is short-circuiting and pure.
- The selection branch order in `portfolio.py` is:
  - capacity limit
  - expiry
  - cooldown
  - duplicate symbol
  - execution health
  - spread health
  - latency health
  - correlation budget
  - replacement eligibility
- `live_runtime.py` builds `accepted_signals`, then calls portfolio selection, then computes governor/risk/execution.
- `persistence.py` already stores telemetry via `topic + payload_json` into `telemetry_index`.

## 5. Implementation Approach

Chosen implementation:
- add a pure selection-trace helper in `tsp_v2/portfolio.py`
- keep `rank_opportunities()` behavior unchanged by delegating to that helper
- emit candidate-level telemetry from the runtime layer in `tsp_v2/live_runtime.py`
- persist telemetry through the existing `store_telemetry_index(topic, payload)` path

Why this approach:
- it avoids duplicating selection logic in `live_runtime.py`
- it keeps `rank_opportunities()` pure
- it preserves the existing selection result exactly
- it adds observability without a new schema

## 6. Selection Telemetry Design

Telemetry topic:

```text
runtime.portfolio_selection
```

Minimum payload fields:
- `cycle_time_utc`
- `symbol`
- `setup_id`
- `score`
- `threshold`
- `selected`
- `first_rejection_reason`

Additional field used in the implementation:
- `candidate_rank`

Payload semantics:
- selected candidate:
  - `selected = true`
  - `first_rejection_reason = null`
- rejected candidate:
  - `selected = false`
  - `first_rejection_reason = <stable string reason>`

The runtime still emits aggregate `runtime_cycle` telemetry separately.

## 7. Reason Mapping

Final rejection reasons used by the workspace implementation:

1. `CAPACITY_LIMIT_REACHED`
2. `EXPIRED`
3. `SYMBOL_COOLDOWN`
4. `DUPLICATE_SYMBOL`
5. `EXECUTION_HEALTH`
6. `SPREAD_HEALTH`
7. `LATENCY_HEALTH`
8. `CORRELATION_BUDGET`
9. `REPLACEMENT_NOT_ALLOWED`

Notes:
- `CAPACITY_LIMIT_REACHED` is required because candidates after `MAX_CONCURRENT_POSITIONS` are not evaluated further.
- `SELECTED` is not a rejection reason; success is represented by `selected = true` and `first_rejection_reason = null`.

## 8. Files Changed

Files changed by the WO-041 implementation:
- `tsp_v2/portfolio.py`
- `tsp_v2/live_runtime.py`
- `tsp_v2/tests/test_portfolio.py`
- `tsp_v2/tests/test_deployment.py`

Documentation file created:
- `docs/WO_041_PORTFOLIO_SELECTION_OBSERVABILITY_IMPLEMENTATION.md`

No schema migration files were changed.

## 9. Tests Added / Changed

Updated test coverage:
- `tsp_v2/tests/test_portfolio.py`
  - trace helper coverage for all short-circuit reasons
  - selection invariance checks against `rank_opportunities()`
- `tsp_v2/tests/test_deployment.py`
  - runtime telemetry emission for `runtime.portfolio_selection`
  - persistence check through `telemetry_index`

## 10. Regression Test Results

Targeted tests:

```text
python -m unittest tsp_v2.tests.test_portfolio
python -m unittest tsp_v2.tests.test_deployment
```

Results:
- PASS

Baseline regression suites:

```text
python -m unittest tsp_v2.tests.test_mt5_bridge tsp_v2.tests.test_market_adapter tsp_v2.tests.test_snapshots
python -m unittest tsp_v2.tests.test_deployment
python -m unittest tsp_v2.tests.test_signals tsp_v2.tests.test_risk tsp_v2.tests.test_execution tsp_v2.tests.test_portfolio tsp_v2.tests.test_telemetry
```

Results:
- PASS

`git diff --check`:
- PASS

## 11. Post-Patch Git State

Observed after the WO-041 implementation patch and validation:

```text
git rev-parse HEAD = 88d38665a907f77a1be47ac153c2d1c267646677
git diff --check   = clean
git status --short = dirty pre-existing runtime/doc artifacts plus WO-041 source/doc changes
```

The runtime DB artifact remains dirty and was not staged or committed.

## 12. Behavior Invariance Assessment

Assessment:
- selection logic changed: NO
- ranking changed: NO
- filter behavior changed: NO
- risk behavior changed: NO
- execution behavior changed: NO
- market-data behavior changed: NO

Reasoning:
- `rank_opportunities()` still returns the same selected signals for the same inputs
- the new helper only exposes the already-existing short-circuit outcome as trace data
- telemetry emission happens in the runtime layer, after selection is computed

## 13. Decision

Decision:
- WO-041 implementation is complete
- observability for portfolio selection is now available without changing portfolio selection semantics

Observed runtime telemetry shape:

```text
runtime.portfolio_selection
  cycle_time_utc
  candidate_rank
  symbol
  setup_id
  score
  threshold
  selected
  first_rejection_reason
```

This gives the runtime a deterministic explanation for accepted signals that do not become selected signals.
