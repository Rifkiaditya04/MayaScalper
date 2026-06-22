# Windows Lock Reclaim Audit

## Current Behavior

`DeploymentRuntime.preflight()` acquires the deployment lock before any startup synchronization or runtime bootstrap. When an existing lock file is present, `SingleInstanceLock.acquire()` loads the on-disk snapshot and calls `_can_reclaim(existing, now_utc)`.

The reclaim decision currently depends on:

```python
return not _process_is_alive(existing.pid)
```

`_process_is_alive()` is implemented with:

```python
os.kill(pid, 0)
```

On Windows, that call is not behaving like a harmless liveness probe in this environment.

## Failure Path

Observed FT9 failure path:

```text
DeploymentRuntime.start()
  -> DeploymentRuntime.preflight()
    -> _acquire_lock()
      -> SingleInstanceLock.acquire()
        -> _can_reclaim(existing, current_time)
          -> _process_is_alive(existing.pid)
            -> os.kill(pid, 0)
              -> OSError [WinError 87]
              -> SystemError: <class 'OSError'> returned a result with an exception set
```

The lock file that triggered the reclaim attempt contained:

```json
{"created_at_utc":"2026-06-22T02:30:44.754121+00:00","owner":"V2:FORWARD_TEST:FORWARD_SAFE","pid":6740,"reclaimed":false,"token":"ec00693ce91e4053b3bc6792aa44090c","updated_at_utc":"2026-06-22T02:30:44.754121+00:00"}
```

This means the failure was reached while evaluating a valid-looking integer PID from the lock file, not while parsing corrupt lock data.

## Root-Cause Hypothesis

The strongest current hypothesis is:

1. `_process_is_alive()` is using a PID probe that is not reliable on this Windows/Python combination.
2. The implementation does not have a Windows-specific branch.
3. The reclaim path therefore fails before startup sync can run.

The supporting local probe showed:

```text
os.kill(6740, 0) -> SystemError("<class 'OSError'> returned a result with an exception set")
```

while other invalid PIDs returned a plain `OSError(22, 'The parameter is incorrect', ..., 87, ...)`.

## Evidence Summary

- `_process_is_alive(pid)` exists in `tsp_v2/deployment.py`.
- It returns `False` for `pid <= 0`, `True` for the current process, and otherwise uses `os.kill(pid, 0)`.
- `SingleInstanceLock._load_snapshot()` coerces `pid` to `int`, so the reclaim path is already operating on an integer PID.
- The FT9 lock file contained `pid=6740`, so the crash was not caused by a missing PID field.
- The runtime never reached `deployment.startup_sync` for the failed launch because the exception occurred earlier in preflight.

## Blueprint Comparison

This failure is orthogonal to the trading/runtime blueprint:

- It happens before startup synchronization.
- It happens before snapshot readiness.
- It happens before live runtime.
- It happens before any execution state machine activity.

So the blueprint does not appear to be the source of the failure.

## Patch Options

Potential implementation options, in increasing scope:

1. Add a Windows-specific liveness check instead of `os.kill(pid, 0)`.
2. Catch the Windows-specific failure path and treat it as "not alive" only when that is safe and evidence-backed.
3. Replace the current probe with a dedicated platform-aware process probe helper.

## Recommended Action

Do not change trading/runtime behavior yet.

Recommended next step:

- Patch `_process_is_alive()` to use a Windows-safe liveness probe.
- Add regression coverage for stale lock reclaim on Windows.
- Keep the startup/runtime/market-data workstreams frozen.

