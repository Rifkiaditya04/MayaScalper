# M1 Failure Characterization

Tujuan:
Mengumpulkan evidence saat M1 mengalami `closed_bars_insufficient`
tanpa mengubah kontrak runtime.

---

## Case ID
M1-YYYYMMDD-###

### Runtime

Date (UTC):
Run type:
Commit:
Profile:
Broker:
Symbol:

---

### Failure

Telemetry event:
Stage:
Reason:

---

### Snapshot

requested_bars:
returned_bars:
minimum_closed_bar_count:
closed_bar_count:
forming_count:
future_count:
duplicate_close_time_count:

payload_health:

---

### Time Context

broker_time_utc:

latest_tick_timestamp_utc:

latest_raw_open_time_utc:

latest_raw_close_time_utc:

latest_closed_bar_close_time_utc:

delta_tick_to_broker_seconds:

delta_raw_close_to_broker_seconds:

delta_closed_close_to_broker_seconds:

---

### Startup / Runtime

startup_sync_completed:
runtime_started:
cycle_number:

last_processed_m5_close_utc:

closed_m5_gate:
(skip/process)

---

### Feed Context

symbol_info_tick_result:

retry_count:

stream_used:

stream_tick_found:

rates_fallback_used:

tick_time_fallback_source:

final_time_source:

---

### Raw Window Summary

requested_raw:

returned_raw:

oldest_raw_open:

newest_raw_open:

oldest_raw_close:

newest_raw_close:

---

### Observation

Free-form notes.

---

### Initial Classification

- [ ] Timing
- [ ] Feed delay
- [ ] MT5 bridge
- [ ] Race condition
- [ ] Unknown

---

### Final Verdict

(diisi setelah investigasi)

---

## Case Log

### M1-20260622-001

- Runtime date (UTC): `2026-06-22`
- Run type: forward test
- Profile: `FORWARD_SAFE`
- Broker: `InstaForex-Server`
- Symbol: `EURUSD`
- Telemetry event: `deployment.market_data_readiness`
- Event id: `1126`
- Stage: `closed_bars_insufficient`
- Reason: `Not enough closed bars for timeframe M1: need at least 34`

Snapshot:

- `requested_bars = 40`
- `returned_bars = 40`
- `minimum_closed_bar_count = 34`
- `closed_bar_count = 30`
- `forming_count = 1`
- `future_count = 9`
- `duplicate_close_time_count = 0`
- `payload_health = GREEN`

Time context:

- `broker_time_utc = 2026-06-22T03:18:55+00:00`
- `latest_tick_timestamp_utc = 2026-06-22T03:19:35+00:00`
- `latest_raw_open_time_utc = 2026-06-22T03:29:00+00:00`
- `latest_raw_close_time_utc = 2026-06-22T03:30:00+00:00`
- `latest_closed_bar_close_time_utc = 2026-06-22T03:18:00+00:00`
- `delta_tick_to_broker_seconds = -40.0`
- `delta_raw_close_to_broker_seconds = -665.0`
- `delta_closed_close_to_broker_seconds = 55.0`

Raw window summary:

- `requested_raw = 40`
- `returned_raw = 40`
- `oldest_raw_open = 2026-06-22T02:47:00+00:00`
- `newest_raw_open = 2026-06-22T03:29:00+00:00`
- `oldest_raw_close = 2026-06-22T02:48:00+00:00`
- `newest_raw_close = 2026-06-22T03:30:00+00:00`

Runtime:

- `startup_sync_completed = true`
- `runtime_started = true`
- `cycle_number = 0`
- `last_processed_m5_close_utc = n/a`
- `closed_m5_gate = n/a`

Feed context:

- `symbol_info_tick_result = success`
- `retry_count = 0`
- `stream_used = false`
- `stream_tick_found = false`
- `rates_fallback_used = false`
- `tick_time_fallback_source = null`
- `final_time_source = tick`

Observation:

- M1 failure occurred in the same forward run where `GBPUSD` and `GBPJPY` passed snapshot readiness.
- The failure remained symbol-specific within the runtime snapshot collection.
- This sample strengthens the characterization set but still does not justify a contract change.

Initial classification:

- [ ] Timing
- [ ] MT5 bridge
- [ ] Race condition
- [x] Unknown

Raw dump reference:

- `telemetry_index.id = 1126`

---

## Lampiran Raw Dump

Simpan dump lengkap `m1_raw_bar_dump` sebagai file terpisah dengan nama yang konsisten, misalnya:

```text
artifacts/M1-20260619-001-raw-bars.json
artifacts/M1-20260619-002-raw-bars.json
```

Referensikan file lampiran di bagian "Observation" atau "Free-form notes" agar dokumen utama tetap ringkas.
