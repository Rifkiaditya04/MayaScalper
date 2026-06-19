# State Machine Blueprint Diff

## Pertanyaan Audit

Apakah blueprint V2 melarang `AMBIGUOUS -> EXPIRED`, atau implementasi lebih ketat daripada blueprint?

## Evidence Blueprint

Dokumen blueprint/mapping yang relevan menyebut:

- execution registry memiliki state `AMBIGUOUS` dan `EXPIRED`
- execution must reconcile with broker truth
- explicit reconciliation path after uncertain broker outcomes
- restart ordering menempatkan:
  - restore persisted registry
  - reconcile registry against broker truth
  - restore lifecycle state
  - flatten unresolved ambiguous exposure
  - resume only after healthy snapshot build

Sumber yang relevan:

- `docs/IMPLEMENTATION_ARCHITECTURE_MAPPING_V2.md`
- `FINAL_BLUEPRINT V2.md`

## Hasil Pembacaan

Blueprint:

- mengakui state `AMBIGUOUS`
- mengakui state `EXPIRED`
- menuntut reconciliation terhadap broker truth
- tidak terlihat mendefinisikan larangan eksplisit bahwa unresolved ambiguous entry tidak boleh menjadi expired setelah timeout

## Implementasi Aktual

`tsp_v2/execution.py` menetapkan:

- `AMBIGUOUS` = terminal
- `EXPIRED` = terminal
- `TRANSITION_ALLOWED[AMBIGUOUS] = frozenset()`

Lalu `tsp_v2/recovery/reconcile.py` tetap mencoba:

- `mark_expired(...)` untuk entry unresolved yang melewati `expires_at_utc`

## Diff Kesimpulan

Implementasi lebih ketat daripada blueprint pada jalur ini.

Blueprint mengizinkan adanya reconciliation path untuk ambiguous exposure, tetapi implementasi registry tidak mengizinkan transisi akhir `AMBIGUOUS -> EXPIRED`.

## Verdict

**A. Blueprint benar, implementasi salah**

Catatan:

Masalahnya tampak lebih seperti kontradiksi internal implementasi antara:

- penandaan `AMBIGUOUS`
- timeout reconciliation ke `EXPIRED`
- dan transition validator yang menolak perpindahan itu
