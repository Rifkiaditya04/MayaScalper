# WS-019 — Runtime Failure Inventory

## Metadata

- Status: Complete
- Scope: `tsp_v2/live_runtime.py` dan seluruh fungsi yang dipanggilnya
- Evidence Baseline: Implementasi aktual per 2026-07-13
- Related Docs:
  - `docs/DR_001_BROKER_RECONCILIATION_MISSING_BROKER_MISCLASSIFICATION.md`
  - `docs/PATCH_DR001A_BROKER_RECONCILIATION_CLASSIFICATION_FIX.md`
  - `docs/WO_017_M5_READINESS_CHARACTERIZATION.md`
  - `docs/WO_018_RUNTIME_READINESS_POLICY_REVIEW.md`

---

## Failure Category

```
A. Normal runtime skip     — cycle dilewati, runtime tetap berjalan, tidak ada exception
B. Recoverable inside cycle — exception di-handle lokal di dalam _run_cycle(), cycle tetap selesai
C. Fatal runtime exception  — exception lolos ke run(), runtime berhenti, re-raise ke caller
D. Startup failure          — runtime tidak pernah masuk loop cycle
```

---

## Tabel Inventaris

| # | Failure Source | Lokasi Kode | Exception | Handle Lokal? | Menghentikan Runtime? | Kategori | Evidence / Dokumen |
|---|---|---|---|---|---|---|---|
| 1 | Bootstrap reconciliation tidak ready | `live_runtime.py:run()` — `if not bootstrap_ready: raise` | `ConfigValidationError` | Tidak | Ya — lolos ke `except Exception` | D | `docs/DR_001_...md`, `docs/PATCH_DR001A_...md` |
| 2 | Market adapter unhealthy (heartbeat gagal atau tick stale) | `live_runtime.py:_run_cycle()` — `if not market_status.ok: raise` | `ConfigValidationError` | Tidak | Ya — lolos ke `except Exception` | C | `docs/WO_018_RUNTIME_READINESS_POLICY_REVIEW.md` |
| 3 | MT5 bridge: paket MetaTrader5 tidak tersedia | `adapters/mt5_bridge.py:_require_module()` | `MT5BridgeError` (`MT5_PACKAGE_UNAVAILABLE`, `fatal=True`) | Tidak | Ya — lolos ke `except Exception` | D |  |
| 4 | MT5 bridge: `initialize()` gagal / terminal tidak tersedia | `adapters/mt5_bridge.py:connect()` | `MT5BridgeError` (`TERMINAL_UNAVAILABLE`, `fatal=True`) | Tidak | Ya — lolos ke `except Exception` | D |  |
| 5 | MT5 bridge: login gagal | `adapters/mt5_bridge.py:connect()` | `MT5BridgeError` (`BROKER_DISCONNECTED`, `fatal=True`) | Tidak | Ya — lolos ke `except Exception` | D |  |
| 6 | MT5 bridge: heartbeat — terminal info unavailable | `adapters/mt5_bridge.py:heartbeat()` | `MT5BridgeError` (`TERMINAL_UNAVAILABLE`, `fatal=True`) | Tidak | Ya — via market_status.ok=False → `ConfigValidationError` | C |  |
| 7 | MT5 bridge: heartbeat — broker disconnected | `adapters/mt5_bridge.py:heartbeat()` | `MT5BridgeStatus.ok=False` (`BROKER_DISCONNECTED`) | Ya — dikembalikan sebagai status | Ya — via market_status.ok=False → `ConfigValidationError` | C |  |
| 8 | MT5 bridge: heartbeat — trade not allowed | `adapters/mt5_bridge.py:heartbeat()` | `MT5BridgeStatus.ok=False` (`BROKER_DISCONNECTED`) | Ya — dikembalikan sebagai status | Ya — via market_status.ok=False → `ConfigValidationError` | C |  |
| 9 | MT5 bridge: `query_account()` gagal | `adapters/mt5_bridge.py:query_account()` | `MT5BridgeError` (`BROKER_DISCONNECTED`, `fatal=False`) | Tidak | Ya — lolos ke `except Exception` | C |  |
| 10 | MT5 bridge: `query_symbol_contract()` — symbol tidak tersedia | `adapters/mt5_bridge.py:query_symbol_contract()` | `MT5BridgeError` (`SYMBOL_UNAVAILABLE`, `fatal=False`) | Tidak | Ya — lolos ke `except Exception` | C |  |
| 11 | MT5 bridge: `get_latest_tick()` — tick null setelah 20 retry | `adapters/mt5_bridge.py:get_latest_tick()` | `MT5BridgeError` (`SYMBOL_UNAVAILABLE`, `fatal=False`) | Tidak | Ya — lolos ke `except Exception` | C |  |
| 12 | MT5 bridge: `get_latest_tick()` — rates fallback gagal (IPC loss) | `adapters/mt5_bridge.py:get_latest_tick()` | `MT5BridgeError` (`SYMBOL_UNAVAILABLE`, `fatal=False`) | Tidak | Ya — lolos ke `except Exception` | C | Telemetry `deployment.market_data_readiness` di-emit oleh `run()` |
| 13 | MT5 bridge: `get_rates()` — rates kosong setelah 20 retry | `adapters/mt5_bridge.py:get_rates()` | `MT5BridgeError` (`CONTRACT_QUERY_FAILURE`, `fatal=False`) | Tidak | Ya — lolos ke `except Exception` | C | Telemetry `deployment.market_data_readiness` di-emit oleh `run()` |
| 14 | MT5 bridge: `order_send` mengembalikan None (API_HUNG) | `adapters/mt5_bridge.py:send_order()` | `MT5TradeResult` dengan `fatal=True`, `response_class=ESCALATE_KILL_REVIEW` | Ya — dikembalikan sebagai result, tidak raise | Tidak — execution result dicatat, cycle selesai | B | `docs/DR_001_...md` — kedua entry EXPIRED berasal dari event ini |
| 15 | MT5 bridge: retcode UNKNOWN / tidak dikenali | `adapters/mt5_bridge.py:_classify_trade_code()` | `MT5TradeResult` dengan `fatal=True`, `response_class=ESCALATE_KILL_REVIEW` | Ya — dikembalikan sebagai result | Tidak — execution result dicatat, cycle selesai | B |  |
| 16 | Snapshot builder: payload RED (bar count di bawah minimum) | `tsp_v2/snapshots.py:build_market_snapshot()` | `ConfigValidationError` | Tidak | Ya — lolos ke `except Exception` | C | `docs/WO_017_M5_READINESS_CHARACTERIZATION.md` |
| 17 | Snapshot builder: closed bars tidak cukup per timeframe | `tsp_v2/snapshots.py:build_market_snapshot()` | `ConfigValidationError` | Tidak | Ya — lolos ke `except Exception` | C | `docs/WO_017_M5_READINESS_CHARACTERIZATION.md` |
| 18 | Snapshot builder: tick bid/ask tidak valid | `tsp_v2/snapshots.py:_normalize_tick()` | `ConfigValidationError` | Tidak | Ya — lolos ke `except Exception` | C |  |
| 19 | Snapshot builder: OHLC malformed | `tsp_v2/snapshots.py:_normalize_rates()` | `ConfigValidationError` | Tidak | Ya — lolos ke `except Exception` | C |  |
| 20 | Snapshot builder: cycle_time_utc regressed | `tsp_v2/snapshots.py:_validate_cycle_monotonicity()` | `ConfigValidationError` | Tidak | Ya — lolos ke `except Exception` | C |  |
| 21 | Snapshot builder: ATR/ADX — bar tidak cukup | `tsp_v2/snapshots.py:_atr()`, `_adx()`, `_median_last()` | `ConfigValidationError` | Tidak | Ya — lolos ke `except Exception` | C |  |
| 22 | M5 gate: snapshot tidak memiliki closed bar sama sekali | `live_runtime.py:_latest_closed_m5_close()` | `ConfigValidationError` | Tidak | Ya — lolos ke `except Exception` | C | `docs/WO_017_M5_READINESS_CHARACTERIZATION.md` |
| 23 | M5 gate: close timestamp bukan datetime | `live_runtime.py:_latest_closed_m5_close()` | `ConfigValidationError` | Tidak | Ya — lolos ke `except Exception` | C |  |
| 24 | M5 gate: current_m5_close <= last_processed | `live_runtime.py:_closed_m5_gate_allows_process()` | Tidak ada exception | Ya — `_run_cycle()` mengembalikan `None` | Tidak — `run()` memanggil `time.sleep()` lalu lanjut | A | `docs/WO_017_M5_READINESS_CHARACTERIZATION.md`, `docs/M5_CLOSE_SEMANTICS.md` |
| 25 | Reconciliation: persistence gagal saat `store_execution_registry()` | `tsp_v2/recovery/reconcile.py:reconcile()` | `Exception` (SQLite / IO) | Tidak | Ya — lolos ke `except Exception` | C |  |
| 26 | Reconciliation: `datetime` tanpa timezone | `tsp_v2/recovery/reconcile.py:_ensure_utc()` | `ConfigValidationError` | Tidak | Ya — lolos ke `except Exception` | C |  |
| 27 | Execution: `validate_execution_intent()` menolak intent | `live_runtime.py:_run_cycle()` — `if not validation.accepted` | Tidak ada exception | Ya — `continue` ke signal berikutnya | Tidak — cycle tetap selesai | B |  |
| 28 | Execution: risk action bukan ENTER/SCALE/PYRAMID | `live_runtime.py:_run_cycle()` — `if risk.action not in {...}` | Tidak ada exception | Ya — `continue` ke signal berikutnya | Tidak — cycle tetap selesai | A |  |
| 29 | Execution adapter: `execute()` melempar exception | `tsp_v2/adapters/execution_adapter.py:execute()` | `Exception` | Tidak | Ya — lolos ke `except Exception` | C |  |
| 30 | Persistence: `store_telemetry_index()` gagal | `live_runtime.py:_emit_telemetry()` | `Exception` (SQLite / IO) | Tidak | Ya — lolos ke `except Exception` | C |  |
| 31 | Persistence: `store_governor_state()` gagal | `live_runtime.py:_run_cycle()` | `Exception` (SQLite / IO) | Tidak | Ya — lolos ke `except Exception` | C |  |
| 32 | Persistence: `store_position()` gagal | `live_runtime.py:_run_cycle()` | `Exception` (SQLite / IO) | Tidak | Ya — lolos ke `except Exception` | C |  |
| 33 | Persistence: `store_runtime_state()` gagal | `live_runtime.py:_store_runtime_state()` | `Exception` (SQLite / IO) | Tidak | Ya — lolos ke `except Exception` | C |  |
| 34 | Config: `symbols.allowlist` kosong | `tsp_v2/snapshots.py:build_market_snapshot()` | `ConfigValidationError` | Tidak | Ya — lolos ke `except Exception` | D |  |
| 35 | Config: symbol tidak ada di allowlist | `tsp_v2/snapshots.py:build_market_snapshot()` | `ConfigValidationError` | Tidak | Ya — lolos ke `except Exception` | D |  |
| 36 | Config: `contract.point <= 0` | `tsp_v2/snapshots.py:build_symbol_contract()` | `ConfigValidationError` | Tidak | Ya — lolos ke `except Exception` | C |  |
| 37 | Config: `contract.point <= 0` di market adapter | `adapters/market_adapter.py:_spread_points_from_tick()` | `ConfigValidationError` | Tidak | Ya — lolos ke `except Exception` | C |  |
| 38 | KeyboardInterrupt | `live_runtime.py:run()` — `except KeyboardInterrupt` | `KeyboardInterrupt` | Ya — ditangkap, `stopped_reason = "keyboard_interrupt"`, lalu re-raise | Ya — runtime berhenti bersih | C |  |
| 39 | Exception tak terduga (catch-all) | `live_runtime.py:run()` — `except Exception as exc` | `Exception` apapun | Ya — telemetry di-emit, lalu re-raise | Ya — runtime berhenti | C |  |

---

## Catatan Penting dari Kode

### Dua reconciliation per cycle
`_run_cycle()` memanggil `BrokerReconciliationRuntime.reconcile()` **dua kali**: sekali di awal cycle (sebelum snapshot) dan sekali di akhir cycle (setelah execution). Keduanya dapat melempar exception yang lolos ke `run()`. Ini berarti failure reconciliation dapat terjadi di dua titik berbeda dalam satu cycle.

### MT5BridgeError di-handle khusus di run()
`run()` memiliki blok `if isinstance(exc, MT5BridgeError)` yang meng-emit telemetry `deployment.market_data_readiness` sebelum meng-emit `runtime_error`. Ini adalah satu-satunya exception yang mendapat perlakuan khusus di level `run()`. Semua exception lain hanya mendapat `runtime_error` telemetry.

### Telemetry `runtime_stopped` selalu di-emit
Blok `finally` di `run()` selalu meng-emit `runtime_stopped` terlepas dari penyebab berhentinya runtime — termasuk `KeyboardInterrupt` dan exception fatal. Ini adalah satu-satunya jaminan observability saat runtime berhenti.

### Kategori A tidak menghasilkan telemetry error
M5 gate skip (kategori A) hanya menghasilkan telemetry `deployment.closed_m5_gate` dengan `decision: "skip"`. Tidak ada `runtime_error` atau `runtime_stopped` yang di-emit. Jika runtime terlihat "diam" tanpa error, ini adalah kondisi yang perlu diperiksa pertama.

### `order_send` None tidak menghentikan runtime
`API_HUNG` (order_send mengembalikan None) menghasilkan `ExecutionResult` dengan `fatal=True` tetapi **tidak melempar exception**. Runtime tetap berjalan. Ini adalah jalur yang menghasilkan entry `EXPIRED` tanpa `broker_ticket` — persis seperti yang didokumentasikan di DR-001.

---

## Ringkasan per Kategori

| Kategori | Jumlah | Keterangan |
|---|---|---|
| A — Normal skip | 2 | #24 (M5 gate), #28 (risk action bukan ENTER) |
| B — Recoverable inside cycle | 3 | #14 (API_HUNG), #15 (retcode UNKNOWN), #27 (intent rejected) |
| C — Fatal runtime exception | 30 | Mayoritas failure — semua lolos ke `except Exception` di `run()` |
| D — Startup failure | 4 | #1 (bootstrap), #3 (MT5 package), #4 (terminal), #5 (login) |

---

## Clarification Note

Penghentian proses di luar mekanisme exception Python yang tercatat oleh `run()` - misalnya penghentian eksternal atau kegagalan komponen native - tetap merupakan kemungkinan teknis. Skenario tersebut belum terverifikasi sebagai bagian dari inventaris ini dan sebaiknya dibaca sebagai kemungkinan, bukan fakta.
