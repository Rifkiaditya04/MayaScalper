# WO-017 Occurrence Record

## Metadata

```text
Occurrence ID:
Date (UTC):
Run ID:
Commit:
Profile:
Broker:
Terminal Build:
```

---

## Runtime Status

```text
startup_sync_completed:
runtime_started:
cycle_number:
```

---

## Failure

```text
Telemetry Event:
deployment.market_data_readiness

Stage:
closed_bars_insufficient

Exception:
ConfigValidationError
```

---

## Symbol

```text
Symbol:

Timeframe:
M5
```

---

## Snapshot

```text
requested_bars:

returned_bars:

minimum_closed_bar_count:

closed_bar_count:

forming_count:

future_count:

duplicate_close_time_count:

payload_health:
```

---

## Time Context

```text
broker_time_utc:

cycle_time_utc:

latest_tick_timestamp_utc:

latest_raw_open_time_utc:

latest_raw_close_time_utc:

latest_closed_bar_close_time_utc:
```

---

## Runtime Context

```text
last_processed_m5_close_utc:

closed_m5_gate:

primary_symbol:

allowlist_size:
```

---

## MT5 Context

Jika ada, catat event jurnal MT5 yang berdekatan dengan waktu kejadian.

```text
connection lost:

reconnect:

authorization:

terminal synchronized:

ping:
```

---

## Classification

Centang salah satu atau lebih.

```text
[ ] Same pattern as WO-017

[ ] Different symbol

[ ] Different timeframe

[ ] Network reconnect nearby

[ ] Startup passed

[ ] Runtime passed

[ ] Unknown
```

---

## Comparison

Bandingkan dengan occurrence sebelumnya.

```text
Same symbol?

Same broker time?

Same future_count?

Same closed_bar_count?

Same requested bars?

Same pattern?
```

---

## Notes

Observasi bebas.

Misalnya:

```text
Startup snapshot_ready=true.

Runtime cycle berikutnya gagal.

Pattern identik dengan EV-006 dan EV-007.
```

---

## Evidence

```text
Transcript:

Telemetry DB:

MT5 Journal:

Runtime DB:

Commit:
```
