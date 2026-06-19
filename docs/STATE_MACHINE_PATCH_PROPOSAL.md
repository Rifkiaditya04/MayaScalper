# State Machine Patch Proposal

## Current Behaviour

- broker response yang tidak tegas dapat menempatkan registry entry ke `AMBIGUOUS`
- reconciliation timeout mencoba menutup unresolved entry menjadi `EXPIRED`
- validator transisi menolak `AMBIGUOUS -> EXPIRED`
- runtime berhenti dengan `ConfigValidationError`

## Root Cause

Implementasi state machine terlalu ketat pada jalur timeout reconciliation:

- `AMBIGUOUS` diperlakukan sebagai terminal tanpa transisi lanjutan
- tetapi reconciliation masih menganggap unresolved ambiguous entry dapat diselesaikan menjadi expired
- hasilnya, code path reconciliation dan transition table saling bertabrakan

## Proposed Behaviour

Pilihan desain yang paling konsisten dengan blueprint adalah:

- tetap pertahankan `AMBIGUOUS` sebagai state observability
- izinkan reconciliation timeout untuk menutup unresolved ambiguous exposure secara eksplisit
- pastikan transisi yang digunakan oleh reconciliation merupakan transisi yang sah secara registry

## Acceptance Criteria

1. Reconciliation timeout terhadap unresolved ambiguous entry tidak lagi memicu `ConfigValidationError`
2. Entry ambiguous yang melewati expiry dapat diselesaikan deterministik
3. `runtime_error:ConfigValidationError` dengan pesan `AMBIGUOUS -> EXPIRED` tidak muncul lagi
4. Tidak ada perubahan pada snapshot, market data, startup sync, atau M5 gate
5. Telemetry reconciliation tetap ada dan tetap bisa diaudit

## Risk

- Perubahan state transition dapat mempengaruhi recovery/reconciliation history
- Harus dijaga agar tidak membuka retry storm atau state regressions pada entry terminal lain

## Out of Scope

- snapshot contract
- MT5 bridge
- startup synchronization
- closed-M5 gate
- requested bars / minimum bars
- signal, risk, governor, execution sizing
- observability di luar state machine
