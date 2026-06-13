from __future__ import annotations

import os
from pathlib import Path
import tempfile
import textwrap
import unittest

from tsp_v2.config import build_config_fingerprint, canonicalize_config_for_fingerprint, load_config
from tsp_v2.config_schema import ConfigValidationError
from tsp_v2.enums import ProfileName, RuntimeMode


BASE_YAML = """
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
  source_path: deploy/v2/news/calendar_snapshot.json
  stale_warn_minutes: 15
  stale_soft_fail_minutes: 30
  stale_hard_fail_minutes: 60
"""


class ConfigFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved_env)

    def test_load_config_applies_base_profile_env_and_cli_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            base_path = root / "base.yaml"
            profile_path = root / "forward_safe.yaml"
            env_path = root / ".env"
            base_path.write_text(textwrap.dedent(BASE_YAML).strip() + "\n", encoding="utf-8")
            profile_path.write_text(
                textwrap.dedent(
                    """
                    bot:
                      mode: FORWARD_TEST
                      profile: FORWARD_SAFE
                    signal:
                      ttl_seconds: 420
                    execution:
                      signal_ttl_seconds: 420
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            env_path.write_text(
                "\n".join(
                    (
                        "TSP_V2_MT5_LOGIN=123456",
                        "TSP_V2_MT5_PASSWORD=secret",
                        "TSP_V2_MT5_SERVER=Demo-Server",
                        "TSP_V2_MT5_TERMINAL_PATH=C:\\MT5\\terminal64.exe",
                        "TSP_V2_SIGNAL__TTL_SECONDS=480",
                        "TSP_V2_EXECUTION__SIGNAL_TTL_SECONDS=480",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            config = load_config(
                config_path=profile_path,
                env_path=env_path,
                cli_overrides={"bot.mode": RuntimeMode.DEVELOPMENT.value},
            )

        self.assertEqual(config.bot.mode, RuntimeMode.DEVELOPMENT)
        self.assertEqual(config.bot.profile, ProfileName.FORWARD_SAFE)
        self.assertEqual(config.signal.ttl_seconds, 480)
        self.assertEqual(config.execution.signal_ttl_seconds, 480)
        self.assertEqual(config.secrets.mt5_login, "123456")
        self.assertIsNotNone(config.fingerprint)

    def test_unknown_key_rejected_fail_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "base.yaml").write_text(
                textwrap.dedent(BASE_YAML).strip() + "\n",
                encoding="utf-8",
            )
            (root / "forward_safe.yaml").write_text(
                textwrap.dedent(
                    """
                    bot:
                      mode: FORWARD_TEST
                      profile: FORWARD_SAFE
                    telemetry:
                      ghost_flag: true
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(ConfigValidationError):
                load_config(config_path=root / "forward_safe.yaml")

    def test_final_sprint_requires_expert_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "base.yaml").write_text(
                textwrap.dedent(BASE_YAML).strip() + "\n",
                encoding="utf-8",
            )
            (root / "final_sprint.yaml").write_text(
                textwrap.dedent(
                    """
                    bot:
                      mode: CONTEST
                      profile: FINAL_SPRINT
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(ConfigValidationError):
                load_config(config_path=root / "final_sprint.yaml")

    def test_diagnostic_news_mode_rejected_outside_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "base.yaml").write_text(
                textwrap.dedent(BASE_YAML).strip() + "\n",
                encoding="utf-8",
            )
            (root / "bad_profile.yaml").write_text(
                textwrap.dedent(
                    """
                    bot:
                      mode: FORWARD_TEST
                      profile: FORWARD_SAFE
                    news:
                      provider_mode: DISABLED_DIAGNOSTIC_ONLY
                      source_path: null
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(ConfigValidationError):
                load_config(config_path=root / "bad_profile.yaml")

    def test_invalid_env_override_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "base.yaml").write_text(
                textwrap.dedent(BASE_YAML).strip() + "\n",
                encoding="utf-8",
            )
            (root / "forward_safe.yaml").write_text(
                "bot:\n  mode: FORWARD_TEST\n  profile: FORWARD_SAFE\n",
                encoding="utf-8",
            )
            (root / ".env").write_text(
                "\n".join(
                    (
                        "TSP_V2_MT5_LOGIN=123456",
                        "TSP_V2_MT5_PASSWORD=secret",
                        "TSP_V2_MT5_SERVER=Demo-Server",
                        "TSP_V2_MT5_TERMINAL_PATH=C:\\MT5\\terminal64.exe",
                        "TSP_V2_RISK__MAX_OPEN_RISK_PCT=abc",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(ConfigValidationError):
                load_config(config_path=root / "forward_safe.yaml", env_path=root / ".env")

    def test_legacy_mt5_env_aliases_are_accepted_for_forward_test(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "base.yaml").write_text(
                textwrap.dedent(BASE_YAML).strip() + "\n",
                encoding="utf-8",
            )
            (root / "forward_safe.yaml").write_text(
                "bot:\n  mode: FORWARD_TEST\n  profile: FORWARD_SAFE\n",
                encoding="utf-8",
            )
            (root / ".env").write_text(
                "\n".join(
                    (
                        "MT5_LOGIN=123456",
                        "MT5_PASSWORD=secret",
                        "MT5_SERVER=Demo-Server",
                        "MT5_TERMINAL_PATH=C:\\MT5\\terminal64.exe",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            config = load_config(config_path=root / "forward_safe.yaml", env_path=root / ".env")

        self.assertEqual(config.secrets.mt5_login, "123456")
        self.assertEqual(config.secrets.mt5_password, "secret")
        self.assertEqual(config.secrets.mt5_server, "Demo-Server")
        self.assertEqual(str(config.secrets.mt5_terminal_path), "C:\\MT5\\terminal64.exe")

    def test_malformed_yaml_indentation_rejected_fail_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "base.yaml").write_text(
                textwrap.dedent(BASE_YAML).strip() + "\n",
                encoding="utf-8",
            )
            (root / "forward_safe.yaml").write_text(
                "bot:\n   mode: FORWARD_TEST\n  profile: FORWARD_SAFE\n",
                encoding="utf-8",
            )
            with self.assertRaises(ConfigValidationError):
                load_config(config_path=root / "forward_safe.yaml")

    def test_fingerprint_canonicalization_stable_across_dict_order(self) -> None:
        config_a = {
            "bot": {
                "mode": RuntimeMode.FORWARD_TEST,
                "profile": ProfileName.FORWARD_SAFE,
                "expert_mode": False,
                "poll_interval_seconds": 5,
            },
            "symbols": {"allowlist": ["XAUUSD"]},
            "alpha": {"setup_cooldown_bars": 1},
            "regime": {"closed_bar_timeframe": "M5", "news_lockout_minutes": 30},
            "signal": {"min_score": 0.65, "ttl_seconds": 300},
            "risk": {"max_open_risk_pct": 1.0, "max_daily_loss_pct": 5.0},
            "governor": {
                "initial_state": "NORMAL",
                "kill_review_drawdown_pct": 15.0,
                "offensive_profiles_require_expert_mode": True,
            },
            "lifecycle": {"thesis_ttl_minutes": 60, "break_even_after_r": 1.0},
            "execution": {
                "signal_ttl_seconds": 300,
                "slippage_veto_ratio": 0.30,
                "max_spread_ratio": 1.80,
            },
            "telemetry": {
                "heartbeat_interval_seconds": 5,
                "emit_candidate_diagnostics": True,
            },
            "persistence": {
                "sqlite_path": "runtime/db/tsp_v2_runtime.sqlite3",
                "lock_path": "runtime/locks",
                "wal_enabled": True,
            },
            "contest": {"ranking_proxy_enabled": False, "contest_window_minutes": 1440},
            "deployment": {
                "runtime_root": "runtime",
                "log_root": "logs",
                "report_root": "reports",
                "allow_live_execution": False,
            },
            "news": {
                "provider_mode": "STATIC_FILE",
                "source_path": "deploy/v2/news/calendar_snapshot.json",
                "stale_warn_minutes": 15,
                "stale_soft_fail_minutes": 30,
                "stale_hard_fail_minutes": 60,
            },
        }
        config_b = {
            "news": {
                "stale_hard_fail_minutes": 60,
                "stale_warn_minutes": 15,
                "provider_mode": "STATIC_FILE",
                "source_path": "deploy/v2/news/calendar_snapshot.json",
                "stale_soft_fail_minutes": 30,
            },
            "deployment": {
                "allow_live_execution": False,
                "report_root": "reports",
                "runtime_root": "runtime",
                "log_root": "logs",
            },
            "contest": {"contest_window_minutes": 1440, "ranking_proxy_enabled": False},
            "persistence": {
                "wal_enabled": True,
                "lock_path": "runtime/locks",
                "sqlite_path": "runtime/db/tsp_v2_runtime.sqlite3",
            },
            "telemetry": {
                "emit_candidate_diagnostics": True,
                "heartbeat_interval_seconds": 5,
            },
            "execution": {
                "max_spread_ratio": 1.80,
                "slippage_veto_ratio": 0.30,
                "signal_ttl_seconds": 300,
            },
            "lifecycle": {"break_even_after_r": 1.0, "thesis_ttl_minutes": 60},
            "governor": {
                "offensive_profiles_require_expert_mode": True,
                "kill_review_drawdown_pct": 15.0,
                "initial_state": "NORMAL",
            },
            "risk": {"max_daily_loss_pct": 5.0, "max_open_risk_pct": 1.0},
            "signal": {"ttl_seconds": 300, "min_score": 0.65},
            "regime": {"news_lockout_minutes": 30, "closed_bar_timeframe": "M5"},
            "alpha": {"setup_cooldown_bars": 1},
            "symbols": {"allowlist": ["XAUUSD"]},
            "bot": {
                "poll_interval_seconds": 5,
                "expert_mode": False,
                "profile": ProfileName.FORWARD_SAFE,
                "mode": RuntimeMode.FORWARD_TEST,
            },
        }
        canonical_a = canonicalize_config_for_fingerprint(config_a)
        canonical_b = canonicalize_config_for_fingerprint(config_b)
        self.assertEqual(canonical_a, canonical_b)
        self.assertEqual(
            build_config_fingerprint(config_a),
            build_config_fingerprint(config_b),
        )


if __name__ == "__main__":
    unittest.main()
