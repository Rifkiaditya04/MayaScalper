# Final Blueprint MT5 Live Trading Rebuild

## 1. Tujuan Rebuild

Blueprint ini mendokumentasikan versi final desain bot MT5 live yang akan dibangun ulang dari nol, berdasarkan:

- file yang masih bisa ditemukan seperti `main.py` dan `.env`
- jejak log live yang tersisa
- seluruh perbaikan operasional yang sudah terbukti penting
- keputusan final atas bug, false signal, dan broker behavior yang pernah muncul

Target utamanya:

- fungsi inti kembali
- state machine benar
- guardrail tidak bocor
- broker reality tertangani
- log audit jelas

## 2. Prinsip Desain

### 2.1 Jangan kejar arsitektur terlalu besar

Bot lama justru membaik ketika rule dibuat makin eksplisit dan makin sederhana. Karena itu rebuild ini:

- tidak berangkat dari sistem multi-strategy yang terlalu luas
- tidak memasukkan grid, martingale, atau hedge recovery sebagai fondasi awal
- fokus ke satu engine directional MT5 live yang disiplin

### 2.2 Semua keputusan harus bisa diaudit dari log

Setiap setup yang lolos atau tertahan harus bisa dijelaskan dari log, bukan tebakan. Maka:

- blocker harus eksplisit
- state lock/unlock harus tercatat
- alasan entry/exit harus tercatat
- broker action harus tercatat

### 2.3 Broker reality lebih penting daripada teori strategy

Beberapa masalah terbesar dulu bukan datang dari arah market, tapi dari realitas broker:

- TP tidak selalu aman jika dikirim langsung saat entry
- min stop distance broker memaksa TP efektif membesar
- filling mode retry bisa sukses setelah percobaan awal gagal
- `order` belum tentu identik dengan `position ticket`

Karena itu layer broker tidak boleh dianggap detail kecil.

## 3. Modul Inti

## 3.1 `main.py`

Tanggung jawab:

- membaca `.env`
- bootstrap logging
- inisialisasi MT5 client
- memilih symbol live
- membuat instance engine bot
- menjalankan polling/loop live
- menangani shutdown yang aman

## 3.2 `mt5_bot/config.py`

Tanggung jawab:

- load config dari environment
- validasi nilai penting
- preset per asset mode
- expose satu object config final ke seluruh sistem

Harus mencakup:

- login/server/path terminal
- symbol
- timeframe
- risk
- lot cap
- TP feasibility buffer
- structure lookback
- lock buffer
- failsafe minutes
- follow-through threshold
- freshness buffer
- max positions
- layer count

## 3.3 `mt5_bot/mt5_client.py`

Tanggung jawab:

- initialize MT5
- login / attach ke terminal
- fetch symbol info
- fetch candles per timeframe
- place market order
- modify SL/TP
- get positions
- close position

Layer ini harus mengurus broker reality berikut:

1. entry dikirim tanpa TP/SL broker-side jika itu lebih aman
2. TP di-attach setelah posisi live benar-benar teridentifikasi
3. filling mode di-retry
4. position ticket harus di-resolve dari posisi live, bukan hanya percaya `result.order`
5. semua request/retcode/log payload disimpan jelas

## 3.4 `mt5_bot/indicators.py`

Tanggung jawab:

- ATR
- RSI
- MA7
- candle body/wick stats
- slope / location helper
- structure helper

Semua indikator harus berbasis candle close yang konsisten, bukan tick acak.

## 3.5 `mt5_bot/bot.py`

Ini inti decision engine.

Tanggung jawab:

- build setup
- filter setup
- enforce guardrail
- execute entry
- manage live position
- maintain regime state
- emit decision log

## 3.6 `mt5_bot/state.py`

Tanggung jawab:

- state directional lock
- freshness reset/reclaim state
- expected close memory
- manual close cooldown state
- session start state
- per-position runtime state

## 3.7 `mt5_bot/risk.py`

Tanggung jawab:

- equity-aware risk budget
- lot sizing
- max lot cap
- max positions
- drawdown constraints
- trade feasibility from ATR vs effective TP

## 3.8 `mt5_bot/execution.py`

Tanggung jawab:

- wrap `mt5_client` execution flow
- entry without TP
- resolve position ticket
- attach TP
- verify TP attached
- close flow
- protection update flow

## 3.9 `mt5_bot/logging_utils.py`

Tanggung jawab:

- format decision logs
- format state logs
- per symbol/session log naming
- consistent event names

## 4. Data Flow Final

### 4.1 Loop utama

1. ambil M1, M5, H1, H4 candle close terbaru
2. hitung indikator
3. bangun setup BUY/SELL
4. hitung HTF bias
5. cek location quality
6. cek TP feasibility
7. cek follow-through forecast
8. cek directional lock
9. cek freshness reset/reclaim
10. cek posisi aktif searah
11. jika lolos, execute entry
12. jika ada posisi aktif, jalankan position management

### 4.2 Alur entry

1. build signal
2. apply blocker
3. kirim market order tanpa TP broker-side
4. resolve live position
5. attach TP via modify protection
6. verifikasi TP benar-benar attached
7. simpan runtime state posisi
8. arm freshness state untuk arah tersebut

### 4.3 Alur live management

1. update max favorable excursion
2. coba protection/BE jika syarat terpenuhi
3. cek time-based exit setelah 2 candle M5
4. cek apakah posisi hilang
5. klasifikasikan hilangnya posisi:
   - bot expected close
   - broker TP close
   - manual/external close
6. jika failure exit, lock regime direction

## 5. Entry Engine Final

## 5.1 HTF bias

Rule:

- `BUY` jika H1 dan H4 sama-sama mendukung bullish
- `SELL` jika H1 dan H4 sama-sama mendukung bearish
- selain itu `HOLD`

Rule final:

- `HTF = HOLD -> NO TRADE`
- `HOLD` bukan unlock condition

## 5.2 M1 trigger

Trigger digunakan sebagai eksekutor lokal, bukan penentu tunggal arah.

Komponen continuation score:

- structure
- body strength
- close near extreme
- wick quality
- ATR expansion
- no extreme rejection
- not overextended

Minimal:

- searah HTF: `score >= 4`
- lawan HTF tidak dipakai dalam blueprint final awal ini, kecuali nanti kita benar-benar aktifkan mode counter-trend dengan syarat lebih berat

## 5.3 M5 MA7 location quality

Tujuannya mencegah entry di area transisi atau reclaim palsu.

BUY valid jika:

- close berada di atas MA7
- tidak ada body cross bearish yang jelas
- jika terlalu dekat MA7, harus ada konteks reclaim yang benar

SELL simetris.

## 5.4 TP feasibility

Rumus:

- `effective_tp_distance = max(tp_atr_mult * atr, broker_min_tp_distance * broker_buffer)`

Trade hanya boleh jika market mampu membayar target minimal broker itu.

Rule final:

- `M5 ATR >= effective_tp_distance * feasibility_buffer`

Jika tidak:

- block trade
- log blocker `tp_feasibility`

## 5.5 Follow-through forecast

Tujuan:

- bukan meramal pasti
- tapi menilai probabilitas 2-3 candle ke depan cukup sehat

Komponen:

- body kuat
- close near extreme
- no opposite rejection
- MA slope support
- HTF alignment kuat

Klasifikasi:

- `HIGH`
- `MEDIUM`
- `LOW`

Rule final:

- `LOW -> skip trade`

## 6. Exit Engine Final

## 6.1 TP broker-side

Final decision:

- TP tetap dipasang broker-side
- tetapi dipasang setelah entry sukses
- bukan dipercaya mentah di request awal order

## 6.2 BE / profit protection

BE hanya aktif jika:

- progress cukup
- broker min stop distance memungkinkan

Kalau belum memungkinkan:

- log `BE delayed`
- jangan paksa modify gagal berulang tanpa konteks

## 6.3 Two-candle M5 failure exit

Minimalist exit final:

1. tunggu 2 candle M5 close
2. hitung MFE
3. jika `MFE < 50% effective TP`
   - exit
4. jika harga kembali ke entry area setelah 2 candle
   - exit

Ini menggantikan early-exit kompleks yang terlalu sensitif.

## 6.4 Manual/external close detection

Saat posisi hilang:

- jika sebelumnya ditandai expected close -> bot close
- jika hilang dekat TP broker -> broker TP close
- selain itu -> manual/external close

Manual close harus:

- menandai cooldown/pause
- tidak dianggap exit bot sendiri

## 7. State Machine Final

## 7.1 Directional regime lock

Lock dibuat ketika:

- trade ditutup oleh failure exit, terutama `progress_below_50pct_after_2_m5`

Saat lock, simpan:

- direction
- htf_bias saat lock
- `ref_high`
- `ref_low`
- `timestamp`
- `buffer`

## 7.2 Unlock final

Unlock hanya jika:

1. structure break close-based + buffer
2. HTF opposite nyata

BUY lock:

- unlock jika `M5 close < ref_low - buffer`
- atau HTF berubah ke `SELL`

SELL lock:

- unlock jika `M5 close > ref_high + buffer`
- atau HTF berubah ke `BUY`

`HOLD`:

- ignore
- bukan alasan unlock

## 7.3 Freshness reset -> reclaim

Ini guardrail penting untuk mencegah entry di ujung leg.

### BUY

Reset:

- bar `N`: `M5 close[N] <= MA7[N] + buffer`

Reclaim:

- bar `M > N`: `M5 close[M] > MA7[M]`
- lalu trigger/score valid

### SELL

Reset:

- bar `N`: `M5 close[N] >= MA7[N] - buffer`

Reclaim:

- bar `M > N`: `M5 close[M] < MA7[M]`

Guardrails:

- reset dan reclaim tidak boleh di bar yang sama
- gunakan close, bukan wick
- reclaim hanya valid jika `bar_time > reset_bar_time`
- setelah reclaim dipakai untuk entry, state freshness harus di-clear
- reset boleh punya expiry 3-5 bar agar tidak menggantung

## 7.4 No same-direction add-on

Jika belum benar-benar memakai layering mode:

- ada BUY aktif -> block BUY baru
- ada SELL aktif -> block SELL baru

Tujuannya memotong pola:

- BUY 1
- BUY 2
- BUY 3

di leg yang sama tanpa reset nyata

## 8. Broker Reality Rules

## 8.1 Attach TP after entry

Masalah lama:

- request entry awal bisa gagal
- retry filling mode bisa sukses
- TP belum tentu nempel walau order done

Solusi final:

1. entry tanpa TP
2. resolve live position
3. attach TP via modify
4. verify TP attached

## 8.2 Minimum stop distance

Jangan gunakan target teoritis semata.

Selalu hitung:

- broker stop level
- broker buffer

Lalu ubah itu menjadi:

- effective TP distance

## 8.3 Filling mode retry

Jika order rejected oleh filling mode:

- retry mode lain
- log setiap percobaan

Tetapi:

- jangan pernah menganggap retry sukses berarti protection otomatis sukses

## 8.4 Resolve live position ticket

Jangan percaya `result.order` mentah sebagai `position ticket`.

Harus resolve dari live positions berdasarkan:

- symbol
- side
- magic
- waktu dekat
- volume cocok

## 8.5 Manual close / external close handling

Bot harus bisa membedakan:

- bot close
- broker TP close
- manual close
- external close

Supaya:

- tidak salah set cooldown
- tidak salah tulis reason exit
- tidak memicu state yang salah

## 9. Guardrail Final

Bot tidak boleh trade jika salah satu dari berikut gagal:

- `HTF == HOLD`
- `tp_feasibility` gagal
- `follow_through == LOW`
- direction sedang locked
- same-direction active
- freshness reset/reclaim belum lengkap
- position cap tercapai
- spread/news/session filter gagal jika nanti diaktifkan

## 10. Logging Standard Final

Log harus eksplisit dan konsisten.

Jenis event minimum:

- `SETUP DEBUG`
- `ORDER DEBUG`
- `BOT POSITION placed`
- `BOT POSITION TP attached`
- `BOT POSITION BE delayed`
- `BOT POSITION time exit`
- `Manual/external close detected`
- `[LOCK]`
- `[UNLOCK]`
- `[FRESHNESS] reset detected`
- `[FRESHNESS] reclaim confirmed`
- `No valid setup`

Untuk blocker:

- tulis list blocker, bukan satu kalimat generik

Contoh:

- `htf_bias:HOLD_no_trade`
- `tp_feasibility:m5_atr_below_required`
- `direction_lock:locked_buy_regime`
- `same_direction_active`
- `freshness:await_buy_reset_reclaim_m5_ma7`

## 11. Tahapan Implementasi

### Phase 1 - Blueprint final

- dokumen arsitektur
- rule final
- state machine
- broker behavior

### Phase 2 - Scaffold project baru

- buat struktur folder dan file inti
- stub class dan interface
- config + logging dasar

### Phase 3 - Broker/client layer

- initialize/login
- candles
- symbol info
- order flow
- modify TP/SL
- close position

### Phase 4 - Decision engine

- HTF bias
- trigger
- location quality
- TP feasibility
- follow-through

### Phase 5 - State machine & exit management

- directional lock
- freshness
- no add-on
- time-based failure exit
- manual close detection

### Phase 6 - Logging audit

- standard event names
- blocker lists
- state transition logs

### Phase 7 - Dry/live controlled testing

- 1 symbol
- 1 small lot
- 1 mode
- audit log sesi demi sesi

## 12. Penutup

Rebuild ini tidak boleh hanya menjadi “proyek yang hidup lagi”.

Ia harus menjadi versi yang:

- lebih jelas
- lebih modular
- lebih bisa diaudit
- lebih tahan terhadap bug lama
- lebih jujur terhadap realitas broker dan market

Jika rule ini dipatuhi, kita tidak memulai dari nol kosong. Kita memulai dari pelajaran live yang sudah mahal dan sudah terbukti penting.
