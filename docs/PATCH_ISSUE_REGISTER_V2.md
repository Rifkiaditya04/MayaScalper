# PATCH Issue Register V2

Dokumen ini memegang rolling governance closure model untuk implementasi `tsp_v2/`.

Severity policy:

- `P0` blocker: wajib ditutup sebelum patch berikutnya
- `P1` high: boleh carry singkat dengan tracking eksplisit, wajib ditutup segera
- `P2` medium: boleh masuk hardening backlog
- `P3` low: boleh defer

## Register

| Issue ID | Patch Origin | Issue | Severity | Status | Owner | Closure Patch | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| V2-001 | PATCH-001 | Environment override coercion could be misread as silent bad input risk | P1 | CLOSED | Codex | PATCH-002A | Explicit fail-loud tests added for invalid env override values |
| V2-002 | PATCH-001 | Fingerprint canonicalization proof required before deeper persistence/snapshot work | P1 | CLOSED | Codex | PATCH-002A | Canonicalization helper and deterministic ordering proof tests added |
| V2-003 | PATCH-002 | Session enum naming drift `OVERLAP` vs blueprint canonical `LONDON_NY` | P1 | CLOSED | Codex | PATCH-002A | Enum and tests renamed before snapshot embedding work |
| V2-004 | PATCH-001 | Custom YAML subset parser has maintenance risk vs battle-tested loader | P2 | CLOSED | Codex | PATCH-011A | Custom parser retained by design for controlled YAML subset; strict schema validation and malformed YAML rejection coverage confirm risk is contained |
| V2-005 | PATCH-003 | Snapshot timestamp monotonicity proof still missing for reconnect/regression anomalies | P1 | CLOSED | Codex | PATCH-005 | Monotonicity guard added to snapshot builder with regression test coverage |
| V2-006 | PATCH-003 | Partial payload degradation policy not yet explicit for mixed-good/mixed-missing timeframe inputs | P2 | CLOSED | Codex | PATCH-007A | Snapshot payload health now classifies GREEN/YELLOW/RED explicitly; thin payloads are allowed only when above minimum and partial payloads below minimum fail loud |
| V2-007 | PATCH-003 | Spread anomaly classification thresholds need explicit confirmation or formalization | P1 | CLOSED | Codex | PATCH-005 | Canonical spread health now emitted in snapshot contract and consumed by signal gating |
| V2-008 | PATCH-004 | MICRO latency gate ambiguity between feed health and explicit latency doctrine | P1 | CLOSED | Codex | PATCH-005 | Explicit `latency_health` snapshot field added and MICRO signal rejects any non-GREEN latency |
| DR-001 | RV1 Campaign (2026-07) | Broker reconciliation counts `EXPIRED` execution_registry entries with `broker_ticket = NULL` as `MISSING_BROKER`, keeping `ready_to_resume` permanently `false` | P1 | OPEN | - | - | See `docs/DR_001_BROKER_RECONCILIATION_MISSING_BROKER_MISCLASSIFICATION.md`. Scope isolated to `tsp_v2/recovery/reconcile.py`; independent of WO-017/WO-018 |

## Closure Rule

No patch may proceed past a related subsystem boundary while carrying unresolved `P0`.

`P1` items must either:

- be closed before the next subsystem depends on them, or
- have explicit written approval to carry temporarily.
