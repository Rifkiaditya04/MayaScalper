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

## Lampiran Raw Dump

Simpan dump lengkap `m1_raw_bar_dump` sebagai file terpisah dengan nama yang konsisten, misalnya:

```text
artifacts/M1-20260619-001-raw-bars.json
artifacts/M1-20260619-002-raw-bars.json
```

Referensikan file lampiran di bagian "Observation" atau "Free-form notes" agar dokumen utama tetap ringkas.
