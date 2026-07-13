# PATCH-DR001A - Broker Reconciliation Classification Fix

## Metadata

- Related Defect: `DR-001`
- Scope: `tsp_v2/recovery/reconcile.py`
- Status: Implemented and verified
- Evidence Baseline: 2026-07-13

---

## Summary

`BrokerReconciliationRuntime.reconcile()` now skips `EXPIRED` / `CANCELLED` execution registry entries when they have no `broker_ticket`.

The change is intentionally narrow:

- active or pending entries still participate in missing-broker reconciliation;
- expired or cancelled entries with a broker ticket still participate in reconciliation;
- expired or cancelled entries without a broker ticket no longer inflate `missing_broker_count`.

---

## Before

`missing_broker_count` included terminal `EXPIRED` / `CANCELLED` entries even when they had no broker ticket and no longer represented a live broker candidate.

This kept `broker_reconciliation` in `RED` and `ready_to_resume = false` across the RV1 July campaign.

---

## After

The broker-missing check now excludes only:

- `ExecutionRegistryState.EXPIRED`
- `ExecutionRegistryState.CANCELLED`
- `broker_ticket is None`

All other entries still follow the original reconciliation rule.

---

## Verification

Regression coverage added in `tsp_v2/tests/test_broker_reconciliation.py`:

- two terminal entries without broker tickets are not counted as `MISSING_BROKER`;
- one active entry without broker ticket is still counted;
- the rest of the reconciliation behavior remains unchanged.

Validation result:

- `python -m unittest tsp_v2.tests.test_broker_reconciliation` - PASS
- `python -m unittest discover tsp_v2.tests` - PASS

---

## Notes

This patch does not change persistence lifecycle, archival behavior, or any runtime policy outside broker reconciliation accounting.
