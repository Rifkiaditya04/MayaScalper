# Scalper Repository

Repository ini sekarang memuat dua jalur yang sengaja dipisahkan:

- `mt5_bot/` adalah rebuild live engine lama yang tetap dipertahankan sebagai substrate referensi MT5.
- `tsp/` adalah jalur implementasi resmi untuk `TOURNAMENT SCALPING PREDATOR V1` berdasarkan blueprint yang sudah di-lock.
- `tsp_v2/` adalah package terisolasi untuk implementation phase `FINAL_BLUEPRINT V2`, dimulai dari scaffold kontrak dan config foundation tanpa mencampur runtime V1.

Blueprint implementasi aktif ada di [FINAL_BLUEPRINT (BOT SCALPER).md](d:\Maya\Scalper\FINAL_BLUEPRINT%20(BOT%20SCALPER).md).

## Entry points

- `main.py` tetap menunjuk runner legacy `mt5_bot`.
- `tsp_main.py` adalah entrypoint baru untuk implementation phase TSP V1.

## Phase 3 status

- Phase 3.1 `Repository skeleton + package layout`: scaffolded.
- Phase 3.2 `Core state models (state.py)`: implemented with production-grade enums, immutable snapshots, runtime state containers, and validation guards.
- Phase 3.3 `Config loader + validation`: implemented with strict section parsing, env-only MT5 credentials, config fingerprinting, and fail-fast cross-section validation.
- Phase 3.4 `Data pipeline + MarketSnapshot builder`: implemented with deterministic ATR/baseline/compression calculations, session inference, snapshot assembly, and a reusable read-only market data adapter contract.
- Phase 3.5 `Regime engine`: implemented with hierarchical classification, direction-bias derivation, TREND/BREAKOUT arbitration, and explicit `HTF_TREND_PENDING` / `TREND_WITH_BO_CONFIRM` handling.
- Phase 3.6 `Signal engine`: implemented with regime-based module routing, component scoring, dynamic thresholding, deterministic `setup_id`, and stale-signal suppression.
- Phase 3.7 `Risk engine`: implemented with effective-equity clamping, deterministic lot sizing, aggression FSM, pyramid eligibility checks, emergency-exit doctrine, and pure `RiskDecision` outputs.
- Phase 3.8 `Competition governor`: implemented with state evaluation for `SURVIVE/NORMAL/HUNT/PROTECT/SPRINT`, session circuit breaker logic, context reset helpers, and hierarchy-safe governor bias application.
- Phase 3.9 `Execution orchestration layer`: implemented with broker adapter protocol, TTL-based execution registry, first-fail validation gates, and structured `ExecutionResult` mapping for fill/reject/timeout states.
- Phase 3.10 `Lifecycle manager`: implemented with pure lifecycle evaluation, TP recovery priority, BE/trail/partial handling, atomic layer mutations, and startup orphan recovery doctrine.
- Phase 3.11 `Main bot loop`: implemented as in-memory orchestration that bootstraps runtime state, processes one closed-bar cycle, applies subsystem results to `RuntimeState`, and onboards fills into `PositionState`.
- Phase 3.12 `SQLite persistence`: implemented with schema bootstrap, config-fingerprint enforcement, broker-truth startup reconcile, persisted execution registry, and atomic `BEGIN IMMEDIATE` bar-cycle writes owned explicitly by `bot.py`.
- Phase 3.13 `Testing harness`: implemented with profile-based test runner (`unit` / `smoke` / `all`), harness self-checks, and a stable CLI entrypoint for regression gating.
- Phase 3.14 `Backtest adapter`: implemented with a dedicated historical replay adapter, deterministic execution assumptions, and structured performance reporting without branching production bot logic.
- Phase 3.15 `Forward-test deployment pack`: implemented with deployment profiles, env templates, PowerShell launchers, runtime guardrails, single-instance lock, and MT5 adapter bridging for forward-test operations.

## Engineering notes

- Semua patch baru untuk jalur `tsp/` ditulis sebagai implementation-grade code, bukan prototype throwaway.
- Setiap pembaruan engine atau fitur penting akan diringkas di `README.md` agar jejak implementasi tetap mudah diikuti.
- `mt5_bot/` legacy tetap dipertahankan sebagai substrate referensi integrasi MT5, tetapi pengembangan resmi TSP V1 bergerak di `tsp/`.
- Surface konfigurasi TSP V1 sekarang memakai `tsp/config.yaml` + env `TSP_MT5_*`, dengan strict unknown-key rejection per section untuk mencegah drift atau typo operator.
- Pipeline snapshot TSP sekarang sudah siap menarik rates `M1/M5/M15/H1`, membentuk `MarketSnapshot`, dan menyiapkan kontrak simbol yang nanti dipakai fase risk/execution.
- Implementation interpretation yang saya kunci di fase regime: `MarketSnapshot` diperluas dengan `adx_m5`, `adx_m15`, dan `adx_h1` karena blueprint memakai ADX dalam formula regime walau field itu belum tertulis eksplisit di daftar awal snapshot.
- Signal engine TSP sekarang sudah menghasilkan `SignalScore` yang siap dipakai oleh phase risk, termasuk lineage-aware stale suppression terhadap `runtime.last_signal`.
- Risk engine TSP sekarang tetap pure: ia hanya mengembalikan keputusan dan evaluasi, tanpa mutasi runtime langsung, sehingga masih konsisten dengan doctrine `bot.py` sebagai satu-satunya owner mutasi state.
- Competition governor TSP sekarang juga tetap pure: ia menghasilkan `GovernorDirective` dan helper context transitions, tanpa menulis runtime atau persistence secara langsung.
- Execution layer TSP sekarang memisahkan orchestration dari broker intelligence: validasi, dedup, dan result mapping ada di `tsp/execution.py`, sedangkan retry/fill nuance broker tetap menjadi tanggung jawab adapter MT5.
- Lifecycle manager TSP sekarang memakai pola `events + mutations`: evaluator boleh berinteraksi dengan adapter broker untuk verifikasi/recovery, tetapi tetap tidak memutasi `RuntimeState` secara langsung.
- `tsp/bot.py` sekarang menjadi owner mutasi state sesuai doctrine blueprint, tetapi persistence SQLite masih sengaja belum diaktifkan sampai Phase 3.12.
- Persistence SQLite TSP sekarang memakai `WAL` + `synchronous=NORMAL`, schema-version metadata, fail-loud config fingerprint checks, stale registry pruning, dan single-transaction persistence per successful bar-cycle untuk menghindari split-brain state.
- Startup restore TSP sekarang tidak lagi percaya SQLite secara buta: posisi persisted direkonsiliasi terhadap broker reality, dan broker menjadi source of truth untuk live exposure.
- Harness test TSP sekarang punya entrypoint tetap di `python -m tests.harness <profile>`, jadi validasi tidak lagi bergantung pada command unittest panjang yang rawan drift saat suite bertambah.
- Backtest TSP sekarang hidup di `tsp/backtest.py` dan tetap mengikuti contract adapter bot yang sama, sehingga replay historis, spread/slippage assumptions, dan execution simulation tidak bocor menjadi shortcut di modul produksi.
- Deployment pack TSP sekarang hidup di `tsp/deploy.py` + folder `deploy/`, dengan guardrails ops yang fail-loud dan profile `aggressive` tetap dipin ke baseline locked config sampai ada governance approval yang sah.
- Bridge deployment TSP sekarang juga menormalkan broker clock offset per-jam yang berasal dari feed MT5 Python, sehingga preflight tetap memakai UTC efektif tanpa menurunkan freshness guardrail.
- Bug-fix hardening untuk validation phase: `tsp/data_pipeline.py` sekarang hanya membangun `MarketSnapshot` dari last fully closed bars `M1/M5/M15/H1`, bukan current forming bar, agar fidelity runtime benar-benar sinkron dengan doctrine `M1 closed-bar`.
- Observability hardening untuk validation phase: heartbeat deployment sekarang membedakan `processed_new_bar` vs `duplicate_bar_skip` dan ikut mencatat raw regime metrics penting seperti ADX, slope normalization, trend composite, compression ratio, ATR burst ratio, dan secondary confirmation count.
- Regime telemetry hardening untuk validation phase: heartbeat sekarang juga mencatat candidate-path diagnostics untuk `TREND` dan `BREAKOUT`, termasuk `*_fail_reason` terstruktur, supaya `CHOP` bisa diaudit sebagai hasil decision tree yang lengkap alih-alih fallback yang gelap.
- PATCH-000 V2 sudah mengunci scaffold `tsp_v2/`, `deploy/v2/`, adapter protocols, recovery stubs, dan test skeleton tanpa menyentuh runtime `tsp/` V1.
- PATCH-001 V2 sekarang mengunci `base -> profile -> environment -> governed CLI` config loading, strict schema validation, official mode/profile enums, env-only secret doctrine, dan deterministic config fingerprinting.
- PATCH-002 V2 sekarang mengunci broker-clock authority, deterministic UTC session classifier, dan governed news-provider surface dengan fail-loud freshness rules untuk `FORWARD_TEST/CONTEST` serta diagnostic-only bypass yang eksplisit.
- PATCH-002A V2 sekarang menutup hardening fondasi yang tidak boleh dibawa ke snapshot layer: env override coercion fail-loud proof, fingerprint canonicalization proof, session enum drift closure ke `LONDON_NY`, dan issue register resmi di `docs/PATCH_ISSUE_REGISTER_V2.md`.
- PATCH-003 V2 sekarang mengunci immutable market snapshot builder dengan unsupported-symbol rejection, contract sanity validation, closed-bar-only filtering `M1/M5/M15/H1`, feed health classification, news/session embedding, dan frozen indicator bundle dari satu atomic capture path.
- PATCH-004 V2 sekarang mengunci deterministic regime priority `NEWS_LOCKOUT -> TREND -> BREAKOUT -> MICRO_MOMENTUM -> CHOP`, termasuk EMA20 slope normalization, HTF alignment, ADX-gated trend composite, compression-history breakout qualification, micro impulse gating, dan explicit priority-order tests.
- PATCH-005 V2 sekarang mengunci signal engine `TREND_CONTINUATION / BREAKOUT_MOMENTUM / MICRO_IMPULSE`, termasuk deterministic setup identity hash, TTL doctrine, duplicate suppression, governor-aware threshold adjustment, structured reject reasons, dan hardening snapshot contract untuk monotonicity + explicit `latency_health` / `spread_health`.
- PATCH-006 V2 sekarang mengunci pure risk engine dengan governor base-risk ladder, portfolio/correlation caps, max-2-position enforcement, pyramid eligibility, anti-revenge blocking, dan emergency escalation `EMERGENCY_EXIT / KILL_REVIEW / REDUCE`.
- PATCH-007 V2 sekarang mengunci governor/portfolio orchestration dengan canonical state ladder `SURVIVE / NORMAL / ATTACK / HUNTER / CHASE / PROTECT / SPRINT / KILL_REVIEW`, pace classification `BEHIND / ON_TRACK / AHEAD`, starvation escalation, synthetic ranking pace support, top-2 opportunity selection, replacement superiority gate, dan correlation-aware portfolio filtering.
- PATCH-007A V2 sekarang menutup `V2-006` dengan explicit snapshot payload health policy `GREEN / YELLOW / RED`, thin-payload allowance bila masih di atas minimum closed-bar requirement, dan fail-loud rejection untuk partial timeframe loss di bawah minimum.
- PATCH-008 V2 sekarang mengunci execution orchestration dengan intent validation, deterministic submission identity, idempotency registry, 15-second symbol lock, broker-truth reconciliation, retryable/non-retryable failure mapping, dan explicit execution lifecycle transitions `PENDING / SUBMITTED / ACKNOWLEDGED / PARTIAL / FILLED / REJECTED / CANCELLED / EXPIRED / AMBIGUOUS`.
- PATCH-009 V2 sekarang mengunci persistence/recovery runtime dengan single-writer SQLite schema bootstrap, config fingerprint persistence, governor/account/runtime state tables, execution registry round-trip, recovery bootstrap ordering, unresolved exposure handling, dan broker-truth replay-safe reconciliation.
- PATCH-010 V2 sekarang mengunci deployment runtime dengan preflight validation, single-instance lock ownership/reclaim, startup metadata persistence, graceful shutdown/emergency shutdown path, operator launcher `run_v2.py`, dan deployment scripts yang terhubung ke launcher V2.
- PATCH-011 V2 sekarang mengunci telemetry/runtime reporting dengan structured event model, severity routing, runtime metrics aggregation, governor/execution/recovery telemetry, daily runtime summary, dan telemetry_index export path yang read-only terhadap keputusan trading.
- PATCH-012A V2 sekarang mengunci MT5 bridge contract awal di `tsp_v2/adapters/mt5_bridge.py` dengan connect/disconnect, symbol/account/tick/rates queries, place/cancel/modify/close request flow, heartbeat classification, dan explicit retryable/non-retryable/fatal status model yang tetap testable tanpa dependency MT5 terpasang.
- PATCH-012B V2 sekarang menambahkan market adapter di `tsp_v2/adapters/market_adapter.py` yang menormalkan broker time, tick, rates, symbol contract, dan market status view untuk snapshot builder tanpa menyentuh regime, signal, risk, atau execution logic.
- PATCH-012C V2 sekarang menambahkan execution adapter di `tsp_v2/adapters/execution_adapter.py` yang hanya menerima `ExecutionIntent`, mengubahnya menjadi MT5 order request, memetakan broker response ke typed `ExecutionResult`, dan menjaga registry/idempotency compatibility tanpa mengubah risk, governor, signal, atau regime.
- PATCH-012D V2 sekarang menambahkan broker reconciliation runtime di `tsp_v2/recovery/reconcile.py` yang menyatukan positions/orders/deals/account state terhadap broker truth, menuliskan konflik/orphan findings ke telemetry, dan menautkan recovery bootstrap ke broker truth provider tanpa mengubah ledger contract upstream.
- PATCH-012E V2 sekarang mengaktifkan live runtime path di `tsp_v2/deployment.py`, `tsp_v2/live_runtime.py`, dan `tsp_v2/run_v2.py` sehingga `preflight -> connect -> reconcile -> activate_loop` berjalan melalui bridge, market adapter, execution adapter, dan broker reconciliation runtime yang nyata.
- Fase berikutnya adalah validasi produksi formal; source of truth-nya ada di [docs/VALIDATION_PROGRAM_V2.md](docs/VALIDATION_PROGRAM_V2.md).
