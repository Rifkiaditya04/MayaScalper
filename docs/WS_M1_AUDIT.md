# WS-M1 Audit

## Tujuan Audit

Menentukan apakah `closed_bars_insufficient` pada timeframe M1 disebabkan oleh:

- kontrak snapshot,
- karakteristik feed MT5,
- atau defect implementasi.

Audit ini hanya karakterisasi. Tidak ada perubahan perilaku runtime, snapshot, atau market adapter.

## Current Contract

Implementasi saat ini memakai kontrak berikut:

- `SnapshotBuildConfig.m1_bars = 40`
- `minimum_closed_bar_count(M1) = 34`
- `_closed_bars()` hanya menerima bar dengan `close_time_utc <= cycle_time_utc`
- `build_market_snapshot()` melempar `ConfigValidationError` bila `len(closed_m1) < 34`
- `evaluate_payload_health()` dapat tetap `GREEN` walau `closed_bar_count` belum mencapai minimum, selama raw payload lengkap

Artinya, M1 snapshot ready ditentukan oleh `cycle_time_utc` versus `close_time_utc`, bukan oleh jumlah raw bars semata.

## Failure Chronology

### 1. 2026-06-16 12:59:40 UTC

- Telemetry event id: `502`
- Symbol: tidak tersimpan di payload ringkas
- `requested_bars = 40`
- `returned_bars = 40`
- `closed_bar_count = 31`
- `minimum_closed_bar_count = 34`
- `payload_health = GREEN`
- `m1_raw_bar_stats` tidak tersedia pada payload ini

Kesimpulan sementara:

- raw payload lengkap,
- closed bar count belum cukup,
- belum ada detail raw dump untuk menjelaskan kenapa 9 bar sisanya tidak closed.

### 2. 2026-06-16 13:40:56 UTC

Dalam satu runtime yang sama:

- `GBPUSD` snapshot_ready
- `GBPJPY` snapshot_ready
- `EURUSD` gagal pada M1

Rincian failure `EURUSD`:

- Telemetry event id: `519`
- `requested_bars = 40`
- `returned_bars = 40`
- `closed_bar_count = 24`
- `minimum_closed_bar_count = 34`
- `forming_count = 1`
- `future_count = 15`
- `duplicate_close_time_count = 0`
- `latest_close_time_utc = 2026-06-16T13:58:00+00:00`
- `oldest_close_time_utc = 2026-06-16T13:18:00+00:00`

### 3. 2026-06-22 03:18:55 UTC

Dalam satu runtime yang sama:

- `GBPUSD` snapshot_ready
- `GBPJPY` snapshot_ready
- `EURUSD` gagal pada M1

Rincian failure `EURUSD`:

- Telemetry event id: `1126`
- `requested_bars = 40`
- `returned_bars = 40`
- `closed_bar_count = 30`
- `minimum_closed_bar_count = 34`
- `forming_count = 1`
- `future_count = 9`
- `duplicate_close_time_count = 0`
- `latest_close_time_utc = 2026-06-22T03:30:00+00:00`
- `oldest_close_time_utc = 2026-06-22T02:48:00+00:00`

Time context pada event ini:

- `broker_time_utc = 2026-06-22T03:18:55+00:00`
- `latest_tick_timestamp_utc = 2026-06-22T03:19:35+00:00`
- `m1_latest_closed_bar_close_time_utc = 2026-06-22T03:18:00+00:00`
- `m1_latest_raw_open_time_utc = 2026-06-22T03:29:00+00:00`
- `m1_latest_raw_close_time_utc = 2026-06-22T03:30:00+00:00`
- `broker_time_minus_latest_closed_bar_seconds = 55.0`
- `broker_time_minus_latest_raw_bar_seconds = -665.0`
- `broker_time_minus_latest_tick_seconds = -40.0`

## Evidence Already Owned

- Raw M1 payloads are complete (`requested_bars = returned_bars = 40`).
- The raw payload can be GREEN while `closed_bar_count` is still below minimum.
- Successful and failed M1 outcomes can happen in the same runtime timestamp across different symbols.
- Raw dumps show monotonic index ordering in emitted telemetry.
- Duplicate close times were not observed in the failure payloads that included stats.

## Evidence Still Missing

- A controlled replay of the same symbol and same broker time to isolate whether future bars are a feed characteristic or a time-reference mismatch.
- Direct provider-side guarantee about whether M1 rates should include future bars relative to `cycle_time_utc`.
- A broader sample set showing whether the same symbol fails consistently or only under specific feed timing windows.
- A direct comparison of successful and failed raw M1 windows for the same symbol under the same market session state.

## Hypotheses Still Possible

- M1 raw windows include future bars by provider design, and some symbols simply have less history available at the same moment.
- The raw window and `broker_time_utc` are not aligned tightly enough for some symbols.
- The issue is symbol-specific feed availability or session timing, not a global M1 contract defect.
- There are occasional gaps in M1 history that reduce the closed count for a symbol while other symbols remain healthy.

## Hypotheses That Are Largely Fallen

- `requested_bars = 40` is too small by itself. The evidence shows some symbols still pass with `40 raw`.
- Duplicate close times are the dominant cause. No duplicate close times were observed in the failure payloads with stats.
- The closed-bar filter itself is broken. The same filter yields `snapshot_ready` for other symbols in the same runtime.
- M1 is globally broken for all symbols. It is not: GBPUSD and GBPJPY passed in the same runs where EURUSD failed.

## Questions Before Patch

1. Is M1 failure symbol-specific or a true global contract issue?
2. Are the observed future bars a feed characteristic or a time-reference mismatch?
3. Should M1 be treated as a per-symbol readiness problem, or is the current contract already sufficient?
4. Is there enough evidence to change retrieval or validation behavior, or should WS-M1 remain characterization-only?

## Verdict

**Evidence masih belum cukup.**

The current data shows a real M1 failure pattern, but not enough to justify a contract or implementation change yet.

