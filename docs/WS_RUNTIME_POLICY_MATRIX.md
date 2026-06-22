# WS-RP2 Runtime Failure Policy Matrix

| Failure | Continue | Stop | Retry | Unknown |
| ------- | -------- | ---- | ----- | ------- |
| ConfigValidationError |  | X |  |  |
| MT5BridgeError |  | X |  |  |
| Snapshot failure |  | X |  |  |
| Reconciliation failure |  | X |  |  |
| Execution failure |  | X |  |  |
| Persistence failure |  | X |  |  |
| Telemetry failure |  | X |  |  |
| Unexpected Exception |  | X |  |  |
| Closed-M5 gate skip | X |  |  |  |

## Notes

- `Closed-M5 gate skip` is not a failure; it is the only built-in recoverable live-loop path.
- `Retry` is not part of the steady-state runtime policy.
- If a retryable recovery path is desired, it must be defined explicitly in a separate contract note.

