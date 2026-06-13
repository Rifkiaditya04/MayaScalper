# Operational Runbook MT5 Live Rebuild

## Tujuan
Dokumen ini adalah panduan operasional untuk menjalankan bot MT5 live rebuild secara aman, konsisten, dan bisa diaudit.

## Status Readiness Saat Ini
- Paper / demo: YES
- Live micro unattended: YES
- Meaningful live capital (supervised): YES
- Fully autonomous funded: belum final

Catatan:
- Sistem sudah kuat secara strategy, execution guard, drawdown guard, persistence, dan operator kill-switch.
- Sistem masih mengasumsikan satu proses aktif per symbol/magic.

## Mode Operasi
### 1. Observation mode
Gunakan:
```env
MT5_ENABLE_ORDER_EXECUTION=0
```
Tujuan:
- validasi signal
- validasi blocker
- validasi persistence
- validasi restart continuity
- validasi log audit

### 2. Supervised live execution
Gunakan:
```env
MT5_ENABLE_ORDER_EXECUTION=1
```
Hanya dipakai jika semua checklist live sudah lolos.

## Normal Startup
1. Pastikan terminal MT5 InstaForex terbuka dan akun sudah login.
2. Pastikan symbol target ada di Market Watch.
3. Pastikan hanya satu proses bot aktif untuk symbol/magic ini.
4. Jalankan bot.
5. Cek log startup:
   - `MT5 initialized successfully`
   - `Session attached ...`
   - `Session state initialized ...`
   - `persistent_loaded=True` pada restart normal berikutnya

## Normal Shutdown
Stop bot dengan:
```powershell
Ctrl + C
```

Jangan gunakan putus internet sebagai cara stop. Itu bukan shutdown yang bersih.

## File Runtime Penting
### 1. Persistent state
Path:
```text
D:\Valcon\MT5\runtime\mt5_GBPUSD_state.json
```
Dipakai untuk menyimpan:
- drawdown state
- kill-switch state
- session peak equity
- direction lock
- anti-burst reference
- manual close cooldown
- pause state

### 2. Operator acknowledge file
Path:
```text
D:\Valcon\MT5\runtime\mt5_GBPUSD_ack.json
```
Dipakai hanya saat `manual_ack_required=true`.

## Operator Kill-Switch Workflow
### Saat hard drawdown trigger
Perilaku sistem:
1. `trading_disabled=True`
2. `manual_ack_required=True`
3. bot mencoba flatten posisi milik bot
4. trading tetap diblok lintas restart
5. log `KILL_SWITCH_ACTIVE ...`

### Cara operator membuka kill-switch
Buat file:
```json
{
  "acknowledge": true,
  "symbol": "GBPUSD",
  "magic_number": 20260508,
  "reason": "operator_resume_after_review"
}
```
Simpan ke:
```text
D:\Valcon\MT5\runtime\mt5_GBPUSD_ack.json
```

Efek saat file valid diproses:
- `trading_disabled` dibersihkan
- `manual_ack_required` dibersihkan
- `manual_ack_timestamp` diisi
- `session_peak_equity` di-reset ke equity saat itu
- file ack dihapus otomatis

## Restart Expectations
### Restart sehat
Setelah `Ctrl + C` lalu run ulang, log startup seharusnya menunjukkan:
- `persistent_loaded=True`
- tidak ada warning corruption/ownership mismatch
- `last_m5_anchor` pulih masuk akal

### Restart tidak sehat
Waspadai jika muncul:
- `Persistent state load failed`
- `Persistent state ownership mismatch`
- `Persistent state version mismatch`
- `Persistent trading_disabled loaded without reason`
- `Persistent manual_ack_required loaded without reason`

## Execution Safety Boundary
- TP broker-side sekarang dihitung dari actual fill price, bukan estimated entry.
- Jika target TP menjadi invalid setelah actual fill terhadap broker `min_stop_distance`, bot akan reject sebagai deterministic non-retryable failure.
- Bot tidak akan silently widen TP untuk mengejar validasi broker, agar strategy economics tetap immutable.
- TP attach retry hanya untuk failure yang diklasifikasikan retryable di layer MT5 client.
- Semua payload `order_send` sekarang memakai broker comment yang dinormalisasi terpusat ke short code MT5-safe; reason internal tetap disimpan di log/state.
- Rule `progress_below_50pct_after_2_m5` sekarang dihitung dari `entry_m5_anchor` berbasis candle M5 closed, bukan perbandingan wall-clock langsung.

## Event Log Yang Wajib Dipahami
### Informational
- `Session state initialized`
- `M5 candle closed`
- `HTF DEBUG`
- `SETUP DEBUG`
- `No valid setup`
- `Valid setup detected`
- `event="near_miss_sampled"`
- `event="near_miss_outcome"`
- `event="progress_exit_counterfactual_started"`
- `event="progress_exit_counterfactual_outcome"`
- `STRATEGY TELEMETRY SUMMARY`

### Guard / block
- `[ENTRY BLOCK] reason=manual_ack_required`
- `[ENTRY BLOCK] reason=hard_drawdown_guard`
- `[ENTRY BLOCK] reason=position_cap`
- `[ENTRY BLOCK] reason=anti_burst`
- `[ENTRY BLOCK] reason=max_layers_direction`
- `[ENTRY BLOCK] reason=atr_spacing`

### State machine
- `[LOCK] ...`
- `[UNLOCK] ...`
- `[FRESHNESS] reset detected`
- `[FRESHNESS] reclaim confirmed`

### Execution / recovery observability
- emergency close retry backoff sekarang mengikuti `MT5_POSITION_CLOSE_RETRY_BACKOFF_SCHEDULE` mis. `5,10,20,40,60`.
- `event="protection_rejected_invalid_after_fill"`
- `event="protection_attach_failed"`
- `event="emergency_close_subattempt_failed"`
- `event="emergency_close_failed"`
- `event="emergency_close_retry_scheduled"`
- `event="emergency_close_succeeded"`
- `event="kill_switch_escalated"`
- `event="startup_unprotected_position_detected"`
- `event="broker_comment_normalized"` (debug, saat reason internal dipetakan ke broker comment MT5-safe)
- `event="progress_exit_evaluation"`
- `event="progress_exit_triggered"`

### Capital protection
- `Soft drawdown pause activated`
- `Hard drawdown guard triggered`
- `Hard drawdown flatten`
- `KILL_SWITCH_ACTIVE`
- `Operator acknowledge accepted`

## Strategy Evaluation Telemetry
- Patch telemetry ini read-only: tidak mengubah scoring, gating, execution path, atau risk decision.
- Counter blocker direkam sebagai `all blockers` dan `primary blockers` agar distribusi sesi tidak bias hanya oleh urutan string blocker.
- Near-miss sample direkam saat keputusan diblok tetapi skor kandidat tetap tinggi (default `score >= 5`) dengan sample cap runtime.
- Counterfactual snapshot dicatat selektif untuk near-miss pada horizon `15m`, `30m`, dan `60m`.
- Progress exits dengan reason `progress_below_50pct_after_2_m5` sekarang juga punya counterfactual slice khusus untuk mengukur MFE/MAE 15m/30m/60m, TP asli kena atau tidak dalam 60 menit, dan interaksi direction lock sesudah exit.
- Ringkasan agregat akan ditulis saat shutdown normal (`Ctrl + C`) lewat `STRATEGY TELEMETRY SUMMARY`.

## Operational Constraints Saat Ini
- Single-process assumption: satu symbol/magic hanya satu proses bot.
- Belum ada PID/file lock guard.
- Ack file name masih symbol-scoped; validasi ownership tetap dilakukan lewat `magic_number` di isi file.
- Flatten saat hard DD masih best-effort, belum ada retry escalation workflow khusus.

## Kapan MT5_ENABLE_ORDER_EXECUTION=1 Layak Dipakai
### Layak dipakai untuk supervised live micro jika:
- persistence restart sudah terbukti (`persistent_loaded=True`)
- tidak ada false positive kill-switch atau pause state
- tidak ada corruption/ownership warning
- operator siap memantau sesi pertama
- Anda menerima bahwa first live execution build ini masih perlu audit runtime order path

### Belum layak jika:
- restart continuity belum bersih
- log masih menunjukkan state mismatch
- Anda belum siap memonitor sesi live pertama
- Anda ingin mode fully autonomous funded
