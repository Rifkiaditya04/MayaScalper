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

### M1-20260624-001

- Runtime date (UTC): `2026-06-24`
- Run type: forward test
- Build / repo commit: `43af549`
- Profile: `FORWARD_SAFE`
- Broker: `InstaForex-Server`
- Symbol: `GBPJPY`
- Telemetry event: `deployment.market_data_readiness`
- Event id: `1364`
- Stage: `closed_bars_insufficient`
- Reason: `Not enough closed bars for timeframe M1: need at least 34`

Snapshot:

- `requested_bars = 40`
- `returned_bars = 40`
- `minimum_closed_bar_count = 34`
- `closed_bar_count = 29`
- `forming_count = 1`
- `future_count = 10`
- `duplicate_close_time_count = 0`
- `payload_health = GREEN`

Time context:

- `broker_time_utc = 2026-06-24T03:45:23+00:00`
- `latest_tick_timestamp_utc = 2026-06-24T03:46:51+00:00`
- `latest_raw_open_time_utc = 2026-06-24T03:55:00+00:00`
- `latest_raw_close_time_utc = 2026-06-24T03:56:00+00:00`
- `latest_closed_bar_close_time_utc = 2026-06-24T03:45:00+00:00`
- `delta_tick_to_broker_seconds = -88.0`
- `delta_raw_close_to_broker_seconds = -637.0`
- `delta_closed_close_to_broker_seconds = 23.0`

Raw window summary:

- `requested_raw = 40`
- `returned_raw = 40`
- `oldest_raw_open = 2026-06-24T03:16:00+00:00`
- `newest_raw_open = 2026-06-24T03:55:00+00:00`
- `oldest_raw_close = 2026-06-24T03:17:00+00:00`
- `newest_raw_close = 2026-06-24T03:56:00+00:00`

Runtime:

- `startup_sync_completed = true`
- `runtime_started = true`
- `cycle_number = 0`
- `last_processed_m5_close_utc = n/a`
- `closed_m5_gate = n/a`

Feed context:

- `symbol_info_tick_result = unknown`
- `retry_count = unknown`
- `stream_used = unknown`
- `stream_tick_found = unknown`
- `rates_fallback_used = unknown`
- `tick_time_fallback_source = unknown`
- `final_time_source = unknown`

Observation:

- Forward validation started normally and then failed before the first runtime cycle because M1 closed-bar readiness remained insufficient.
- The sample is consistent with prior M1 characterization samples but does not yet justify a contract change.

Initial classification:

- [ ] Timing
- [ ] MT5 bridge
- [ ] Race condition
- [x] Unknown

Raw dump reference:

- `telemetry_index.id = 1364`

---

## Lampiran Raw Dump

Simpan dump lengkap `m1_raw_bar_dump` sebagai file terpisah dengan nama yang konsisten, misalnya:

```text
artifacts/M1-20260619-001-raw-bars.json
artifacts/M1-20260619-002-raw-bars.json
```

Referensikan file lampiran di bagian "Observation" atau "Free-form notes" agar dokumen utama tetap ringkas.

---

### M1-20260624-20S-CAMPAIGN

- Runtime date (UTC): `2026-06-24`
- Run type: forward test campaign
- Build / repo commit: `43af549`
- Sample count: `20`
- Startup PASS: `20/20`
- Runtime PASS: `20/20`
- M1 closed_bars_insufficient observed: `0/20`
- Forward cycles observed: `2/20`

Observation:

- The 20-sample campaign did not reproduce the earlier M1 failure.
- Within this campaign, M1 readiness was stable enough to allow runtime startup in every attempt.
- The behavior remains intermittent across the broader evidence set, so the characterization remains open.

Evidence references:

- `validation/RV1/logs/20260624T040031Z-rv1-sample-01.log`
- `validation/RV1/logs/20260624T040032Z-rv1-sample-02.log`
- `validation/RV1/logs/20260624T040033Z-rv1-sample-03.log`
- `validation/RV1/logs/20260624T040425Z-rv1-sample-04.log`
- `validation/RV1/logs/20260624T040928Z-rv1-sample-05.log`
- `validation/RV1/logs/20260624T040929Z-rv1-sample-06.log`
- `validation/RV1/logs/20260624T040930Z-rv1-sample-07.log`
- `validation/RV1/logs/20260624T040931Z-rv1-sample-08.log`
- `validation/RV1/logs/20260624T040932Z-rv1-sample-09.log`
- `validation/RV1/logs/20260624T040933Z-rv1-sample-10.log`
- `validation/RV1/logs/20260624T040934Z-rv1-sample-11.log`
- `validation/RV1/logs/20260624T040935Z-rv1-sample-12.log`
- `validation/RV1/logs/20260624T040936Z-rv1-sample-13.log`
- `validation/RV1/logs/20260624T040937Z-rv1-sample-14.log`
- `validation/RV1/logs/20260624T040938Z-rv1-sample-15.log`
- `validation/RV1/logs/20260624T040939Z-rv1-sample-16.log`
- `validation/RV1/logs/20260624T040940Z-rv1-sample-17.log`
- `validation/RV1/logs/20260624T040941Z-rv1-sample-18.log`
- `validation/RV1/logs/20260624T040942Z-rv1-sample-19.log`
- `validation/RV1/logs/20260624T040943Z-rv1-sample-20.log`
