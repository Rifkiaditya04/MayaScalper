# VALIDATION REPORT V2-V03

Stage: V2-V03
Timestamp UTC: 20260530T112137Z
Overall Result: PASS

## Forward Test Scope

Three supervised runtime sessions with shared persistence, each running a bounded live cycle.

## Session Results

- session 1: PASS | cycles=1 | executions=1 | reconciliation_ready=True
- session 2: PASS | cycles=1 | executions=1 | reconciliation_ready=True
- session 3: PASS | cycles=1 | executions=0 | reconciliation_ready=True

## Observability

- runtime_hours: 0.25
- runtime_cycles: 3
- signals_generated: 3
- orders_submitted: 2
- telemetry_rows: 86

## Evidence

- Telemetry: validation/V2-V03/telemetry/20260530T112137Z_v2_v03_evidence.json
- Summary log: validation/V2-V03/logs/20260530T112137Z_v2_v03_summary.txt
- Full suite log: validation/V2-V03/logs/20260530T112137Z_full_suite.txt
