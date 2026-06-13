# M5 Close Semantics

This note records how V2 decides whether an M5 bar is closed.

## Source of Truth

The snapshot builder uses `cycle_time_utc` as the authoritative cutoff.

Relevant flow in `tsp_v2/snapshots.py`:
- `cycle_utc = _ensure_utc(cycle_time_utc, ...)`
- `_normalize_rates(...)` computes `close_time_utc` for each bar
- `_closed_bars(...)` filters bars using `close_time_utc <= cycle_time_utc`

## How a Bar Becomes Closed

Normalized bars are built with:

```python
close_time_utc = timestamp + timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
```

For M5:
- open time = bar timestamp
- close time = `timestamp + 5 minutes`

A bar is considered closed only if:

```text
bar.close_time_utc <= cycle_time_utc
```

## Snapshot Builder Behavior

`build_market_snapshot()`:
- fetches raw bars from the provider
- normalizes them
- filters them through `_closed_bars()`
- counts closed bars
- rejects the snapshot if any required timeframe is below its minimum

For M5:
- `requested_bars = 70`
- `minimum_closed_bar_count = 70`

So the snapshot is valid only when:

```text
closed_bar_count >= 70
```

## Latest Closed M5 Close

The latest closed M5 bar is the last bar remaining after `_closed_bars()` filtering.

In payload terms, the anchor field is:
- `bar_anchor_m5_close_utc`

This is the close time of the latest closed M5 bar in the snapshot.

## Important Consequence

Because the cutoff is `cycle_time_utc`, a cycle executed while the current M5 candle is still open will naturally produce:

```text
70 raw bars -> 69 closed bars
```

That is expected behavior for the current contract.

## What This Semantics Is Not

This is **not** a waiting mechanism.

`build_market_snapshot()` is a validator:
- it checks readiness
- it fails fast if readiness is insufficient
- it does not wait for readiness to arrive

