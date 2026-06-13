# IMPLEMENTATION_ARCHITECTURE_MAPPING_V2.md

Status: Production-Grade Derivation Document
Depends on: [FINAL_BLUEPRINT V2.md](D:\Maya\Scalper\FINAL_BLUEPRINT%20V2.md)
Authority Level: Implementation Contract

## 1. Purpose

Dokumen ini adalah turunan implementasi dari blueprint V2.

Ia bukan redesign strategi.

Ia bukan override terhadap blueprint.

Ia adalah jembatan resmi:

```text
blueprint V2
-> package structure
-> module contracts
-> state contracts
-> persistence schema
-> runtime ordering
-> deployment behavior
```

Jika terjadi konflik:

```text
blueprint architecture wins
implementation mapping must be revised
```

## 2. Implementation Doctrine

Official implementation stance:

- preserve architecture
- minimize interpretation drift
- maximize deterministic behavior
- keep single-owner mutation discipline
- keep broker truth authoritative
- keep recovery idempotent
- keep observability first-class

This mapping assumes:

- existing `tsp/` package remains V1 baseline
- V2 implementation is developed in a separate package
- no silent in-place mutation of V1 engine
- cutover occurs only after V2 validation gates pass

## 3. Package Strategy

### 3.1 Official Package

V2 implementation package:

```text
tsp_v2/
```

Rationale:

- avoids collision with `tsp/` V1
- preserves rollback path
- enables side-by-side validation
- prevents accidental mixed-runtime imports

### 3.2 Proposed Tree

```text
tsp_v2/
  __init__.py
  app.py
  orchestrator.py
  config.py
  config_schema.py
  enums.py
  models.py
  clock.py
  sessions.py
  news.py
  market_data.py
  snapshots.py
  indicators.py
  regime.py
  signals.py
  risk.py
  governor.py
  portfolio.py
  execution.py
  lifecycle.py
  persistence.py
  telemetry.py
  deployment.py
  adapters/
    __init__.py
    mt5_bridge.py
    market_adapter.py
    execution_adapter.py
    news_provider.py
  recovery/
    __init__.py
    bootstrap.py
    reconcile.py
    idempotency.py
  schemas/
    config/
    sql/
  tests/
    ...
```

### 3.3 Module Separation Rule

Hard rule:

- `orchestrator.py` owns final runtime mutation
- strategy modules return pure outputs
- adapters talk to MT5 or external inputs
- persistence never authorizes live trading
- telemetry never influences trading decisions directly

## 4. Canonical Runtime Model

### 4.1 Loop Model

Official runtime model:

```text
single orchestrator loop
poll every 2-5 seconds
process only on new closed M5 bar
```

### 4.2 Loop Sequence

Per loop iteration:

1. read authoritative broker time
2. build immutable market snapshot set
3. reject duplicate M5 bar
4. refresh broker/account/position truth
5. reconcile anomalies if any
6. classify regime
7. score signals
8. evaluate governor
9. evaluate risk
10. evaluate portfolio competition
11. evaluate execution gates
12. submit execution if allowed
13. reconcile fills and partial fills
14. evaluate lifecycle updates
15. flush critical persistence
16. emit telemetry
17. mark last processed closed M5 bar

### 4.3 Loop Guarantees

Each processed bar must satisfy:

- one immutable snapshot per symbol per cycle
- one authoritative clock per cycle
- one final commit path
- one persistence write transaction owner
- one telemetry correlation root

## 5. Clock, Time, and Session Authority

### 5.1 Clock Source

Canonical clock:

```text
broker server time normalized to UTC
```

Implementation owner:

`clock.py`

### 5.2 Clock Sanity Thresholds

Preflight / runtime thresholds:

- local vs broker skew warning: `> 60s`
- local vs broker skew soft fail: `> 180s`
- local vs broker skew hard fail: `> 300s`
- intra-session broker time jump warning: `> 5s backward`
- intra-session broker time hard anomaly: `> 30s backward`

Action doctrine:

- warning only: telemetry + degraded health
- soft fail: symbol execution block
- hard fail: startup fail or kill review escalation

### 5.3 Session Classifier

Implementation owner:

`sessions.py`

Inputs:

- UTC-normalized broker time
- governed session windows

Output:

- `LONDON`
- `LONDON_NY`
- `EARLY_NY`
- `LATE_NY`
- `ASIA`
- `DEAD`

## 6. Market Snapshot Semantics

### 6.1 Snapshot Builder Owner

Implementation owner:

`snapshots.py`

### 6.2 Atomic Snapshot Doctrine

Each cycle begins by capturing:

- `cycle_time_utc`
- latest bid/ask tick
- closed bars for H1, M15, M5, M1
- symbol contract
- broker health metadata
- news provider state

Then snapshot builder computes all downstream metrics from this frozen input set only.

Forbidden:

- fetching H1 now and M5 later inside same decision path
- indicator recalculation from a refreshed rate set mid-cycle
- using current forming bar in one layer and closed bar in another

### 6.3 Closed-Bar Semantics

For timeframe `tf`, last usable bar is:

```text
max(bar.close_time <= cycle_time_utc_floor_for_tf)
```

Processing anchor:

```text
new closed M5 bar only
```

M1 is refinement input only, not independent trigger clock.

### 6.4 Snapshot Structure

Mandatory fields:

- `cycle_time_utc`
- `symbol`
- `tick_bid`
- `tick_ask`
- `spread_points`
- `spread_ratio`
- `session`
- `news_state`
- `news_lockout_active`
- `contract_snapshot`
- `bars_h1`
- `bars_m15`
- `bars_m5`
- `bars_m1`
- `indicator_bundle`
- `feed_health`

## 7. News Integration Contract

### 7.1 Provider Modes

Provider implementations:

- `StaticFileNewsProvider`
- `CalendarSnapshotNewsProvider`
- `DisabledDiagnosticNewsProvider`

### 7.2 Freshness Thresholds

News source staleness:

- file/calendar snapshot age warning: `> 15 minutes`
- stale degraded: `> 30 minutes`
- unusable hard fail in FORWARD/CONTEST: `> 60 minutes`

If next relevant event time is inside lockout horizon and provider is stale:

```text
block offensive deployment
```

### 7.3 Provider Output Contract

Mandatory outputs:

- `provider_mode`
- `provider_state`
- `snapshot_generated_at_utc`
- `relevant_events`
- `lockout_active`
- `next_relevant_event_utc`

## 8. Regime / Signal / Risk Contract Chain

### 8.1 Regime Contract

Implementation owner:

`regime.py`

Input:

- immutable snapshot

Output:

- `regime`
- `confidence`
- `direction_bias`
- `raw_scores`
- `diagnostics`

### 8.2 Signal Contract

Implementation owner:

`signals.py`

Output:

- `setup_id`
- `signal_family`
- `symbol`
- `direction`
- `score`
- `threshold`
- `expires_at_utc`
- `rationale`
- `lineage`

### 8.3 Risk Contract

Implementation owner:

`risk.py`

Output:

- `action`
- `risk_multiplier`
- `sized_volume`
- `invalidation_price`
- `hard_block_reason`
- `governor_adjusted_state`

### 8.4 Governor Contract

Implementation owner:

`governor.py`

Output:

- `state`
- `state_reason`
- `pace_classification`
- `aggression_multiplier`
- `profile_constraints`
- `escalation_flags`

### 8.5 Portfolio Contract

Implementation owner:

`portfolio.py`

Responsibilities:

- rank multi-symbol opportunities
- enforce correlation caps
- prevent third concurrent position
- apply replacement doctrine

## 9. Governor Pace Curve Contract

### 9.1 Owner

Implementation owner:

`governor.py`

### 9.2 Pace Curve Definition

Pace curve is profile-specific and static-governed.

Official implementation shape:

piecewise linear curve in R-space

Inputs:

- `contest_progress` in `[0.0, 1.0]`
- active profile

Outputs:

- `expected_pnl_r_at_t`

### 9.3 Initial Pace Curves

`FORWARD_SAFE`

- 0.00 -> 0.0R
- 0.25 -> 0.5R
- 0.50 -> 1.0R
- 0.75 -> 1.5R
- 1.00 -> 2.0R

`CONTEST_BALANCED`

- 0.00 -> 0.0R
- 0.25 -> 0.8R
- 0.50 -> 1.8R
- 0.75 -> 3.2R
- 1.00 -> 5.0R

`CONTEST_HUNTER`

- 0.00 -> 0.0R
- 0.25 -> 1.0R
- 0.50 -> 2.4R
- 0.75 -> 4.5R
- 1.00 -> 7.0R

`FINAL_SPRINT`

- not valid as startup base curve
- derived only after governed late-stage transition

### 9.4 Governance

Pace curve changes are:

```text
Contest Optimization Request
```

not ad-hoc config tweaks.

## 10. Execution and Idempotency Contract

### 10.1 Execution Module Owner

Implementation owner:

`execution.py`

### 10.2 Submission Identity

Every execution attempt must bind:

- `setup_id`
- `symbol`
- `direction`
- `decision_price`
- `submission_uuid`
- `cycle_time_utc`

### 10.3 Execution Registry

Persistence-backed registry states:

- `PENDING`
- `ACKNOWLEDGED`
- `PARTIAL`
- `FILLED`
- `REJECTED`
- `AMBIGUOUS`
- `CANCELLED`
- `EXPIRED`

### 10.4 Symbol Lock

Lock owner:

`execution.py`

Duration baseline:

`15 seconds`

Lock release conditions:

- resolved broker outcome
- deterministic failure
- explicit ambiguity escalation

## 11. Lifecycle Contract

### 11.1 Owner

Implementation owner:

`lifecycle.py`

### 11.2 Lifecycle State

Mandatory fields:

- `position_ticket`
- `setup_id`
- `symbol`
- `opened_at_utc`
- `direction`
- `entry_price`
- `initial_stop`
- `initial_r_distance`
- `current_stop`
- `partial_taken`
- `trail_active`
- `pyramid_count`
- `thesis_expiry_utc`
- `orphan_recovered`

### 11.3 Lifecycle Mutation Rule

Lifecycle module may propose:

- stop move
- partial close
- trail activation
- time-stop exit
- failed-thesis exit

Only orchestrator commits resulting authoritative state.

## 12. Persistence Contract

### 12.1 Writer Model

Official doctrine:

```text
single writer SQLite model
```

Meaning:

- orchestrator owns all write transactions
- telemetry must not write directly to SQLite runtime DB
- operator tooling must be read-only against live DB
- auxiliary writers require separate artifact store, not live runtime DB

This closes `database is locked` drift by architecture.

### 12.2 Physical Layout

Recommended runtime DB:

```text
runtime/db/tsp_v2_runtime.sqlite3
```

SQL schema folder:

```text
tsp_v2/schemas/sql/
```

### 12.3 Minimum Tables

- `persistence_meta`
- `config_fingerprint`
- `runtime_state`
- `governor_state`
- `account_state`
- `execution_registry`
- `execution_events`
- `positions`
- `lifecycle_state`
- `health_state`
- `recovery_events`
- `telemetry_index`

### 12.4 Transaction Boundaries

Mandatory immediate flush events:

- order acknowledged
- partial fill
- fill complete
- emergency flatten
- KILL_REVIEW transition
- stop movement
- orphan reconstruction

### 12.5 Restart Idempotency Ordering

Authoritative restart order:

1. validate runtime lock ownership
2. validate DB integrity
3. validate schema version
4. validate config fingerprint
5. connect MT5 bridge
6. query authoritative account state
7. query authoritative live positions
8. restore persisted registry
9. reconcile registry against broker truth
10. restore lifecycle state
11. reconstruct recoverable positions
12. flatten unresolved ambiguous exposure
13. emit recovery telemetry
14. resume only after healthy snapshot build

No shortcut re-entry before reconciliation completes.

## 13. Deployment Contract

### 13.1 Deployment Owners

- `deployment.py` for Python-side launcher logic
- `deploy/` for operator-facing scripts

### 13.2 Startup Guardrails

Preflight must validate:

- single-instance lock
- writable runtime paths
- MT5 bridge availability
- broker login state
- supported symbol universe
- contract sanity
- news provider readiness
- broker/local clock sanity
- DB schema compatibility
- config fingerprint compatibility

### 13.3 Artifact Layout

Recommended layout:

```text
runtime/
  db/
  locks/
  state/
logs/
reports/
backtests/
```

## 14. Test Mapping

### 14.1 Package Tests

Required V2 test families:

- `test_config_schema.py`
- `test_clock.py`
- `test_sessions.py`
- `test_news_provider.py`
- `test_snapshots.py`
- `test_regime.py`
- `test_signals.py`
- `test_risk.py`
- `test_governor.py`
- `test_portfolio.py`
- `test_execution.py`
- `test_lifecycle.py`
- `test_persistence.py`
- `test_recovery.py`
- `test_deployment.py`
- `test_orchestrator.py`

### 14.2 High-Risk Scenarios

Mandatory scenario tests:

- broker clock offset normalization
- stale news provider block
- duplicate setup submission suppression
- partial fill recovery
- restart after fill before lifecycle flush
- restart after ambiguous execution
- MT5 bridge hung timeout
- symbol lock contention
- portfolio correlation veto
- KILL_REVIEW transition ownership

## 15. Implementation Sequence

Recommended build order:

1. enums/models/config schema
2. clock/sessions/news
3. market snapshot builder
4. regime
5. signals
6. governor
7. risk
8. portfolio
9. execution
10. lifecycle
11. persistence
12. recovery bootstrap
13. orchestrator
14. deployment
15. validation harness

Reason:

minimize downstream churn and keep foundational contracts stable first.

## 16. Patch Classification for V2 Build

All work from this mapping should be classified only as:

- Implementation Interpretation
- Bug Fix Deviation

Unless strategy mathematics or tournament doctrine is changed explicitly.

## 17. Immediate Next Deliverable

The next concrete engineering artifact after this document should be:

```text
V2 package scaffold + interface stubs
```

not direct full-feature coding in one step.

That means:

- package tree
- empty module files
- canonical enums
- typed model contracts
- adapter protocols
- schema placeholders
- test harness skeleton

This is the correct production-grade starting point for implementation.
