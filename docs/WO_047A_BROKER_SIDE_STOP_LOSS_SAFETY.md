# WO-047a Broker-Side Stop-Loss Safety

## Scope

This work item fixes a confirmed safety defect in the workspace source: the risk
layer calculated `invalidation_price` for sizing, but `ExecutionIntent` and the
MT5 deal request did not carry that value as a broker-side stop loss. A filled
position therefore had no stop loss placed by this execution path.

This is a bug-fix deviation, not a strategy change. Lifecycle management,
trailing stops, retest entries, and entry-timing changes remain out of scope.

## Safety Changes

- `forward_live.yaml` now defaults `allow_live_execution` to `false` for future
  launches. This does not stop an already-running process or protect an
  already-open broker position.
- `evaluate_risk()` now blocks a signal before sizing when its structural stop
  is closer than `stop_level_points * point`. It neither widens the stop nor
  recalculates a larger-risk order.
- `evaluate_risk()` also blocks an invalid structural stop on the profitable
  side of entry before sizing. This prevents an invalid historical/tick pairing
  from reaching the execution layer.
- `ExecutionIntent` carries a required `stop_loss_price` and an optional
  `take_profit_price`.
- Execution-intent construction validates that the stop is positive and on the
  loss side of the intended long or short entry.
- MT5 deal requests include `sl` and optionally `tp`.
- The bridge now supports a dedicated `TRADE_ACTION_SLTP` request for future
  lifecycle work without requiring market-deal-only `volume` and `type`
  fields.

## Verification Scope

Tests cover risk fail-closed behavior, stop propagation from risk through the
execution intent into the adapter request, wrong-side stop rejection, and the
SLTP bridge request shape. The patch does not implement position lifecycle
management; broker-side SL on entry is the safety boundary delivered here.

## Operational Requirement

Before any live or forward launch, inspect the MT5 Trade tab manually and
confirm that no open position lacks an SL. Local SQLite state is not broker
truth and cannot establish that the terminal is flat or protected.
