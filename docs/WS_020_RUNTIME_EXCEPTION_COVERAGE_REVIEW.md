# WS-020 - Runtime Exception Coverage Review

## Metadata

- Status: Complete
- Scope: `tsp_v2/live_runtime.py` dan seluruh jalur exception yang dapat mencapai `run()`
- Evidence Baseline: Implementasi aktual per 2026-07-14
- Related Docs:
  - `docs/WS_019_RUNTIME_FAILURE_INVENTORY.md`
  - `docs/WS_M1_CHARACTERIZATION.md`
  - `docs/WO_017_M5_READINESS_CHARACTERIZATION.md`
  - `docs/WO_018_RUNTIME_READINESS_POLICY_REVIEW.md`

---

## Objective

Memetakan seluruh exception yang dapat mencapai `run()`, lalu mencocokkannya satu per satu dengan inventaris WS-019 untuk memastikan tidak ada jalur runtime yang kehilangan observability atau belum terdokumentasi.

---

## Method

Audit ini mengikuti tiga lapisan:

1. Identifikasi semua raise/call-site di `_run_cycle()` dan `run()`.
2. Cocokkan setiap jalur dengan kategori WS-019.
3. Tandai apakah ada exception runtime-facing yang belum tercakup.

---

## Coverage Map

| # | Source Path | Exception / Outcome | WS-019 Coverage | Status |
|---|---|---|---|---|
| 1 | `live_runtime.py:206-249` | `KeyboardInterrupt`, generic `Exception`, `finally -> runtime_stopped` | #38, #39 | Covered |
| 2 | `live_runtime.py:209-239` | `MT5BridgeError` handled specially, emits `deployment.market_data_readiness` then `runtime_error` | #6-#13, #29, #39 | Covered |
| 3 | `live_runtime.py:261-265` | `ConfigValidationError("Market adapter unhealthy: ...")` | #2, #6, #7, #8 | Covered |
| 4 | `live_runtime.py:269-273` | `BrokerReconciliationRuntime.reconcile()` pre-snapshot can raise persistence / UTC / reconciliation exceptions | #1, #25, #26 | Covered |
| 5 | `live_runtime.py:277-285`, `snapshots.py:254-258`, `news.py:95-136`, `news.py:174-320` | `build_market_snapshot()` can raise `ConfigValidationError` / normalization errors; `build_news_snapshot()` can raise news-provider validation errors | #16-#23, #34-#37, #39 | Partially covered - news-provider failures are not explicit in WS-019 |
| 6 | `live_runtime.py:289-291` | M5 gate skip returns `None`, no exception | #24 | Covered |
| 7 | `live_runtime.py:343-407` | `validate_execution_intent()` reject path is non-exceptional; `execution_adapter.execute()` and persistence can raise | #27, #29, #30-#33, #39 | Covered |
| 8 | `live_runtime.py:408-423` | `BrokerReconciliationRuntime.reconcile()` post-execution can raise persistence / UTC / reconciliation exceptions | #1, #25, #26, #39 | Covered |
| 9 | `mt5_bridge.py:1000-1029` | `order_send()` returning `None` yields `MT5TradeResult` (`API_HUNG`) without raising | #14 | Covered |
| 10 | `snapshots.py:42-49` | `SnapshotBuildConfig.__post_init__` raises `ValueError` for invalid config | No explicit WS-019 row; not runtime-reachable in current call path | Internal-only |
| 11 | `snapshots.py:62-67`, `418-479`, `572-679` | `ConfigValidationError` from allowlist, monotonicity, malformed tick/rates, closed-bar minimums, ATR/ADX, and UTC validation | #16-#23, #34-#37 | Covered |
| 12 | `snapshots.py:670-673` | `datetime.fromtimestamp(float(raw_value), tz=timezone.utc)` can surface `ValueError` / `OverflowError` / `TypeError` from malformed provider data | #39 | Covered by catch-all |

---

## Verification Notes

- `_run_cycle()` calls `BrokerReconciliationRuntime.reconcile()` **dua kali**: sebelum snapshot (`live_runtime.py:269-273`) dan sesudah execution (`live_runtime.py:408-412`).
- `send_order()` on `API_HUNG` returns a `MT5TradeResult` object with `fatal=True` and does **not** raise Python exception (`mt5_bridge.py:1000-1029`).
- `run()` emits `runtime_stopped` from a `finally` block (`live_runtime.py:240-249`), so the runtime-stop telemetry path is independent of success or failure outcome.
- `build_market_snapshot()` remains the main source of `ConfigValidationError` inside the cycle (`snapshots.py:62-67`, `187-197`, `418-479`, `572-679`).

---

## Coverage Assessment

Every runtime-facing exception path examined during this audit maps to an existing WS-019 category, but not every source is named with equal specificity.

The explicit documentation gap found in WS-019 is the news-snapshot / news-provider family (`build_news_snapshot()` and its validation helpers). Those exceptions are runtime-facing and do reach `run()`, but WS-019 currently only covers them indirectly under the generic catch-all category. The gap is documentation specificity rather than runtime observability.

The only item that is not a current runtime-facing path is `SnapshotBuildConfig.__post_init__` (`snapshots.py:42-49`): it raises `ValueError` if invoked with invalid parameters, but `_run_cycle()` currently constructs `SnapshotBuildConfig()` with default values only (`snapshots.py:65`), so this path is internal-only in the current runtime execution model.

No additional runtime-facing exception path requiring a new WS-019 failure category was found beyond the news-snapshot family already noted above.

---

## Verdict

`WS-019` already covers the runtime exception surface that can reach `run()` in the current codebase, but the news-snapshot / news-provider family should be added later if we want full source-level specificity.

`WS-020` therefore closes as an audit review with:

- no new runtime-facing exception class discovered,
- no new telemetry gap discovered,
- no code change required.
