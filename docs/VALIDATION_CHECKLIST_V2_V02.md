# Validation Checklist V2 V02

## Purpose
Use this operational checklist to execute Recovery Validation without interpretive drift.
This stage proves restart doctrine, recovery bootstrap, and broker-truth convergence.

## Objective
Validate that the following doctrines remain correct under runtime failure:
- Recovery Doctrine
- Persistence Doctrine
- Broker Truth Doctrine

## Global Pass Criteria
All scenarios must satisfy the following:
- No ghost position
- No ghost order
- No duplicate submission
- No permanent state divergence
- Broker truth remains authoritative
- Recovery bootstrap succeeds
- Reconciliation succeeds

## Scenario A - Idle Kill + Restart
### Setup
- [ ] No open positions
- [ ] No active orders
- [ ] Runtime active and normal

### Execution
- [ ] Run `run_v2 start`
- [ ] Let several cycles complete
- [ ] Kill the process forcefully

### Recovery
- [ ] Restart runtime

### Validation
- [ ] Startup succeeds
- [ ] Recovery bootstrap succeeds
- [ ] Reconciliation succeeds
- [ ] Runtime returns to active state

### Evidence
- [ ] Save logs under `validation/V2-V02/logs/`
- [ ] Save telemetry under `validation/V2-V02/telemetry/`
- [ ] Add report under `validation/V2-V02/reports/`

## Scenario B - Active Signal Kill + Restart
### Setup
- [ ] A signal is active
- [ ] No execution has been submitted yet

### Execution
- [ ] Kill the process

### Recovery
- [ ] Restart runtime

### Validation
- [ ] The old signal does not cause a duplicate action
- [ ] The registry remains consistent

### Pass Criteria
- [ ] No duplicate intent
- [ ] No duplicate submission

## Scenario C - Open Position Kill + Restart
### Setup
- [ ] At least one position is open

### Execution
- [ ] Kill the process

### Recovery
- [ ] Restart runtime

### Validation
- [ ] Broker position is discovered
- [ ] Reconciliation finds the position
- [ ] Local state is restored

### Critical Check
- [ ] Local state does not remain `no position` while broker has a position

### Pass Criteria
- [ ] `reconciliation_ready = true`

## Scenario D - Broker Disconnect + Reconnect
### Setup
- [ ] Runtime is active

### Execution
- [ ] Disconnect the broker
- [ ] Example: MT5 shutdown or network block

### Validation
- [ ] Disconnect is detected
- [ ] Telemetry is recorded
- [ ] Runtime remains non-corrupt

### Recovery
- [ ] Reconnect the broker

### Pass Criteria
- [ ] Runtime recovers
- [ ] Reconnect succeeds
- [ ] Reconciliation succeeds

## Scenario E - Recovery Bootstrap + Reconciliation
### Setup
- [ ] Persistence contains execution registry entries
- [ ] Persistence contains telemetry
- [ ] Persistence contains account state

### Execution
- [ ] Restart runtime

### Validation
- [ ] `preflight` runs
- [ ] `connect` runs
- [ ] Recovery bootstrap runs
- [ ] `reconcile` runs
- [ ] `activate_loop` runs

### Critical Check
- [ ] Broker truth wins whenever a conflict exists

## Evidence Requirements
For every scenario, capture the following:
- [ ] Report under `validation/V2-V02/reports/`
- [ ] Logs under `validation/V2-V02/logs/`
- [ ] Telemetry under `validation/V2-V02/telemetry/`
- [ ] Screenshots if available

## Stage Pass Criteria
V2-V02 is PASS only if all scenarios pass:
- [ ] Scenario A PASS
- [ ] Scenario B PASS
- [ ] Scenario C PASS
- [ ] Scenario D PASS
- [ ] Scenario E PASS

And none of the following are observed:
- [ ] Ghost position
- [ ] Ghost order
- [ ] Duplicate submission
- [ ] Permanent divergence

## Stage Fail Criteria
Immediately FAIL if any of the following are observed:
- [ ] Broker truth violation
- [ ] Ghost position
- [ ] Ghost order
- [ ] Duplicate execution
- [ ] Recovery corruption

## Output Artifact
- [ ] `validation/V2-V02/reports/VALIDATION_REPORT_V2_V02.md`

## Final Reminder
Validate
-> Measure
-> Fix
-> Re-validate

