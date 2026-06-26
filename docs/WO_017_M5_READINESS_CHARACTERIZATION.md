# WO-017 - M5 Readiness Characterization

## Metadata

- Status: Operational Characterization
- Evidence Baseline: 2026-06-25
- Baseline Commit: `43af549`
- Related Checklist:
  - Runtime Reliability
  - Validation Program V2-V03

## Question

Apakah `ConfigValidationError` berikut menunjukkan regresi terhadap workstream M5 yang telah dinyatakan `PASS`, atau merupakan kejadian operasional yang masih sesuai dengan kontrak runtime?

```text
ConfigValidationError:
Not enough closed bars for timeframe M5: need at least 70
```

---

## Evidence

### Governance

- `docs/M5_CLOSE_SEMANTICS.md`
  - M5 menggunakan `cycle_time_utc` sebagai cutoff.
  - `build_market_snapshot()` adalah validator fail-fast.
  - Kondisi `70 raw bars -> 69 closed bars` merupakan perilaku yang dapat terjadi ketika snapshot dibangun saat candle M5 belum memenuhi syarat closed-bar.
- `docs/BLUEPRINT_COMPLIANCE_REVIEW.md`
  - Runtime lifecycle, snapshot contract, dan closed-bar contract berstatus `COMPLIANT`.
- `docs/WL2_VERDICT.md`
  - WL2 berstatus `PASS` dan telah dibekukan.
- `docs/CONTEST_READINESS_CHECKLIST.md`
  - Validation Program masih `IN PROGRESS`.
  - `Contest Ready = NO`.

### Runtime Sequence

Runtime mengikuti urutan berikut:

```text
DeploymentRuntime.start()

-> startup synchronization

-> startup snapshot readiness

-> runtime_started

-> runner.run()

-> runtime cycle

-> build_market_snapshot(cycle_time_utc)
```

Startup berhasil mencapai snapshot readiness dan memasuki runtime.

Pada runtime cycle berikutnya, snapshot dibangun ulang menggunakan `cycle_time_utc` saat itu.

### Runtime Occurrence

Telemetry menunjukkan:

```text
deployment.startup_sync
snapshot_ready = true
```

diikuti:

```text
runtime_started
```

Kemudian runtime menghasilkan:

```text
deployment.market_data_readiness
```

dengan:

```text
symbol = GBPJPY

requested_bars = 71
returned_bars = 71

closed_bar_count = 69
minimum_closed_bar_count = 70

forming_count = 1
future_count = 1

payload_health = GREEN
```

Validator kemudian mengeluarkan:

```text
ConfigValidationError
```

sesuai kontrak snapshot.

### Primary Evidence

- `validation/RV1/logs/20260625T012551Z-demo-all-day.log`
- `docs/EVIDENCE_REGISTER.md`
- `runtime/forward_live/db/tsp_v2_runtime.sqlite3`

---

## Finding

Evidence menunjukkan bahwa:

- startup synchronization berhasil;
- runtime berhasil dimulai;
- kegagalan terjadi pada pembangunan snapshot di runtime cycle berikutnya;
- validator menghentikan runtime karena `closed_bar_count` berada di bawah batas minimum.

Observasi ini hanya berlaku untuk runtime cycle yang terdokumentasi pada evidence ini dan tidak menggeneralisasi seluruh perilaku runtime di semua kondisi pasar.

Evidence tidak menunjukkan bahwa:

- startup synchronization mengalami regresi;
- runtime lifecycle menyimpang dari blueprint;
- closed-bar contract berubah;
- implementasi workstream M5/WL2 tidak lagi sesuai dengan kontrak yang telah diterima.

---

## Classification

### Workstream Contract

Tidak ditemukan evidence yang membatalkan status `PASS` pada workstream M5/WL2.

Kontrak yang telah dibekukan tetap berlaku.

### Runtime Occurrence

Run ini merupakan kejadian operasional di mana snapshot readiness M5 tidak terpenuhi pada runtime cycle tertentu.

Kejadian tersebut sesuai dengan perilaku validator fail-fast yang telah didefinisikan oleh kontrak runtime.

---

## Governance Impact

- Blueprint: `No change`
- Runtime Contract: `No change`
- Execution Contract: `No change`
- Validation Program: `IN PROGRESS`
- Contest Ready: `NO`

Checklist Impact:

- Affected:
  - Runtime Reliability (observation only)
  - Validation Program V2-V03
- Not affected:
  - Architecture Baseline
  - B3
  - B4
  - WL2

Status workstream:

- B3: `PASS`
- B4: `PASS`
- WL2: `PASS`

Tidak terdapat evidence yang mengharuskan reopening workstream yang telah dibekukan.

---

## Limitations

Evidence ini mendokumentasikan satu kejadian runtime yang tervalidasi dan dibandingkan dengan campaign 20-sampel sebelumnya yang tidak mereproduksi kondisi yang sama.

Karena itu, evidence saat ini cukup untuk karakterisasi operasional, tetapi belum cukup untuk:

- menyimpulkan akar penyebab;
- menetapkan pola kejadian;
- mengubah status governance;
- mengubah kontrak runtime atau blueprint.

Observasi tambahan tetap diperlukan sebagai bagian dari Validation Program.
