# State Machine Audit

## Scope

Audit ini hanya mencakup execution lifecycle di:

- `tsp_v2/enums.py`
- `tsp_v2/execution.py`
- `tsp_v2/adapters/execution_adapter.py`
- `tsp_v2/recovery/reconcile.py`
- caller terkait di `tsp_v2/live_runtime.py`

## 1. Execution State Enum

`ExecutionRegistryState` didefinisikan di `tsp_v2/enums.py`:

- `PENDING`
- `SUBMITTED`
- `ACKNOWLEDGED`
- `PARTIAL`
- `FILLED`
- `REJECTED`
- `AMBIGUOUS`
- `CANCELLED`
- `EXPIRED`

## 2. Transition Table

`tsp_v2/execution.py` mendefinisikan `TRANSITION_ALLOWED`.

Ringkasan aturan yang relevan:

- `PENDING` -> `SUBMITTED`, `ACKNOWLEDGED`, `PARTIAL`, `FILLED`, `REJECTED`, `CANCELLED`, `EXPIRED`, `AMBIGUOUS`
- `SUBMITTED` -> `ACKNOWLEDGED`, `PARTIAL`, `FILLED`, `REJECTED`, `CANCELLED`, `EXPIRED`, `AMBIGUOUS`
- `ACKNOWLEDGED` -> `PARTIAL`, `FILLED`, `REJECTED`, `CANCELLED`, `EXPIRED`, `AMBIGUOUS`
- `PARTIAL` -> `FILLED`, `REJECTED`, `CANCELLED`, `EXPIRED`, `AMBIGUOUS`
- `FILLED` -> tidak ada transisi
- `REJECTED` -> tidak ada transisi
- `CANCELLED` -> tidak ada transisi
- `EXPIRED` -> tidak ada transisi
- `AMBIGUOUS` -> tidak ada transisi

## 3. Validator Transisi

`ExecutionRegistryBook.transition()` di `tsp_v2/execution.py`:

- memeriksa apakah `new_state == entry.state`
- jika sama, update timestamp saja
- jika berbeda, validasi `new_state in TRANSITION_ALLOWED[entry.state]`
- jika tidak valid, melempar:

```text
Invalid execution state transition: <current> -> <new>
```

## 4. Caller yang Mengubah State

### `tsp_v2/execution.py`

- `ExecutionRegistryBook.mark_submitted()`
- `ExecutionRegistryBook.mark_acknowledged()`
- `ExecutionRegistryBook.mark_partial()`
- `ExecutionRegistryBook.mark_filled()`
- `ExecutionRegistryBook.mark_rejected()`
- `ExecutionRegistryBook.mark_cancelled()`
- `ExecutionRegistryBook.mark_expired()`
- `ExecutionRegistryBook.mark_ambiguous()`

### `tsp_v2/adapters/execution_adapter.py`

`MT5ExecutionAdapter._update_registry()`:

- `bridge_result.ok` -> `mark_submitted()`, lalu `mark_partial()` / `mark_filled()` / `mark_acknowledged()`
- `disposition.retryable` -> `mark_submitted()`
- `disposition.suggested_state is REJECTED` -> `mark_rejected()`
- `disposition.suggested_state is CANCELLED` -> `mark_cancelled()`
- `disposition.suggested_state is EXPIRED` -> `mark_expired()`
- selain itu -> `mark_ambiguous()`

### `tsp_v2/recovery/reconcile.py`

`BrokerReconciliationRuntime.reconcile()` memanggil `reconcile_registry_against_broker_truth()`, yang pada akhirnya dapat memanggil:

- `ExecutionRegistryBook.mark_expired()` jika entry masih unresolved dan `current_time >= expires_at_utc`

## 5. Lokasi Pengirim AMBIGUOUS

`AMBIGUOUS` dikirim dari:

- `tsp_v2/execution.py`
  - `classify_broker_response()` saat kode broker tidak dikenal
  - default fallback saat response tidak punya code yang bisa dipetakan
- `tsp_v2/adapters/execution_adapter.py`
  - `_update_registry()` saat disposition tidak retryable dan bukan REJECTED/CANCELLED/EXPIRED

## 6. Lokasi Pengirim EXPIRED

`EXPIRED` dikirim dari:

- `tsp_v2/execution.py`
  - `reconcile_against_broker_truth()` melalui `mark_expired()` ketika entry unresolved dan `expires_at_utc` sudah lewat
- `tsp_v2/adapters/execution_adapter.py`
  - `_update_registry()` jika broker disposition sudah mengindikasikan expired

## 7. Temuan Utama

Failure `AMBIGUOUS -> EXPIRED` bukan berasal dari snapshot, MT5 bridge, atau startup sync.

Pola kegagalannya adalah:

1. execution response tidak dapat dipetakan dengan tegas
2. registry masuk `AMBIGUOUS`
3. reconciliation timeout mencoba `EXPIRED`
4. validator menolak transisi tersebut

## 8. Verdict Sementara

**Verdict: A**

Blueprint tidak menunjukkan larangan eksplisit untuk reconciliation terhadap unresolved ambiguous exposure, tetapi implementasi execution registry terlalu ketat karena tidak mengizinkan `AMBIGUOUS -> EXPIRED` walaupun path reconciliation mencoba menutup entry unresolved setelah timeout.
