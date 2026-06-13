from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import textwrap
import unittest

from tsp_v2.config import load_config
from tsp_v2.config_schema import ConfigValidationError
from tsp_v2.enums import NewsProviderState
from tsp_v2.news import build_news_snapshot


BASE_CONFIG = """
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
  sqlite_path: runtime/db/tsp_v2_runtime.sqlite3
  lock_path: runtime/locks
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
  source_path: NEWS_PATH_PLACEHOLDER
  stale_warn_minutes: 15
  stale_soft_fail_minutes: 30
  stale_hard_fail_minutes: 60
"""


class NewsProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved_env)

    def test_news_snapshot_ready_and_symbol_relevant(self) -> None:
        cycle_time = datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            news_path = root / "news.json"
            self._write_news(
                news_path,
                generated_at_utc="2026-05-26T09:50:00+00:00",
                events=[
                    {
                        "event_id": "n1",
                        "title": "US CPI",
                        "symbol": "USD",
                        "impact": "HIGH",
                        "starts_at_utc": "2026-05-26T10:10:00+00:00",
                    }
                ],
            )
            config = self._load_config(root=root, news_path=news_path, profile_text="bot:\n  mode: FORWARD_TEST\n  profile: FORWARD_SAFE\n")
            snapshot = build_news_snapshot(cycle_time_utc=cycle_time, config=config, symbol="XAUUSD")
            self.assertEqual(snapshot.provider_state, NewsProviderState.READY)
            self.assertTrue(snapshot.lockout_active)
            self.assertEqual(len(snapshot.relevant_events), 1)

    def test_news_provider_fail_loud_when_unavailable_in_forward_test(self) -> None:
        cycle_time = datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            news_path = root / "missing.json"
            config = self._load_config(root=root, news_path=news_path, profile_text="bot:\n  mode: FORWARD_TEST\n  profile: FORWARD_SAFE\n")
            with self.assertRaises(ConfigValidationError):
                build_news_snapshot(cycle_time_utc=cycle_time, config=config, symbol="XAUUSD")

    def test_news_provider_stale_rejected_in_forward_test(self) -> None:
        cycle_time = datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            news_path = root / "news.json"
            self._write_news(
                news_path,
                generated_at_utc="2026-05-26T08:30:00+00:00",
                events=[],
            )
            config = self._load_config(root=root, news_path=news_path, profile_text="bot:\n  mode: FORWARD_TEST\n  profile: FORWARD_SAFE\n")
            with self.assertRaises(ConfigValidationError):
                build_news_snapshot(cycle_time_utc=cycle_time, config=config, symbol="XAUUSD")

    def test_diagnostic_bypass_allows_disabled_provider(self) -> None:
        cycle_time = datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = self._load_config(
                root=root,
                news_path=None,
                profile_text=textwrap.dedent(
                    """
                    bot:
                      mode: DIAGNOSTIC
                      profile: DIAGNOSTIC
                    news:
                      provider_mode: DISABLED_DIAGNOSTIC_ONLY
                      source_path: null
                    """
                ).strip()
                + "\n",
            )
            snapshot = build_news_snapshot(cycle_time_utc=cycle_time, config=config, symbol="XAUUSD")
            self.assertEqual(snapshot.provider_state, NewsProviderState.DISABLED)

    def _load_config(self, *, root: Path, news_path: Path | None, profile_text: str):
        base_text = BASE_CONFIG.replace(
            "NEWS_PATH_PLACEHOLDER",
            str(news_path).replace("\\", "/") if news_path is not None else "null",
        )
        (root / "base.yaml").write_text(textwrap.dedent(base_text).strip() + "\n", encoding="utf-8")
        (root / "profile.yaml").write_text(profile_text, encoding="utf-8")
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

    def _write_news(self, path: Path, *, generated_at_utc: str, events: list[dict[str, str]]) -> None:
        payload = {
            "generated_at_utc": generated_at_utc,
            "events": events,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
