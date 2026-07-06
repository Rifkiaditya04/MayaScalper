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

## Update Observation

Kondisi `closed_bar_count = 69` pada runtime cycle telah diamati lagi pada run tanggal `2026-06-26`.

Pada run tersebut, startup readiness tercapai terlebih dahulu pada `GBPUSD`, lalu runtime cycle berikutnya menghasilkan kegagalan snapshot pada `GBPJPY` dengan pola closed-bar yang sama.

Dengan demikian, evidence saat ini menunjukkan bahwa fenomena tersebut bersifat recurrent dalam kondisi operasional tertentu, tetapi tetap belum cukup untuk:

- menyimpulkan akar penyebab;
- membuktikan regresi implementasi;
- mengubah status governance.

## Occurrence Matrix

Occurrence berikut adalah kejadian `deployment.market_data_readiness` bertipe `closed_bars_insufficient` untuk timeframe M5 yang muncul pada window evidence terbaru. Dua kejadian M1 pada window yang sama didokumentasikan terpisah di `docs/M1_FAILURE_CHARACTERIZATION.md`.

| Event ID | Date (UTC) | Symbol | TF | Closed/Req | Future | Broker Time (UTC) |
| --- | --- | --- | --- | --- | --- | --- |
| 3253 | 2026-07-02 | GBPJPY | M5 | 68/71 | 2 | 2026-07-02T04:38:45+00:00 |
| 3773 | 2026-07-02 | EURUSD | M5 | 69/71 | 1 | 2026-07-02T04:44:15+00:00 |
| 4649 | 2026-07-02 | GBPJPY | M5 | 69/71 | 1 | 2026-07-02T04:51:15+00:00 |
| 4669 | 2026-07-02 | EURUSD | M5 | 69/71 | 1 | 2026-07-02T05:04:13+00:00 |
| 4689 | 2026-07-02 | EURUSD | M5 | 69/71 | 1 | 2026-07-02T05:04:13+00:00 |
| 4709 | 2026-07-02 | EURUSD | M5 | 69/71 | 1 | 2026-07-02T05:04:13+00:00 |
| 4729 | 2026-07-02 | EURUSD | M5 | 69/71 | 1 | 2026-07-02T05:04:13+00:00 |
| 5659 | 2026-07-02 | GBPJPY | M5 | 69/71 | 1 | 2026-07-02T05:24:38+00:00 |
| 5698 | 2026-07-06 | EURUSD | M5 | 67/71 | 3 | 2026-07-06T02:14:19+00:00 |
| 5717 | 2026-07-06 | GBPJPY | M5 | 69/71 | 1 | 2026-07-06T02:14:19+00:00 |
| 6544 | 2026-07-06 | GBPJPY | M5 | 69/71 | 1 | 2026-07-06T02:20:13+00:00 |
| 8221 | 2026-07-06 | GBPJPY | M5 | 69/71 | 1 | 2026-07-06T02:44:40+00:00 |
| 8602 | 2026-07-06 | GBPJPY | M5 | 69/71 | 1 | 2026-07-06T02:49:31+00:00 |
| 8990 | 2026-07-06 | GBPJPY | M5 | 69/71 | 1 | 2026-07-06T02:53:14+00:00 |
| 9009 | 2026-07-06 | GBPJPY | M5 | 69/71 | 1 | 2026-07-06T02:53:14+00:00 |
| 9028 | 2026-07-06 | GBPJPY | M5 | 69/71 | 1 | 2026-07-06T02:53:14+00:00 |
| 9241 | 2026-07-06 | GBPJPY | M5 | 69/71 | 1 | 2026-07-06T02:59:33+00:00 |
| 10680 | 2026-07-06 | GBPJPY | M5 | 69/71 | 1 | 2026-07-06T03:18:16+00:00 |
| 10767 | 2026-07-06 | GBPJPY | M5 | 69/71 | 1 | 2026-07-06T03:32:28+00:00 |
| 10786 | 2026-07-06 | GBPJPY | M5 | 69/71 | 1 | 2026-07-06T03:39:47+00:00 |
| 11516 | 2026-07-06 | EURUSD | M5 | 69/71 | 1 | 2026-07-06T03:49:11+00:00 |

## Pattern Summary

- Total M5 occurrences in this window: `21`
- GBPJPY M5 occurrences: `14`
- EURUSD M5 occurrences: `7`
- Dominant pattern: `closed_bar_count = 69`, `requested_bars = 71`, `returned_bars = 71`, `future_count = 1`
- Outliers:
  - `3253` with `closed_bar_count = 68`, `future_count = 2`
  - `5698` with `closed_bar_count = 67`, `future_count = 3`
- The two M1 occurrences in the same window are intentionally excluded from this WO-017 table and are documented in `docs/M1_FAILURE_CHARACTERIZATION.md`.
