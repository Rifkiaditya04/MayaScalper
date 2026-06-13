from __future__ import annotations

from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path

from tsp_v2.config import load_config
from tsp_v2.orchestrator import TSPV2Orchestrator


BASE_TEMPLATE = """
bot:
  mode: FORWARD_TEST
  profile: FORWARD_SAFE
  expert_mode: false
  poll_interval_seconds: 5
symbols:
  allowlist:
    - XAUUSD
alpha:
  setup_cooldown_bars: 1
regime:
  closed_bar_timeframe: M5
  news_lockout_minutes: 30
signal:
  min_score: 0.65
  ttl_seconds: 300
risk:
  max_open_risk_pct: 1.0
  max_daily_loss_pct: 5.0
governor:
  initial_state: NORMAL
  kill_review_drawdown_pct: 15.0
  offensive_profiles_require_expert_mode: true
lifecycle:
  thesis_ttl_minutes: 60
  break_even_after_r: 1.0
execution:
  signal_ttl_seconds: 300
  slippage_veto_ratio: 0.30
  max_spread_ratio: 1.80
telemetry:
  heartbeat_interval_seconds: 5
  emit_candidate_diagnostics: true
persistence:
  sqlite_path: {sqlite_path}
  lock_path: {lock_path}
  wal_enabled: true
contest:
  ranking_proxy_enabled: false
  contest_window_minutes: 1440
deployment:
  runtime_root: runtime
  log_root: logs
  report_root: reports
  allow_live_execution: false
news:
  provider_mode: STATIC_FILE
  source_path: {news_path}
  stale_warn_minutes: 15
  stale_soft_fail_minutes: 30
  stale_hard_fail_minutes: 60
"""


class OrchestratorTests(unittest.TestCase):
    def test_bootstrap_initializes_store_and_reports_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _load_config(root)
            orchestrator = TSPV2Orchestrator(config)
            try:
                report = orchestrator.bootstrap(
                    broker_positions=(),
                    current_time_utc=datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
                )
                self.assertTrue(report.ready_to_resume)
                self.assertIsNotNone(orchestrator.store)
                self.assertEqual(orchestrator.store.get_config_fingerprint(), config.fingerprint)
            finally:
                if orchestrator.store is not None:
                    orchestrator.store.close()


def _load_config(root: Path):
    news_path = root / "news.json"
    news_path.write_text(
        "{\"generated_at_utc\":\"2026-05-29T09:00:00+00:00\",\"events\":[]}",
        encoding="utf-8",
    )
    sqlite_path = root / "runtime.db"
    lock_path = root / "locks"
    base_text = BASE_TEMPLATE.format(
        sqlite_path=str(sqlite_path).replace("\\", "/"),
        lock_path=str(lock_path).replace("\\", "/"),
        news_path=str(news_path).replace("\\", "/"),
    )
    (root / "base.yaml").write_text(base_text, encoding="utf-8")
    (root / "profile.yaml").write_text(
        "bot:\n  mode: FORWARD_TEST\n  profile: FORWARD_SAFE\n",
        encoding="utf-8",
    )
    (root / ".env").write_text(
        "\n".join(
            (
                "TSP_V2_MT5_LOGIN=1",
                "TSP_V2_MT5_PASSWORD=secret",
                "TSP_V2_MT5_SERVER=Demo",
                "TSP_V2_MT5_TERMINAL_PATH=C:\\MT5\\terminal64.exe",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return load_config(config_path=root / "profile.yaml", env_path=root / ".env")


if __name__ == "__main__":
    unittest.main()
