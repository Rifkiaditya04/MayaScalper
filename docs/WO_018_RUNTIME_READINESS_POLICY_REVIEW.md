# WO-018 - Runtime Readiness Policy Review

## Metadata

- Status: Open
- Objective type: Governance review
- Evidence baseline: 2026-07-07
- Related docs:
  - `docs/WO_017_M5_READINESS_CHARACTERIZATION.md`
  - `docs/M1_FAILURE_CHARACTERIZATION.md`
  - `docs/M5_CLOSE_SEMANTICS.md`
  - `docs/BLUEPRINT_COMPLIANCE_REVIEW.md`
  - `docs/WL2_VERDICT.md`
  - `docs/CONTEST_READINESS_CHECKLIST.md`
  - `docs/WS_RUNTIME_POLICY_AUDIT.md`
  - `docs/WS_RUNTIME_POLICY_CONTRACT.md`
  - `docs/WS_RUNTIME_POLICY_DECISION.md`

## Question

Apakah kebijakan runtime terhadap `ConfigValidationError` pada snapshot readiness M1/M5 masih optimal berdasarkan evidence operasional yang sudah dikarakterisasi?

Dokumen ini tidak mengubah kode dan tidak mengubah kontrak runtime. Tujuannya adalah menilai apakah policy yang sekarang masih layak dipertahankan apa adanya, atau perlu direview untuk tahap implementasi berikutnya.

---

## D1 - Runtime Policy Audit

### Ruang lingkup policy saat ini

Policy steady-state runtime saat ini adalah **fail-fast** untuk exception yang lolos dari `_run_cycle()`.

Evidence yang mendukung hal ini:

- `docs/WS_RUNTIME_POLICY_AUDIT.md`
- `docs/WS_RUNTIME_POLICY_CONTRACT.md`
- `docs/WS_RUNTIME_POLICY_DECISION.md`

Dalam runtime loop:

- `closed-M5 gate skip` adalah satu-satunya path recoverable yang eksplisit di steady-state loop
- `ConfigValidationError` yang lolos dari cycle diperlakukan sebagai fatal runtime error
- exception lain yang lolos dari cycle juga diperlakukan fatal

### Kondisi yang menyebabkan runtime berhenti

Berdasarkan dokumen policy yang sudah ada, runtime berhenti bila ada exception yang keluar dari steady-state cycle, termasuk:

- `ConfigValidationError` dari snapshot readiness M1/M5
- `MT5BridgeError`
- kegagalan snapshot yang lolos dari cycle
- kegagalan reconciliation
- kegagalan execution
- kegagalan persistence
- telemetry failure yang lolos sebagai exception
- unexpected exception

### Hubungan policy dengan kontrak runtime yang dibekukan

Evidence operasional menunjukkan:

- startup synchronization berhasil mencapai `runtime_started`
- snapshot readiness pada startup dapat lolos
- failure kemudian muncul pada runtime cycle berikutnya
- validator tetap fail-loud dan menghentikan runtime ketika snapshot tidak memenuhi minimum closed-bar

Hal ini konsisten dengan dokumen kontrak yang ada:

- `docs/M5_CLOSE_SEMANTICS.md`
- `docs/WO_017_M5_READINESS_CHARACTERIZATION.md`
- `docs/M1_FAILURE_CHARACTERIZATION.md`

### Apakah evidence terbaru memerlukan review policy?

**Ya, untuk review policy, bukan untuk mengubah implementasi secara otomatis.**

Alasannya:

- occurrence M5 dan M1 sudah berulang
- evidence sekarang cukup untuk menyatakan policy ini sering tersentuh oleh kondisi operasional nyata
- namun evidence belum membuktikan bahwa fail-fast itu sendiri salah

Jadi WO-018 berada pada ruang governance, bukan patch.

---

## D2 - Contract Review

### Klasifikasi `ConfigValidationError`

Secara kontrak yang sekarang terdokumentasi, `ConfigValidationError` pada steady-state runtime saat ini diklasifikasikan sebagai **fatal runtime condition** bila ia lolos dari `_run_cycle()`.

### Posisi error terhadap runtime

`ConfigValidationError` diperlakukan sebagai indikator bahwa snapshot readiness untuk cycle tersebut tidak valid.

Dalam policy yang sekarang:

- error ini tidak disembunyikan
- error ini tidak diubah menjadi warning
- error ini tidak di-skip secara otomatis
- error ini menyebabkan runtime berhenti

### Apakah error selalu fatal?

**Dalam policy yang saat ini dibekukan: ya, untuk steady-state cycle yang melempar exception.**

Namun, dokumen ini memisahkan dua hal:

- `startup readiness failure` adalah domain orchestrator/startup
- `steady-state cycle failure` adalah domain runtime loop

Keduanya bisa menghasilkan exception yang terlihat serupa, tetapi policy yang berlaku tetap fail-fast untuk exception yang lolos dari boundary masing-masing.

### Apakah ada kelas error yang layak diperlakukan recoverable?

Berdasarkan evidence yang ada, **belum ada kelas error yang secara kontrak sudah dibuktikan layak menjadi recoverable runtime condition**.

Yang sudah recoverable saat ini hanya:

- closed-M5 gate skip ketika `_run_cycle()` mengembalikan `None`

Ini bukan recovery dari exception, melainkan path normal skip-cycle.

---

## D3 - Decision Matrix

| Condition | Current Decision | Candidate Option | Basis |
| --- | --- | --- | --- |
| Startup snapshot readiness gagal | `EXIT RUNTIME` | tetap `EXIT RUNTIME` | Startup orchestration harus memastikan readiness sebelum masuk steady-state |
| Runtime M1 snapshot kurang minimum closed bar | `EXIT RUNTIME` | `REVIEW` / `WAIT NEXT CLOSE` hanya jika policy diubah | Evidence M1 characterization menunjukkan kondisi ini berulang, tetapi current policy tetap fail-fast |
| Runtime M5 snapshot kurang minimum closed bar | `EXIT RUNTIME` | `REVIEW` / `WAIT NEXT CLOSE` hanya jika policy diubah | Evidence WO-017 menunjukkan kondisi recurrent operasional pada cycle runtime |
| Snapshot failure yang lolos dari `_run_cycle()` | `EXIT RUNTIME` | `RETRY` atau `WAIT NEXT CLOSE` bila contract baru disetujui | Saat ini belum ada contract recoverable untuk snapshot failure |
| `MT5BridgeError` | `EXIT RUNTIME` | tetap `EXIT RUNTIME` | Broker-facing fault harus fail loud pada policy sekarang |
| Reconciliation failure | `EXIT RUNTIME` | `REVIEW` sebelum recovery policy baru | Broker-truth mismatch tidak boleh disenyapkan |
| Persistence failure | `EXIT RUNTIME` | `REVIEW` sebelum recovery policy baru | Recovery deterministik bergantung pada persistence yang valid |
| Telemetry failure yang lolos sebagai exception | `EXIT RUNTIME` | tetap `EXIT RUNTIME` | Audit trail tidak boleh hilang diam-diam |
| Unexpected exception | `EXIT RUNTIME` | tetap `EXIT RUNTIME` | Unknown state harus fail loud |
| Closed-M5 gate skip | `CONTINUE` | tetap `CONTINUE` | Ini path normal yang memang recoverable dalam loop |

Catatan:

- `RETRY`, `WAIT NEXT CLOSE`, dan `SKIP CYCLE` belum menjadi policy eksplisit untuk snapshot readiness failure.
- Jika opsi tersebut diinginkan, itu memerlukan governance dan implementasi baru.

---

## D4 - Recovery Boundary

### Recovery diperbolehkan

Recovery diperbolehkan hanya bila policy secara eksplisit menyebut kondisi tersebut sebagai non-fatal.

Dalam policy saat ini, recovery yang sudah eksplisit adalah:

- closed-M5 gate skip

### Recovery dilarang

Recovery dilarang untuk exception yang lolos dari steady-state cycle dan belum diklasifikasikan sebagai recoverable.

Itu termasuk:

- `ConfigValidationError`
- snapshot failure yang tidak di-handle
- execution failure
- reconciliation failure
- persistence failure
- MT5 bridge failure
- unexpected exception

### Kapan runtime wajib berhenti

Runtime wajib berhenti ketika exception yang belum didefinisikan sebagai recoverable lolos dari boundary cycle.

Ini menjaga:

- determinisme
- broker-truth safety
- auditability
- reproduktibilitas failure

### Batas yang dapat diuji

Boundary ini dapat diuji dengan membandingkan:

- startup readiness telemetry
- runtime cycle telemetry
- `ConfigValidationError` payload
- terminal log
- runtime DB

Jika suatu exception ingin dipulihkan di masa depan, boundary barunya harus didefinisikan sebelum implementasi.

---

## D5 - Impact Assessment

| Component | Impact | Reason |
| --- | --- | --- |
| Runtime Contract | `No Change` | Policy yang sekarang sudah terdokumentasi sebagai fail-fast untuk escaped cycle errors |
| Blueprint | `No Change` | Evidence belum menunjukkan blueprint perlu direvisi |
| B3 | `No Change` | Startup synchronization tetap tervalidasi |
| B4 | `No Change` | Closed-M5 gate tetap tervalidasi |
| WL2 | `No Change` | Lock reclaim dan startup path tetap frozen |
| Validation Program | `Documentation Update` | WO-018 mengklarifikasi policy boundary yang masih berjalan di validation envelope |
| Telemetry | `No Change` | Telemetry yang ada sudah cukup untuk audit policy |
| Deterministic Runtime | `No Change` | Fail-fast tetap menjaga state yang jelas dan auditable |
| Contest Readiness | `No Change` | Checklist tetap `NO` sampai validation envelope selesai |

---

## Evidence Mapping

Evidence yang dipakai untuk review ini:

- `docs/WO_017_M5_READINESS_CHARACTERIZATION.md`
- `docs/M1_FAILURE_CHARACTERIZATION.md`
- `docs/M5_CLOSE_SEMANTICS.md`
- `docs/WS_RUNTIME_POLICY_AUDIT.md`
- `docs/WS_RUNTIME_POLICY_CONTRACT.md`
- `docs/WS_RUNTIME_POLICY_DECISION.md`
- `docs/BLUEPRINT_COMPLIANCE_REVIEW.md`
- `docs/WL2_VERDICT.md`
- `docs/CONTEST_READINESS_CHECKLIST.md`

Tambahan evidence operasional yang sudah dikarakterisasi:

- recurrent M5 runtime occurrences pada `GBPJPY` dan `EURUSD`
- recurrent M1 runtime occurrences pada `GBPJPY`
- startup readiness yang tetap PASS pada run yang relevan
- terminal log reconnect / no-connection context yang sudah dicatat

---

## Recommendation

### Decision A

**Pertahankan policy saat ini.**

`No implementation required.`

Alasan:

- evidence operasional menunjukkan policy fail-fast sering tersentuh, tetapi bukan terbukti salah
- contract saat ini konsisten dengan implementation structure
- recoverable policy untuk snapshot readiness belum didefinisikan secara governed
- perubahan ke policy recoverable akan memerlukan WO-019

---

## Notes

- Dokumen ini sengaja tidak mengubah runtime behavior.
- Dokumen ini juga tidak menganggap recurring occurrence sebagai bukti bahwa fail-fast salah.
- Jika nanti governance ingin memperkenalkan recoverable runtime condition, maka hasil WO-018 ini menjadi baseline keputusan untuk WO-019.
