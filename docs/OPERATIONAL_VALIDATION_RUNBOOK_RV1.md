# Operational Validation Runbook RV1

## Purpose

Runbook ini mengoperasionalkan `docs/CONTEST_READINESS_CHECKLIST.md` untuk forward validation harian.

Runbook ini tidak mengubah blueprint, validation program, runtime policy, atau contract apa pun.

## Scope

- Evidence collection
- Forward runtime observation
- Recovery rehearsal
- Daily review
- Escalation when a new reproducible issue appears

## Prerequisites

Sebelum memulai:

- Architecture baseline masih frozen.
- Baseline commit sesuai metadata checklist.
- Build yang dijalankan sesuai baseline yang disetujui.
- Environment dan broker setup sesuai baseline validasi.
- Tidak ada perubahan kode yang belum direview.

Jika salah satu tidak terpenuhi, hentikan dan review governance terlebih dahulu.

## Daily Procedure

### 1. Startup Verification

Sebelum runtime berjalan:

- Verifikasi startup berhasil.
- Verifikasi broker connection.
- Verifikasi lock acquisition.
- Verifikasi snapshot initialization.
- Verifikasi startup reconciliation.

Catat setiap kegagalan dan referensi telemetry terkait.

### 2. Forward Runtime

Biarkan runtime berjalan normal.

Selama operasi:

- jangan tuning strategi;
- jangan mengubah parameter untuk mengejar hasil sementara;
- jangan intervensi manual kecuali untuk keselamatan operasional.

Fokus utama adalah observasi.

### 3. Evidence Collection

Kumpulkan evidence bila muncul:

- startup telemetry
- runtime cycle
- reconciliation
- recovery
- `execution_filled`
- `execution_rejected`
- persistence restore
- MT5 reconnect
- restart recovery

Setiap evidence harus bisa ditelusuri kembali ke log atau telemetry source.

### 4. Recovery Rehearsal

Lakukan rehearsal sesuai jadwal validasi, misalnya:

- restart MT5;
- restart runtime;
- reconnect broker;
- recovery posisi;
- persistence restore setelah restart.

Setiap rehearsal harus dicatat sebagai evidence terpisah.

### 5. Daily Review

Di akhir hari:

- tinjau log;
- tinjau telemetry;
- identifikasi regresi;
- identifikasi evidence baru;
- petakan evidence ke item pada checklist.

Status checklist hanya boleh berubah jika evidence baru cukup kuat.

## Change Rules

Selama fase ini, jangan:

- mengubah blueprint;
- mengubah runtime contract;
- mengubah execution policy;
- mengubah validation program;
- membuka workstream arsitektur baru.

Perubahan hanya dipertimbangkan bila ada:

- regression yang dapat direproduksi;
- defect kritis;
- requirement baru yang disetujui melalui governance.

## Escalation Criteria

Segera lakukan review jika terjadi salah satu kondisi berikut:

- runtime crash yang tidak diharapkan;
- recovery gagal;
- broker reconciliation tidak konsisten;
- kehilangan state;
- pelanggaran contract runtime;
- perubahan environment yang mengubah validation envelope.

## Closure Routine

Pada akhir periode review:

1. Perbarui repositori evidence.
2. Evaluasi `Open Evidence`.
3. Evaluasi `Known Deferred Items`.
4. Tinjau `Residual Risks`.
5. Tentukan apakah ada dasar untuk mengubah status item pada checklist.

Jika belum ada evidence baru yang cukup, status checklist tetap apa adanya.

## Relation to Governance

Dokumen ini berada di bawah:

- `FINAL_BLUEPRINT V2.md`
- `docs/VALIDATION_PROGRAM_V2.md`
- `docs/BLUEPRINT_COMPLIANCE_REVIEW.md`
- `docs/CONTEST_READINESS_CHECKLIST.md`

Fungsinya adalah menjembatani decision gate operasional dengan pekerjaan harian yang menghasilkan evidence.
