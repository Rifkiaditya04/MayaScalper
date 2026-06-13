# Validation Report Template V2

## Purpose
Use this template to record one validation run or one validation stage result in a consistent,
audit-friendly format.

## Report Header
- Stage:
- Date UTC:
- Operator:
- Environment:
- Build / Fingerprint:
- Runtime Mode:
- Symbol Set:

## Result
- Result: PASS / FAIL
- Stage Status:
- Go / No-Go Impact:

## Summary
- Short summary of what was validated:
- Short summary of the outcome:

## Evidence
- Start and end time UTC:
- Telemetry summary:
- Reconciliation findings:
- Recovery findings:
- Logs captured:
- Screenshots captured:
- Manual intervention:

## Findings
- Primary findings:
- Secondary findings:
- Ownership or broker-truth issues:
- Exceptions or warnings:

## Corrective Actions
- Immediate corrective action:
- Engineering follow-up:
- Re-validation requirement:

## Next Action
- Next validation stage:
- Preconditions for the next stage:

## Sign-Off
- Operator sign-off:
- Reviewer sign-off:
- Notes:

## Notes
- Keep the report tied to the exact validation stage and runtime mode.
- Do not mix multiple stages into one report unless the failure chain is continuous and explicitly noted.
- Attach or reference the matching stage folder under `validation/V2-V0X/`.
