# MA5 - Execution Evidence

## Metadata

- Status: Closed
- Objective type: Evidence summary
- Evidence baseline: 2026-07-09
- Related docs:
  - `docs/CONTEST_READINESS_CHECKLIST.md`
  - `docs/BLUEPRINT_COMPLIANCE_REVIEW.md`
  - `docs/IMPLEMENTATION_ARCHITECTURE_MAPPING_V2.md`
  - `docs/EVIDENCE_REGISTER.md`

## Objective

Mendokumentasikan evidence yang tersedia untuk jalur eksekusi order:

```text
MarketSnapshot
  -> Signal generation
  -> Decision
  -> Order request
  -> Broker response
  -> execution_filled / execution_rejected
  -> Position management
  -> Persistence
  -> Telemetry
```

Dokumen ini tidak membahas startup readiness, snapshot policy, atau runtime policy.

---

## Execution Contract

Berdasarkan blueprint dan implementasi yang sudah dibekukan, jalur eksekusi yang relevan adalah:

- sinyal menghasilkan keputusan eksekusi
- keputusan eksekusi menghasilkan order request dengan `submission_uuid`
- broker response harus tercatat sebagai event eksekusi
- outcome eksekusi harus bisa diklasifikasikan sebagai `filled` atau `rejected`
- hasil eksekusi harus dipersist dan bisa direkonsiliasi terhadap broker truth
- seluruh lifecycle harus dapat diaudit dari telemetry dan persistence

Kontrak ini menuntut bahwa:

- broker response adalah evidence utama untuk outcome order
- persistence tidak boleh mengaburkan outcome broker
- telemetry dan registry harus konsisten satu sama lain

---

## Filled Cases

### Case F-01

- Source: `validation/V2-V01/telemetry/20260529T122458Z_evidence.json`
- Outcome: `execution_filled`
- `submission_uuid`: `269dcb3d055185792361fc71`
- `symbol`: `XAUUSD`
- `broker_code`: `DONE`
- `classification`: `OK`
- `ticket`: `1`
- `accepted`: `true`
- `filled`: `true`
- `rejected`: `false`
- `partial_fill`: `false`

Telemetry context:

- `execution_count = 1`
- `reconciliation_ready = false`
- `market_health = GREEN`
- `feed_health = GREEN`

Persistence mapping:

- `execution_events = 1`
- `execution_registry = 1`
- registry entry persisted for the submission

### Case F-02

- Source: `validation/V2-V01/telemetry/20260529T122635Z_evidence.json`
- Outcome: `execution_filled`
- `submission_uuid`: `863a7462bfb2640f51e885c1`
- `symbol`: `XAUUSD`
- `broker_code`: `DONE`
- `classification`: `OK`
- `ticket`: `1`
- `accepted`: `true`
- `filled`: `true`
- `rejected`: `false`
- `partial_fill`: `false`

Telemetry context:

- `execution_count = 1`
- `reconciliation_ready = true`
- `market_health = GREEN`
- `feed_health = GREEN`

Persistence mapping:

- `execution_events = 1`
- `execution_registry = 1`
- registry entry persisted for the submission

---

## Rejected Cases

### Case R-01

- Source: `runtime/forward_live/db/tsp_v2_runtime.sqlite3`
- Telemetry event id: `697`
- Outcome: `execution_rejected`
- `submission_uuid`: `ef1aaabcc453c1a38718d6f5`
- `symbol`: `GBPUSD`
- `broker_code`: `API_HUNG`
- `classification`: `ESCALATE_KILL_REVIEW`
- `accepted`: `false`
- `filled`: `false`
- `rejected`: `true`
- `partial_fill`: `false`
- `ticket`: `null`

Persistence mapping:

- `execution_registry.state = EXPIRED`
- `direction = LONG`
- `decision_price = 1.32385`
- `cycle_time_utc = 2026-06-19T12:57:47+00:00`
- `expires_at_utc = 2026-06-19T12:59:17+00:00`
- `broker_ticket = null`

Telemetry / persistence interpretation:

- broker response was rejected
- registry entry was persisted
- no broker ticket was assigned

### Case R-02

- Source: `runtime/forward_live/db/tsp_v2_runtime.sqlite3`
- Telemetry event id: `844`
- Outcome: `execution_rejected`
- `submission_uuid`: `bb85e1cffcb617af640bb04b`
- `symbol`: `GBPUSD`
- `broker_code`: `API_HUNG`
- `classification`: `ESCALATE_KILL_REVIEW`
- `accepted`: `false`
- `filled`: `false`
- `rejected`: `true`
- `partial_fill`: `false`
- `ticket`: `null`

Persistence mapping:

- `execution_registry.state = EXPIRED`
- `direction = LONG`
- `decision_price = 1.32265`
- `cycle_time_utc = 2026-06-19T13:30:04+00:00`
- `expires_at_utc = 2026-06-19T13:32:04+00:00`
- `broker_ticket = null`

Telemetry / persistence interpretation:

- broker response was rejected
- registry entry was persisted
- no broker ticket was assigned

---

## Broker Response

Observed broker response patterns in the available evidence:

- `DONE` for filled cases
- `API_HUNG` for rejected cases

The evidence supports that execution outcome is represented both in telemetry and in registry state.

For filled cases:

- broker response is terminal `filled`
- ticket is assigned
- registry row exists

For rejected cases:

- broker response is terminal `rejected`
- ticket remains null
- registry row is marked expired

---

## Telemetry Mapping

### Filled telemetry

Evidence source:

- `validation/V2-V01/telemetry/20260529T122458Z_evidence.json`
- `validation/V2-V01/telemetry/20260529T122635Z_evidence.json`

Relevant telemetry topics:

- `execution_filled`
- `reconciliation_started`
- `reconciliation_completed`
- `runtime_started`
- `runtime_recovered`
- `runtime_cycle`
- `runtime_stopped`

### Rejected telemetry

Evidence source:

- `runtime/forward_live/db/tsp_v2_runtime.sqlite3`

Relevant telemetry topic:

- `execution_rejected`

Operational meaning:

- the rejection is observable from telemetry
- the outcome is not silent
- the registry can be correlated by `submission_uuid`

---

## Persistence Mapping

The available evidence shows that execution outcomes are reflected in persistence:

- `execution_events` captures the execution event outcome
- `execution_registry` preserves lifecycle state for the submission
- rejected submissions are persisted as `EXPIRED` with no broker ticket
- filled submissions are persisted with a broker ticket and a terminal filled event

This means the order lifecycle is auditable across:

- telemetry
- registry state
- broker outcome classification

---

## Verdict

### What the evidence confirms

- Order-request evidence exists in the form of `submission_uuid` and registry rows
- Broker outcomes are observable as both `filled` and `rejected`
- Persistence is aligned with the observed broker outcome
- The lifecycle is auditable from event to registry

### What is still incomplete

- The latest forward-live build does not yet provide a fresh `execution_filled` or `execution_rejected` sample in the same envelope as the most recent runtime characterization work
- This document does not claim that MA5 is operationally complete for contest readiness
- This document does not change runtime policy, startup behavior, or snapshot policy

### Final judgment

**MA5 execution lifecycle is evidenced, but the contest envelope remains incomplete.**

The available evidence is sufficient to show that the pipeline can produce both filled and rejected outcomes and persist them correctly. It is not sufficient to claim that the latest build has exhausted the full execution envelope needed for contest readiness.
