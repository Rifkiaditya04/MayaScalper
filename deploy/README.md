# Forward-Test Deployment Pack

Deployment pack ini adalah surface operasional untuk TSP V1.

## Scripts

- `run_preflight.ps1`: jalankan harness `all` lalu deployment dry-run
- `run_forward_test.ps1`: jalankan loop forward-test dengan profile `forward_test`
- `run_live.ps1`: jalankan loop live dengan profile `contest_safe`

## Config profiles

- `configs/contest_safe.yaml`: baseline deployment profile yang paling aman
- `configs/forward_test.yaml`: profile forward-test dengan artefak runtime terpisah
- `configs/aggressive.yaml`: placeholder operasional yang tetap dipin ke baseline locked config sampai ada change request yang sah

## Env templates

- root `.env.forward.example`
- root `.env.live.example`

## Artifact paths

- `logs/`: runtime logs
- `reports/`: deployment summaries
- `backtests/`: output historical replay
- `runtime/`: state, lockfile, DB, last-known-good config

## Guardrails

- single-instance lock
- credential validation
- XAUUSD enforcement
- broker symbol contract sanity
- clock skew sanity
- DB/log/state path sanity
- config fingerprint validation via TSP bootstrap

## Operator doctrine

- `--dry-run` hanya memvalidasi preflight dan snapshot build; tidak masuk loop trading
- `TSP_ENABLE_LIVE_EXECUTION=0` akan membuat adapter menolak order live walau loop jalan
- profile `aggressive` tidak mengubah logic trading locked; file itu hanya memisahkan artefak deployment sampai ada governance approval
