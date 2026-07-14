# WS-M1 Characterization

This matrix summarizes what the current evidence says about M1 closed-bar behavior.

| Scenario | Expected | Evidence | Missing | Status |
| --- | --- | --- | --- | --- |
| 40 raw, 40 closed | PASS | Not currently traceable in repository evidence. The previously cited timestamp is not present in the current M1 evidence files. | Need a controlled replay or restored raw evidence. | NOT VERIFIED |
| 40 raw, 39 closed | PASS | Not currently traceable in repository evidence. The previously cited timestamp is not present in the current M1 evidence files. | Need a controlled replay or restored raw evidence. | NOT VERIFIED |
| 40 raw, future bars | INCONCLUSIVE | Observed in failure cases with `future_count = 9`, `future_count = 10`, `future_count = 14`, and `future_count = 24`. | Need a controlled replay to know whether future bars are causal or incidental. | Hypothesis open |
| duplicate close_time | INCONCLUSIVE | Observed `duplicate_close_time_count = 0` in failure payloads that included stats. | Need a case that actually contains duplicates to test the contract. | No supporting evidence |
| unordered bars | INCONCLUSIVE | Emitted raw dumps are indexed monotonically in telemetry. | Need direct provider-order proof to confirm or reject unordered input. | Weakly disfavored |
| gap timestamp | INCONCLUSIVE | Failure payload `1126` shows non-contiguous raw closes in the emitted dump. | Need to know whether gaps are normal provider behavior or a defect. | Observed, not explained |
| stale broker time | INCONCLUSIVE | `1126` shows `broker_time_utc = 03:18:55`, `latest_tick_timestamp_utc = 03:19:35`, `latest_raw_close_time_utc = 03:30:00`. | Need a matched comparison run where broker time, tick time, and raw window are captured for the same symbol across retries. | Observed, not isolated |

## Evidence Notes

- `snapshot_ready` and `closed_bars_insufficient` can occur in the same runtime timestamp across different symbols.
- `GBPUSD` and `GBPJPY` can pass M1 readiness while `EURUSD` fails in the same run.
- Raw M1 payloads in failure cases are complete (`requested_bars = returned_bars = 40`) and still can produce `closed_bar_count < 34`.
- The most informative failure payload so far is `telemetry_index.id = 1126`.
- Earlier `40 raw, 40 closed` and `40 raw, 39 closed` examples are no longer treated as traceable evidence until a matching replay or raw dump is restored.

## Interpretation So Far

The current evidence does **not** support a blanket claim that the M1 contract is broken.

It instead suggests one of these:

- symbol-specific feed availability,
- session timing,
- raw-window alignment differences,
- or a provider characteristic that needs more characterization before any patch.
