# Validation Program V2

## Status
- Phase: Production Validation
- Scope: Validate the broker-connected runtime end to end
- Rule: No architecture redesign, no new trading doctrine, no new alpha

## Purpose
This document is the source of truth for the validation phase after PATCH-012E.
The goal is to prove that the already-implemented V2 runtime is stable, recoverable,
and operationally safe before any contest or meaningful capital deployment.

## Non-Negotiable Guardrails
- Do not change blueprint doctrine during validation unless a real defect is found.
- Do not add new strategy logic, risk logic, governor logic, or execution doctrine.
- Do not treat validation as feature development.
- Do not skip reconciliation, restart, or shutdown checks.
- Do not move to the next stage until the current stage passes.

## Validation Principles
- Validate with evidence, not assumptions.
- Prefer bounded rehearsals over open-ended runtime when proving a new path.
- Separate runtime stability issues from strategy-quality issues.
- Fail loud on any unexplained position, duplicate order, or recovery mismatch.
- Keep all validation findings readable, reproducible, and auditable.

## Validation Stages

### V2-V01 - Integration Validation
Goal:
- Prove that the full pipeline can start, run, and shut down cleanly.

Scope:
- Market Adapter
- Snapshot Builder
- Regime
- Signal
- Risk
- Governor
- Execution
- Execution Adapter
- Broker path

Minimum scenarios:
- Clean startup
- One bounded live cycle with `--max-cycles`
- Clean shutdown
- Telemetry emission during the cycle

Pass criteria:
- No crash
- No deadlock
- No ownership violation
- No uncaught exception
- No unexpected broker mutation

Fail criteria:
- Any uncaught exception
- Any unresolved reconciliation conflict
- Any duplicate execution path
- Any state transition that violates ownership

### V2-V02 - Recovery Validation
Goal:
- Prove restart doctrine and broker-truth convergence.

Scenarios:
- Kill process while a signal is active
- Kill process after execution is submitted
- Kill process while a position is open
- Disconnect broker and reconnect
- Restart after clean shutdown

Pass criteria:
- Recovery produces MATCHED or another explicit controlled finding
- No ghost position
- No ghost order
- No duplicate order after restart
- Broker truth remains authoritative

Fail criteria:
- Any unresolved divergence after recovery
- Any duplicate order submission
- Any phantom exposure
- Any recovery path that depends on manual guesswork

### V2-V03 - Forward-Test Validation
Goal:
- Prove runtime stability in a demo or paper environment before contest use.

Operational tracking:
- Daily tracking: [VALIDATION_V2_V03_DAILY_TRACKING.md](VALIDATION_V2_V03_DAILY_TRACKING.md)
- Weekly summary: [VALIDATION_V2_V03_WEEKLY_SUMMARY.md](VALIDATION_V2_V03_WEEKLY_SUMMARY.md)

Stage 1:
- Demo account
- Minimum duration: 2 weeks
- Use supervised live micro only

Observations:
- Crash count
- Execution failures
- Recovery events
- Reconciliation mismatches
- Telemetry consistency

Hard stop conditions:
- Unexplained position
- Duplicate order
- Any repeated recovery mismatch

Stage 2:
- Extended forward test
- Minimum duration: 4 weeks

Observations:
- Governor transitions
- Risk control behavior
- Pace transitions
- Starvation handling
- Session behavior

### V2-V04 - Contest Rehearsal
Goal:
- Prove contest behavior before real contest capital is used.

Validate:
- SURVIVE
- NORMAL
- ATTACK
- HUNTER
- CHASE
- PROTECT
- SPRINT
- KILL_REVIEW

Also validate:
- BEHIND
- ON_TRACK
- AHEAD
- 180 minute starvation escalation

Pass criteria:
- State transitions match doctrine
- Pace transitions are deterministic
- Starvation escalation behaves as designed

### V2-V05 - Operational Readiness Review
Goal:
- Prove that operators can run the system safely without developer assistance.

Checklist:
- Start system
- Stop system
- Restart system
- Recover system
- Handle broker disconnect
- Handle MT5 restart
- Handle database recovery
- Handle reconciliation mismatch

Pass criteria:
- Runbook is sufficient for standard operator workflows
- No undocumented manual step is required for normal recovery
- Operator can explain the safety boundary and stop conditions

## Validation Gates

### Go / No-Go for Contest Use
All of the following must pass:
- V2-V01 PASS
- V2-V02 PASS
- V2-V03 PASS
- V2-V04 PASS
- V2-V05 PASS

If any stage fails:
- No-go
- Return to engineering
- Fix the defect
- Re-validate the failed stage

## Evidence Requirements
For every validation run, capture:
- Start time and end time UTC
- Runtime mode
- Symbol set
- Operator notes
- Telemetry summary
- Reconciliation findings
- Recovery findings
- Any uncaught exceptions
- Any manual intervention

## Recommended Execution Order
1. Integration validation
2. Recovery validation
3. Forward-test validation
4. Contest rehearsal
5. Operational readiness review

## Current Program Status
- Architecture: complete
- Engine foundation: complete
- Governance: complete
- Live broker integration: complete
- Production validation: not started

## Final Rule
Do not add new doctrine while validation is still in progress.
The correct loop is:

validate
-> measure
-> fix
-> re-validate
