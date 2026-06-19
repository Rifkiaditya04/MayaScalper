# State Machine Failure Path

## Telemetry Run Terakhir

Failure terakhir berakhir dengan:

```text
runtime_error = ConfigValidationError
message = Invalid execution state transition: AMBIGUOUS -> EXPIRED
```

## Rekonstruksi Jalur

### 1. State awal dibentuk

`MT5ExecutionAdapter._update_registry()` di `tsp_v2/adapters/execution_adapter.py` menerima broker response yang tidak dapat dipetakan secara deterministik ke state final yang aman.

Jalur yang dipakai:

- `classify_broker_response()`
- hasil: `BrokerDisposition.suggested_state = AMBIGUOUS`
- lalu `registry.mark_ambiguous(...)`

### 2. Entry tersimpan sebagai AMBIGUOUS

Di runtime DB terlihat entry terakhir:

- `state = AMBIGUOUS`
- `updated_at_utc = 2026-06-19T12:57:47+00:00`
- `expires_at_utc = 2026-06-19T12:59:17+00:00`

### 3. Reconciliation berikutnya mencoba menutup entry

`BrokerReconciliationRuntime.reconcile()` di `tsp_v2/recovery/reconcile.py` memanggil:

- `reconcile_registry_against_broker_truth()`
- `ExecutionRegistryBook.reconcile_against_broker_truth()`

Jika entry belum punya broker record dan `current_time >= expires_at_utc`, kode memanggil:

- `mark_expired(...)`

### 4. Validator menolak

`ExecutionRegistryBook.transition()` di `tsp_v2/execution.py` mengecek transisi:

- current state: `AMBIGUOUS`
- requested state: `EXPIRED`

Karena `TRANSITION_ALLOWED[AMBIGUOUS] = frozenset()`, transisi itu ditolak dan melempar:

```text
Invalid execution state transition: AMBIGUOUS -> EXPIRED
```

### 5. Runtime berhenti

Exception itu dipropagasikan ke runtime dan dicatat sebagai:

- `runtime_error`
- `runtime_stopped`

## File dan Fungsi Kunci

- `tsp_v2/adapters/execution_adapter.py`
  - `MT5ExecutionAdapter._update_registry()`
  - `classify_broker_response()`
- `tsp_v2/recovery/reconcile.py`
  - `BrokerReconciliationRuntime.reconcile()`
  - `build_reconciliation_report()`
- `tsp_v2/execution.py`
  - `ExecutionRegistryBook.mark_ambiguous()`
  - `ExecutionRegistryBook.mark_expired()`
  - `ExecutionRegistryBook.transition()`
  - `ExecutionRegistryBook.reconcile_against_broker_truth()`

## Interpretasi

Failure ini bukan berasal dari snapshot, market data, atau closed-bar gate.

Ini adalah konflik antara:

- state yang dipilih sebagai **AMBIGUOUS** saat broker outcome tidak tegas
- state yang dipilih sebagai **EXPIRED** saat reconciliation timeout
- validator transisi yang menolak perpindahan tersebut
