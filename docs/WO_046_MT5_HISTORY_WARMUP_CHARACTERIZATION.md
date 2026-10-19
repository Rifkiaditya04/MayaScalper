# WO-046 - MT5 History Warm-up Characterization

## 1. Baseline

- Git reference before and after this audit: `88d38665a907f77a1be47ac153c2d1c267646677`.
- `HEAD` is recorded only as a Git reference. The current workspace was used as the source authority.
- This work order did not call `Deployment.start()`, run a forward campaign, change configuration, or modify production or test source.

## 2. Workspace State

Pre-existing workspace state was preserved. It includes modified runtime/source/test files and untracked evidence notes from earlier work orders. In particular, `runtime/forward_live/db/tsp_v2_runtime.sqlite3` remains a runtime artifact and was not changed manually or staged.

`git diff --check` completed without whitespace errors. Git reported pre-existing LF-to-CRLF warnings for already-modified workspace files; this audit did not alter those files.

## 3. Source Verification

The workspace startup path remains the same as characterized in WO-045:

```text
DeploymentRuntime.start()
  -> _wait_for_startup_snapshot_readiness()
  -> build_market_snapshot(..., cycle_time_utc=broker_time)
  -> MarketDataProvider.get_rates()
  -> MT5MarketAdapter.get_rates()
  -> MT5Bridge.get_rates()
  -> copy_rates_range()
```

- `deployment.py` calls startup readiness before `runner_start` ([deployment.py](../tsp_v2/deployment.py:461), [deployment.py](../tsp_v2/deployment.py:683)).
- `build_market_snapshot()` passes the shared `cycle_time_utc` to every historical rates request ([snapshots.py](../tsp_v2/snapshots.py:197)).
- The adapter forwards that cutoff unchanged ([market_adapter.py](../tsp_v2/adapters/market_adapter.py:58)).
- With a cutoff, `MT5Bridge.get_rates()` uses the explicit-range branch ([mt5_bridge.py](../tsp_v2/adapters/mt5_bridge.py:791)); the range helper invokes `copy_rates_range()` ([mt5_bridge.py](../tsp_v2/adapters/mt5_bridge.py:937)).
- Non-M1 windows remain fixed: `max(count + 2, 40)` bars multiplied by timeframe minutes. M1 alone retains its bounded adaptive window ([mt5_bridge.py](../tsp_v2/adapters/mt5_bridge.py:997), [mt5_bridge.py](../tsp_v2/adapters/mt5_bridge.py:1020)).

No source difference affecting this path was found during this audit relative to the WO-045 characterization.

## 4. Terminal State Before Deployment Startup

At the observation time, `terminal64.exe` was running from the configured InstaForex MT5 installation. This establishes only that the terminal process existed.

The MT5 Python API did not have an IPC-connected, authenticated market-data session:

| Observation | Result |
| --- | --- |
| Direct `MetaTrader5.initialize()` with configured credentials | Failed: `[-6, "Terminal: Authorization failed"]` |
| Direct initialization without credentials | Failed: `[-10004, "No IPC connection"]` |
| `MT5Bridge.connect()` | Returned its existing-session fallback status |
| `symbol_info_tick("GBPUSD")` after bridge fallback | `None` |
| Direct `copy_rates_range("GBPUSD", H1, ...)` after bridge fallback | `0` records; `[-10004, "No IPC connection"]` |

The bridge fallback status is therefore not sufficient evidence that market-data IPC is usable. The direct tick and range calls are the decisive observations for this work order.

## 5. History Availability Table and Timeline

No valid pre-startup history sample could be collected for `GBPUSD`, `GBPJPY`, or `EURUSD` because the terminal API had no usable IPC connection. Recording zero bars as market-history availability would be incorrect: the zero response is accompanied by an explicit transport failure, not a successful empty history response.

| Sample time | Symbols | M1 | M5 | M15 | H1 | Result |
| --- | --- | --- | --- | --- | --- | --- |
| T0, 2026-09-20T01:19Z | GBPUSD, GBPJPY, EURUSD | Not observable | Not observable | Not observable | Not observable | MT5 IPC unavailable |

The requested T+30s, T+60s, T+120s, and T+300s measurements were not run because repeating a request after a confirmed IPC failure would not characterize history warming. It would only repeat a connectivity failure.

## 6. Comparison with WO-042 and WO-044

| Work order | M1 | M5 | M15 | H1 | Interpretation |
| --- | ---: | ---: | ---: | ---: | --- |
| WO-042 | 40 | 90 | 34 | 9 | H1 was the startup blocker for GBPUSD. |
| WO-044 | 40 | 37 | 13 | 4 | Higher-timeframe shortage affected the blocking GBPJPY startup snapshot; M1 remained full. |
| WO-046 | Not observable | Not observable | Not observable | Not observable | The pre-startup MT5 API session was unavailable, so it cannot confirm or refute warm-up behavior. |

WO-046 neither contradicts nor explains the differing WO-042 and WO-044 availability patterns. It adds a new operational prerequisite: valid MT5 API IPC must exist before a history warm-up timeline can be measured.

## 7. Candidate Matrix

| Candidate | Status | Evidence |
| --- | --- | --- |
| Terminal API/IPC unavailable | SUPPORTED | Direct initialization without login and direct rates calls returned `-10004 No IPC connection`. |
| Broker history unavailable | NOT_OBSERVABLE | No successful raw history request was possible in this session. |
| Terminal sync delay | NOT_OBSERVABLE | The session could not reach the history API. |
| History warming | NOT_OBSERVABLE | No valid T0 baseline exists from which to measure growth. |
| Symbol-specific issue | NOT_OBSERVABLE | No symbol returned usable raw data. |
| Timeframe-specific issue | NOT_OBSERVABLE | No timeframe returned usable raw data. |
| MT5 cache behavior | NOT_OBSERVABLE | Requires valid repeated range responses. |
| Startup timing | NOT_OBSERVABLE | Deployment was intentionally not started. |
| `copy_rates_range()` implementation defect | NOT_SUPPORTED | The direct call failed at IPC transport before a range response existed; the API method was not shown to return an incorrect successful payload. |
| MT5 bridge defect | NOT_SUPPORTED | Direct MT5 API calls fail with the same IPC condition; this occurs below bridge normalization and readiness logic. |
| Snapshot/deployment defect | NOT_SUPPORTED | Deployment was not invoked, and the failure precedes snapshot construction. |

## 8. Decision Gate

**D. Evidence still insufficient.**

This work order cannot determine whether history is absent or merely still synchronizing because the terminal was not usable through the MT5 Python IPC interface. No production patch is justified.

The next evidence-only action is operational: establish a logged-in MT5 session that provides a valid tick through `symbol_info_tick()` and a successful raw `copy_rates_range()` call. Only then repeat the T0 through T+300s sampling with the same terminal session and without starting deployment.

## 9. Regression Boundary

WO-046 makes no proposal or change to portfolio selection, risk, execution, telemetry, M1/M5 contracts, snapshot semantics, bridge behavior, readiness thresholds, or startup policy.

## 10. Recommendation

Do not open a code patch from this evidence. Restore valid MT5 API IPC/authentication first, then repeat the pre-startup characterization under that confirmed terminal state. The resulting timeline can distinguish an operational warm-up requirement from a startup/acquisition design issue.
