# Live Runbook Checklist MT5 Live Rebuild

## A. Pre-Flight Checklist
- [ ] Terminal MT5 InstaForex terbuka.
- [ ] Akun yang benar sudah login.
- [ ] Symbol target ada di Market Watch.
- [ ] `MT5_TERMINAL_PATH` benar.
- [ ] `MT5_SYMBOL` benar.
- [ ] Hanya satu proses bot aktif untuk symbol/magic ini.
- [ ] Folder `logs` dan `runtime` bisa ditulis.

## B. Observation Checklist
- [ ] `MT5_ENABLE_ORDER_EXECUTION=0`
- [ ] Startup log bersih.
- [ ] `Session state initialized` muncul.
- [ ] Urutan log M5 bersih.
- [ ] Tidak ada warning persistence mismatch.
- [ ] Tidak ada false positive `KILL_SWITCH_ACTIVE`.
- [ ] Tidak ada blocker aneh yang bertentangan dengan kondisi market.
- [ ] Near-miss telemetry muncul hanya selektif, tidak membanjiri log tiap bar.
- [ ] `STRATEGY TELEMETRY SUMMARY` muncul saat shutdown normal untuk review sesi.
- [ ] `progress_exit_counterfactual_started` / `progress_exit_counterfactual_outcome` muncul jika ada exit karena `progress_below_50pct_after_2_m5`.

## C. Restart Durability Checklist
- [ ] Bot dihentikan dengan `Ctrl + C`.
- [ ] Bot dijalankan ulang.
- [ ] Startup menunjukkan `persistent_loaded=True`.
- [ ] `daily_baseline_equity` tetap masuk akal.
- [ ] `last_m5_anchor` tetap masuk akal.
- [ ] Tidak ada warning corruption / ownership mismatch.

## D. Operator Kill-Switch Checklist
- [ ] Paham lokasi file state: `runtime\mt5_GBPUSD_state.json`
- [ ] Paham lokasi file ack: `runtime\mt5_GBPUSD_ack.json`
- [ ] Paham format ack JSON.
- [ ] Paham bahwa hard DD butuh ack eksplisit untuk resume.
- [ ] Paham bahwa ack me-reset `session_peak_equity` ke equity saat itu.

## E. Before First Live Execution
- [ ] Tidak ada false positive drawdown guard.
- [ ] Tidak ada false positive manual close cooldown.
- [ ] Tidak ada false positive anti-burst setelah restart.
- [ ] Tidak ada warning aneh dari MT5 client.
- [ ] Pahami bahwa broker comment MT5 sekarang dinormalisasi ke short code aman dan reason internal tetap dilihat di log, bukan di payload broker.
- [ ] Operator siap memantau order pertama.
- [ ] Risk per setup sudah Anda set sesuai toleransi nyata.

## F. Recommended Go-Live Sequence
### Stage 1
- [ ] Observation only (`MT5_ENABLE_ORDER_EXECUTION=0`) minimal 2 sesi bersih.

### Stage 2
- [ ] Restart continuity tervalidasi minimal 1 kali.
- [ ] Kill-switch workflow dipahami operator.

### Stage 3
- [ ] Baru aktifkan:
```env
MT5_ENABLE_ORDER_EXECUTION=1
```
- [ ] Gunakan supervised live micro terlebih dahulu.
- [ ] Pertahankan `MT5_LAYER_COUNT=1` bila ingin paling konservatif.
- [ ] Pantau order pertama sampai exit/TP/close classification selesai.

## G. Hard Stop Conditions
- [ ] Muncul warning persistence mismatch.
- [ ] Muncul kill-switch yang tidak dipahami.
- [ ] Bot membuka posisi saat state seharusnya memblok.
- [ ] Runtime tampak bertentangan dengan log decision.

Jika salah satu terjadi: kembalikan ke `MT5_ENABLE_ORDER_EXECUTION=0`.
## Execution Integrity Checks
- Verify TP is attached from actual fill price, not estimated entry assumptions.
- If protection is rejected with `INVALID_STOPS_AFTER_FILL`, do not override manually by widening TP unless strategy review explicitly approves a design change.
- Review `protection_attach_failed` and `protection_rejected_invalid_after_fill` events before approving broader live deployment.
- Review `broker_comment_normalized` debug events jika ada payload recovery/close yang sebelumnya memakai reason internal panjang.
- Verify close retry backoff schedule is set intentionally for the deployment profile.
