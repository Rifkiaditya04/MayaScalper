# Evidence Register

Register ini menghubungkan evidence operasional dengan area checklist yang dipengaruhi.

Gunakan ID evidence yang konsisten agar review, daily log, dan checklist bisa ditelusuri tanpa membuka banyak artefak.

## Register

| Evidence ID | Date | Source | Checklist Section | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| EV-001 | 2026-06-25 | startup telemetry | Runtime Reliability | VERIFIED | startup normal |
| EV-002 | 2026-06-26 | execution_filled | Trading Evidence | VERIFIED | reconciliation OK |
| EV-003 | 2026-06-24 | deployment.startup_sync / runtime_started | Runtime Reliability | VERIFIED | forward validation startup passed |
| EV-004 | 2026-06-24 | deployment.market_data_readiness | Validation Gates | VERIFIED | M1 closed_bars_insufficient; no checklist status change |
| EV-005 | 2026-06-24 | RV1 20-sample campaign | Validation Gates / WS-M1 | VERIFIED | M1 failure not reproduced across 20 attempts |
| EV-006 | 2026-06-25 | deployment.market_data_readiness / runtime db | Validation Gates / WO-017 | VERIFIED | M5 runtime occurrence: 69 closed bars on runtime cycle; characterization only |
| EV-007 | 2026-06-26 | deployment.market_data_readiness / runtime db | Validation Gates / WO-017 | VERIFIED | Recurrent M5 runtime occurrence: startup readiness on GBPUSD, runtime failure on GBPJPY |

## Rules

- Setiap evidence baru harus punya ID unik.
- Evidence harus bisa ditelusuri ke telemetry, log, atau artefak operasional.
- Status evidence mengikuti hasil review operasional, bukan asumsi.
- Register ini hanya berubah bila ada evidence baru atau status evidence yang benar-benar berubah.
