# Test Strategy

Testing harness resmi TSP sekarang hidup di `tests/harness.py` dan menyediakan tiga profile:

- `unit`: rule engine dan pure decision modules
- `smoke`: orchestration, persistence, dan startup recovery surface
- `all`: seluruh suite yang terdaftar

Command utama:

```powershell
D:\Maya\pradita\Scripts\python.exe -m tests.harness unit
D:\Maya\pradita\Scripts\python.exe -m tests.harness smoke
D:\Maya\pradita\Scripts\python.exe -m tests.harness all --json
```

Utility tambahan:

```powershell
D:\Maya\pradita\Scripts\python.exe -m tests.harness all --list
D:\Maya\pradita\Scripts\python.exe -m tests.harness all --failfast
```

Target coverage harness phase ini:

- invariants state/config/data pipeline
- regime, signal, risk, governor, execution, lifecycle decisions
- bot closed-bar orchestration smoke
- SQLite persistence, config drift enforcement, broker-truth reconcile, dan registry restore
- historical replay adapter, deterministic execution simulation, dan structured backtest report
- deployment guardrails, single-instance lock, dan forward-test settings bridge

Fokus lanjutan untuk phase berikutnya:

- replay test dari log live yang dulu pernah bermasalah
- scenario regression packs untuk lifecycle edge cases
- adapter validation untuk backtest/live parity
