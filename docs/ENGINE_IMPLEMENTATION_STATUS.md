# Engine Implementation Status

Dokumen ini merangkum apa yang sudah diterapkan dan apa yang belum diterapkan dari engine yang kita sepakati selama desain dan audit live.

## A. Sudah Diterapkan
### Strategy / decision core
- HTF bias H1/H4 dengan `HOLD -> NO TRADE`
- M1 continuation score
- M5 MA7 location quality
- TP feasibility vs effective broker-safe TP
- follow-through forecast
- M5-driven logging dan evaluation cadence
- read-only strategy telemetry counters, near-miss sampling, dan counterfactual 15m/30m/60m outcome snapshots
- focused progress-exit counterfactual telemetry untuk menguji apakah `progress_below_50pct_after_2_m5` + direction lock merusak expectancy

### State machine / behavioral control
- directional lock setelah failure exit
- unlock hanya via HTF opposite atau structure break
- lock failsafe timeout
- freshness reset -> reclaim
- no same-direction add-on saat `layer_count <= 1`
- layering controls:
  - max layers per direction
  - ATR spacing vs nearest same-side layer
  - opposite-side active block
  - anti-burst before execution

### Execution hard safety
- staged close-retry backoff schedule
- hard position cap
- duplicate signal same M5 guard
- startup entry guard
- no execution when `MT5_ENABLE_ORDER_EXECUTION=0`

### Position management
- attach TP after entry using actual fill price as source of truth
- TP invalid-after-fill rejection without silent widening
- retcode-aware TP attach retry policy with explicit BUY/SELL side validation
- filling mode retry
- position ticket resolution
- break-even / profit protection dasar
- 2-candle M5 failure exit dengan anchor-based M5 bar counting
- expected close tracking
- broker TP close detection
- manual/external close detection

### Capital protection
- soft drawdown guard
- hard drawdown guard
- loss streak pause
- hard DD flatten attempt

### Durability / operations
- persistent state model
- persisted drawdown state
- persisted session peak equity
- persisted direction locks
- persisted anti-burst reference
- persisted manual close cooldown
- operator kill-switch workflow via ack file
- monotonic runtime timing with UTC fallback after restore

## B. Diterapkan Sebagian / Deliberately Conservative
- hard DD flatten masih best-effort, belum ada retry escalation tree khusus
- close-path observability sekarang structured per filling/retcode/price attempt
- broker comments untuk semua order_send sekarang dinormalisasi terpusat ke MT5-safe short codes
- operator ack workflow baru via file token, belum ada CLI/admin command formal
- monotonic hanya untuk runtime aktif; state hasil restore tetap memakai fallback UTC
- layering belum dibuat "hanya boleh jika active directional lock" karena lock saat ini adalah state blokir, bukan permission state
- timeframe masih hardcoded ke M1/M5/H1/H4, belum env-configurable

## C. Belum Diterapkan
- PID/file-lock guard untuk mencegah accidental double instance
- formal process mutex / single-owner enforcement otomatis
- spread filter eksplisit sebagai hard blocker terpisah
- news/session filter
- replay harness / backtest-style validation harness khusus rebuild ini
- operator workflow yang lebih kaya dari ack file sederhana
- multi-symbol orchestration

## D. Keputusan Yang Sengaja Tidak Dipakai
- grid
- martingale
- hedge recovery
- multi-strategy expansion sebagai fondasi awal

## E. Readiness Verdict Saat Ini
- Demo / paper: YES
- Live micro unattended: YES
- Meaningful supervised capital: YES
- Fully autonomous funded: belum final

## F. Kapan Mengaktifkan MT5_ENABLE_ORDER_EXECUTION=1
Rekomendasi saya:
- belum perlu diaktifkan hanya karena engine sudah compile dan guard lengkap
- aktifkan hanya setelah:
  - observation sessions bersih
  - restart persistence tervalidasi
  - operator kill-switch workflow dipahami
  - operator siap memonitor first live execution session

Mode yang saya anggap paling sehat untuk first enable:
- supervised live micro
- satu symbol
- satu proses
- lot/risk konservatif
- audit penuh log session setelah order pertama selesai
