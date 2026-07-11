# DR-001 - Broker Reconciliation Missing-Broker Misclassification

## Metadata

- Status: Confirmed
- Severity: High
- Affected Component: `tsp_v2/recovery/reconcile.py`
- Scope: Accounting/reconciliation only. Tidak berkaitan dengan WO-017 (snapshot readiness) maupun WO-018 (runtime policy). Tidak membuka kembali governance yang sudah ditutup pada kedua dokumen tersebut.
- Evidence Baseline: 2026-07-07 (runtime db `runtime/forward_live/db/tsp_v2_runtime.sqlite3`)
- Related Docs:
  - `docs/WO_017_M5_READINESS_CHARACTERIZATION.md` (independen, tidak terkait)
  - `docs/WO_018_RUNTIME_READINESS_POLICY_REVIEW.md` (independen, tidak terkait)
  - `docs/EVIDENCE_REGISTER.md`
  - `docs/PATCH_ISSUE_REGISTER_V2.md`

---

## Statement

Broker reconciliation menghitung execution registry entry yang secara operasional tidak lagi memiliki representasi broker (`EXPIRED` dengan `broker_ticket = NULL`) sebagai `MISSING_BROKER`, sehingga status reconciliation tetap berada pada kondisi conflict.

Yang bermasalah bukan status `EXPIRED` itu sendiri, melainkan aturan klasifikasi pada proses reconciliation yang tidak membedakan entry tersebut dari entry aktif/pending.

---

## Evidence

### Kode

`tsp_v2/recovery/reconcile.py:140-156` - loop pencocokan broker mengiterasi seluruh `local_registry` tanpa filter status:

```python
for entry in local_registry:
    broker_match = _find_broker_record(entry, broker_registry_index)
    if broker_match is None:
        missing_broker_count += 1
        findings.append(BrokerReconciliationFinding(
            status="MISSING_BROKER",
            ...
        ))
```

Bandingkan dengan `tsp_v2/recovery/reconcile.py:112-116`, yang untuk keperluan symbol-lock **sudah** mengecualikan `EXPIRED`/`CANCELLED`:

```python
if entry.expires_at_utc is not None and entry.state not in {
    ExecutionRegistryState.EXPIRED,
    ExecutionRegistryState.CANCELLED,
}:
    registry.symbol_locks_until_utc[entry.symbol.upper()] = entry.expires_at_utc
```

Dua bagian fungsi yang sama menggunakan aturan klasifikasi berbeda untuk kategori entry yang sama - inkonsistensi inilah sumber defect.

Tidak ditemukan fungsi cleanup/purge/delete untuk tabel `execution_registry` di seluruh codebase (`DELETE FROM execution_registry`, `prune`, `cleanup`, `purge` - nihil hasil pencarian). Entry yang sudah `EXPIRED` tetap berada di `local_registry` tanpa batas waktu dan dievaluasi ulang di setiap siklus reconciliation berikutnya.

### Database (`runtime/forward_live/db/tsp_v2_runtime.sqlite3`)

`execution_registry` - 2 baris, keduanya:

| setup_id | submission_uuid | symbol | direction | state | broker_ticket |
| --- | --- | --- | --- | --- | --- |
| `4697e130320580b4a3cb` | `ef1aaabcc453c1a38718d6f5` | GBPUSD | LONG | EXPIRED | NULL |
| `c8a6e0f286afa844995a` | `bb85e1cffcb617af640bb04b` | GBPUSD | LONG | EXPIRED | NULL |

Kedua `submission_uuid` di atas cocok dengan telemetry `execution_rejected` bertanggal `2026-06-19`, dengan `broker_code: "API_HUNG"` dan `classification: "ESCALATE_KILL_REVIEW"` - order tidak pernah mendapat konfirmasi broker, sehingga wajar tidak memiliki representasi broker.

`health_state` (snapshot terakhir, `2026-07-07T03:00:09.236744+00:00`):

```json
{"account_status": "MATCHED", "missing_broker_count": 2, "ready_to_resume": false, "orphan_position_count": 0}
```

`recovery_events` periode `2026-07-02` s/d `2026-07-08`: `broker_reconciliation/conflict` = 1593 kejadian, tersebar di 32 sesi runtime.

Riwayat `missing_broker_count` dari `recovery_events` (stage `broker_reconciliation`, outcome `conflict`):

- 5 baris paling awal (mulai `2026-06-02T03:19:51`): `missing_broker_count = 1`
- 5 baris paling akhir (`2026-07-07T02:59:22`): `missing_broker_count = 2`

Konsisten dengan kronologi: satu entry EXPIRED sudah ada sebelum awal Juni (`missing_broker_count = 1`), entry kedua bertambah sekitar `2026-06-19` (bertepatan dengan kedua `execution_rejected`/`API_HUNG` di atas), dan sejak itu `missing_broker_count = 2` bertahan tanpa perubahan sampai akhir campaign RV1 (`2026-07-07`).

---

## Impact

`critical_divergence = bool(state_divergence_count or orphan_position_count or missing_broker_count)` (`reconcile.py:351`) - nilai `missing_broker_count` nonzero membuat reconciliation selalu berstatus critical, terlepas dari kondisi lain. Ini membuat `health_state.broker_reconciliation` tetap `RED` dan `ready_to_resume` tetap `false` secara persisten, independen dari status M1/M5 snapshot readiness (WO-017) maupun kebijakan fail-fast (WO-018).

Dengan kata lain: sekalipun WO-017/WO-018 sepenuhnya diselesaikan, sistem tidak akan pernah mencapai `ready_to_resume = true` selama kedua entry `EXPIRED` ini tetap berada di `execution_registry` tanpa pengecualian pada proses reconciliation.

---

## Options (belum dipilih)

### Opsi A - Exclude pada reconciliation

Mengecualikan entry berstatus `EXPIRED`/`CANCELLED` dengan `broker_ticket IS NULL` dari perhitungan `missing_broker_count` di `reconcile.py:140-156`, konsisten dengan pengecualian yang sudah diterapkan untuk symbol-lock di baris 112-116. Perubahan terbatas pada logika klasifikasi, dampak minimal.

### Opsi B - Archive/purge

Menambahkan mekanisme archival/purge untuk entry `EXPIRED`/`CANCELLED` yang telah melewati `expires_at_utc` sekian lama. Ini adalah perubahan lifecycle data - jika `execution_registry` berfungsi sebagai audit log permanen, purge memerlukan peninjauan desain persistence dan kebijakan audit terlebih dahulu.

Kedua opsi menyelesaikan masalah yang berbeda dan tidak saling eksklusif. Tidak ada perubahan kode yang dilakukan sampai ada keputusan eksplisit.

---

## Status Kerja

- [x] Defect dikonfirmasi dengan evidence kode + database + telemetry.
- [ ] Keputusan Opsi A / Opsi B / kombinasi keduanya.
- [ ] Implementasi (setelah keputusan).
- [ ] Verifikasi ulang `ready_to_resume` pasca-perbaikan.
