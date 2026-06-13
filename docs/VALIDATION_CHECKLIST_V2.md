# Validation Checklist V2

## Purpose
Use this checklist during the Production Validation phase to execute, observe, and record
each validation stage in a consistent, auditable way.

## How to Use
- Run one stage at a time.
- Mark items only after direct evidence is collected.
- Record any failure immediately and stop the stage.
- Do not advance to the next stage until the current stage passes.

## Shared Evidence Convention
- Store stage evidence under `validation/V2-V0X/`
- Keep logs, reports, telemetry exports, screenshots, and operator notes together per stage
- Use UTC timestamps in filenames and report headers
- Record the runtime mode for every run

## V2-V01 - Integration Validation
### Goal
Prove the full pipeline can start, run, and shut down cleanly.

### Checklist
- [ ] Preflight passed
- [ ] Broker connect passed
- [ ] Reconciliation passed
- [ ] First bounded runtime cycle completed
- [ ] Market Adapter returned valid data
- [ ] Snapshot Builder completed without exception
- [ ] Regime classification completed
- [ ] Signal evaluation completed
- [ ] Risk evaluation completed
- [ ] Governor evaluation completed
- [ ] Execution intent validation completed
- [ ] Execution Adapter path completed
- [ ] Broker path returned a controlled result
- [ ] Telemetry emitted during the cycle
- [ ] Clean shutdown completed

### Evidence to Capture
- [ ] Start and end time UTC
- [ ] Runtime mode
- [ ] Symbol set
- [ ] Telemetry summary
- [ ] Reconciliation findings
- [ ] Any exception or warning

### Pass Criteria
- [ ] No crash
- [ ] No deadlock
- [ ] No ownership violation
- [ ] No uncaught exception
- [ ] No unexpected broker mutation

### Fail Criteria
- [ ] Any uncaught exception
- [ ] Any unresolved reconciliation conflict
- [ ] Any duplicate execution path
- [ ] Any state transition that violates ownership

## V2-V02 - Recovery Validation
### Goal
Prove restart doctrine and broker-truth convergence.

### Stage-Specific Checklist
- [ ] See [VALIDATION_CHECKLIST_V2_V02.md](VALIDATION_CHECKLIST_V2_V02.md) for the operational recovery runbook.

### Checklist
- [ ] Kill process while a signal is active
- [ ] Restart after active signal interruption
- [ ] Kill process after execution is submitted
- [ ] Restart after submitted execution interruption
- [ ] Kill process while a position is open
- [ ] Restart while position is open
- [ ] Disconnect broker and reconnect
- [ ] Restart after clean shutdown

### Evidence to Capture
- [ ] Recovery findings
- [ ] Broker truth findings
- [ ] Registry state after restart
- [ ] Telemetry summary
- [ ] Any manual intervention

### Pass Criteria
- [ ] Recovery produces MATCHED or another explicit controlled finding
- [ ] No ghost position
- [ ] No ghost order
- [ ] No duplicate order after restart
- [ ] Broker truth remains authoritative

### Fail Criteria
- [ ] Any unresolved divergence after recovery
- [ ] Any duplicate order submission
- [ ] Any phantom exposure
- [ ] Any recovery path that depends on manual guesswork

## V2-V03 - Forward-Test Validation
### Goal
Prove runtime stability in a demo or paper environment before contest use.

### Stage-Specific Tracking
- Use [VALIDATION_V2_V03_DAILY_TRACKING.md](VALIDATION_V2_V03_DAILY_TRACKING.md) for daily evidence capture.
- Use [VALIDATION_V2_V03_WEEKLY_SUMMARY.md](VALIDATION_V2_V03_WEEKLY_SUMMARY.md) for weekly rollups.

### Stage 1 - Demo Account
- [ ] Demo account configured
- [ ] Supervised live micro only
- [ ] Minimum duration target: 2 weeks
- [ ] Crash count tracked
- [ ] Execution failures tracked
- [ ] Recovery events tracked
- [ ] Reconciliation mismatches tracked
- [ ] Telemetry consistency reviewed

### Hard Stop Conditions
- [ ] Unexplained position
- [ ] Duplicate order
- [ ] Any repeated recovery mismatch

### Stage 2 - Extended Forward Test
- [ ] Minimum duration target: 4 weeks
- [ ] Governor transitions reviewed
- [ ] Risk control behavior reviewed
- [ ] Pace transitions reviewed
- [ ] Starvation handling reviewed
- [ ] Session behavior reviewed

### Evidence to Capture
- [ ] Daily runtime summary
- [ ] Execution anomalies
- [ ] Recovery anomalies
- [ ] Telemetry exports
- [ ] Operator notes

## V2-V04 - Contest Rehearsal
### Goal
Prove contest behavior before real contest capital is used.

### Checklist
- [ ] SURVIVE observed
- [ ] NORMAL observed
- [ ] ATTACK observed
- [ ] HUNTER observed
- [ ] CHASE observed
- [ ] PROTECT observed
- [ ] SPRINT observed
- [ ] KILL_REVIEW observed
- [ ] BEHIND observed
- [ ] ON_TRACK observed
- [ ] AHEAD observed
- [ ] 180 minute starvation escalation observed

### Pass Criteria
- [ ] State transitions match doctrine
- [ ] Pace transitions are deterministic
- [ ] Starvation escalation behaves as designed

### Evidence to Capture
- [ ] Governor transition log
- [ ] Pace log
- [ ] Starvation event log
- [ ] Telemetry summary

## V2-V05 - Operational Readiness Review
### Goal
Prove operators can run the system safely without developer assistance.

### Checklist
- [ ] Start system
- [ ] Stop system
- [ ] Restart system
- [ ] Recover system
- [ ] Handle broker disconnect
- [ ] Handle MT5 restart
- [ ] Handle database recovery
- [ ] Handle reconciliation mismatch
- [ ] Runbook is sufficient for standard operator workflows
- [ ] No undocumented manual step is required for normal recovery
- [ ] Operator can explain the safety boundary and stop conditions

### Evidence to Capture
- [ ] Operator notes
- [ ] Runbook steps used
- [ ] Recovery findings
- [ ] Any manual intervention

## Validation Gate Summary
- [ ] V2-V01 PASS
- [ ] V2-V02 PASS
- [ ] V2-V03 PASS
- [ ] V2-V04 PASS
- [ ] V2-V05 PASS

If any box above is not satisfied:
- No-go
- Return to engineering
- Fix the defect
- Re-validate the failed stage

## Current Program Status
- [ ] Architecture complete
- [ ] Engine foundation complete
- [ ] Governance complete
- [ ] Live broker integration complete
- [ ] Production validation started

## Final Reminder
Validate
-> Measure
-> Fix
-> Re-validate
