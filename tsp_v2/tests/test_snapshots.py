from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import textwrap
import unittest

from tsp_v2.config import load_config
from tsp_v2.config_schema import ConfigValidationError
from tsp_v2.enums import HealthState, SessionName
from tsp_v2.snapshots import SnapshotBuildConfig, build_market_snapshot


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
  source_path: {news_path}
  stale_warn_minutes: 15
  stale_soft_fail_minutes: 30
  stale_hard_fail_minutes: 60
"""


class FakeMarketProvider:
    def __init__(self, *, tick_time: datetime, m1_last_time: datetime) -> None:
        self._tick_time = tick_time
        self._m1_last_time = m1_last_time

    def get_broker_time(self) -> datetime:
        return self._tick_time

    def get_latest_tick(self, symbol: str) -> dict[str, object]:
        del symbol
        return {
            "time": self._tick_time,
            "bid": 2350.0,
            "ask": 2350.5,
        }

    def get_rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, object]]:
        del symbol
        step = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}[timeframe]
        anchor = {
            "M1": self._m1_last_time,
            "M5": self._m1_last_time - timedelta(minutes=4),
            "M15": self._m1_last_time - timedelta(minutes=14),
            "H1": self._m1_last_time - timedelta(minutes=59),
        }[timeframe]
        bars: list[dict[str, object]] = []
        start = anchor - timedelta(minutes=step * (count - 1))
        current = start
        for idx in range(count):
            open_price = 2300.0 + idx * 0.5
            close_price = open_price + 0.2
            bars.append(
                {
                    "time": current,
                    "open": open_price,
                    "high": close_price + 0.3,
                    "low": open_price - 0.3,
                    "close": close_price,
                    "tick_volume": 100 + idx,
                }
            )
            current += timedelta(minutes=step)
        return bars

    def get_symbol_contract(self, symbol: str) -> dict[str, object]:
        return {
            "symbol": symbol,
            "point": 0.1,
            "trade_tick_size": 0.1,
            "trade_tick_value": 1.0,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "trade_stops_level": 20,
            "trade_freeze_level": 0,
        }


class ThinMarketProvider(FakeMarketProvider):
    def __init__(self, *, m5_shortfall: int = 0, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._m5_shortfall = m5_shortfall

    def get_rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, object]]:
        rates = super().get_rates(symbol, timeframe, count)
        if timeframe == "M5" and self._m5_shortfall > 0:
            return rates[: max(0, len(rates) - self._m5_shortfall)]
        return rates


class M5OpenBarProvider(FakeMarketProvider):
    def get_rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, object]]:
        del symbol
        step = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}[timeframe]
        if timeframe == "M5":
            last_time = self._tick_time
        else:
            last_time = self._tick_time - timedelta(minutes=step)
        start = last_time - timedelta(minutes=step * (count - 1))
        bars: list[dict[str, object]] = []
        current = start
        for idx in range(count):
            open_price = 2300.0 + idx * 0.5
            close_price = open_price + 0.2
            bars.append(
                {
                    "time": current,
                    "open": open_price,
                    "high": close_price + 0.3,
                    "low": open_price - 0.3,
                    "close": close_price,
                    "tick_volume": 100 + idx,
                }
            )
            current += timedelta(minutes=step)
        return bars


class SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved_env)

    def test_snapshot_uses_last_closed_bar_only(self) -> None:
        cycle_time = datetime(2026, 5, 26, 13, 10, 0, tzinfo=timezone.utc)
        provider = FakeMarketProvider(
            tick_time=cycle_time,
            m1_last_time=cycle_time - timedelta(minutes=1),
        )
        diagnostics: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._load_config(Path(tmp_dir))
            snapshot = build_market_snapshot(
                provider,
                config=config,
                symbol="XAUUSD",
                cycle_time_utc=cycle_time,
                build_config=SnapshotBuildConfig(),
                diagnostics_hook=diagnostics.append,
            )
        self.assertEqual(snapshot.bars_m1[-1]["close_time_utc"], cycle_time)
        self.assertEqual(snapshot.session, SessionName.LONDON_NY)
        self.assertEqual(snapshot.payload_health, HealthState.GREEN)
        self.assertTrue(diagnostics)
        self.assertEqual(diagnostics[-1]["stage"], "snapshot_ready")
        self.assertEqual(diagnostics[-1]["symbol"], "XAUUSD")
        self.assertEqual(diagnostics[-1]["returned_bars"]["M1"], 40)
        self.assertEqual(diagnostics[-1]["closed_bar_count"]["M1"], 40)

    def test_snapshot_emits_closed_bar_diagnostics_before_rejecting(self) -> None:
        cycle_time = datetime(2026, 5, 26, 13, 10, 0, tzinfo=timezone.utc)
        provider = FakeMarketProvider(
            tick_time=cycle_time,
            m1_last_time=datetime(2026, 5, 26, 13, 20, 0, tzinfo=timezone.utc),
        )
        diagnostics: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._load_config(Path(tmp_dir))
            with self.assertRaises(ConfigValidationError):
                build_market_snapshot(
                    provider,
                    config=config,
                    symbol="XAUUSD",
                    cycle_time_utc=cycle_time,
                    diagnostics_hook=diagnostics.append,
                )
        self.assertTrue(diagnostics)
        self.assertEqual(diagnostics[-1]["stage"], "closed_bars_insufficient")
        self.assertEqual(diagnostics[-1]["symbol"], "XAUUSD")
        self.assertEqual(diagnostics[-1]["timeframe"], "M1")
        self.assertEqual(diagnostics[-1]["returned_bars"], 40)
        self.assertLess(diagnostics[-1]["closed_bar_count"], 34)

    def test_snapshot_emits_rates_fetch_failure_diagnostics(self) -> None:
        class BrokenRatesProvider(FakeMarketProvider):
            def get_rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, object]]:
                if timeframe == "M1":
                    raise RuntimeError("simulated rates fetch failure")
                return super().get_rates(symbol, timeframe, count)

        cycle_time = datetime(2026, 5, 26, 13, 10, 0, tzinfo=timezone.utc)
        provider = BrokenRatesProvider(
            tick_time=cycle_time,
            m1_last_time=datetime(2026, 5, 26, 13, 10, 0, tzinfo=timezone.utc),
        )
        diagnostics: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._load_config(Path(tmp_dir))
            with self.assertRaises(RuntimeError):
                build_market_snapshot(
                    provider,
                    config=config,
                    symbol="XAUUSD",
                    cycle_time_utc=cycle_time,
                    diagnostics_hook=diagnostics.append,
                )
        self.assertTrue(diagnostics)
        self.assertEqual(diagnostics[-1]["stage"], "rates_fetch_failed")
        self.assertEqual(diagnostics[-1]["symbol"], "XAUUSD")
        self.assertEqual(diagnostics[-1]["timeframe"], "M1")
        self.assertEqual(diagnostics[-1]["requested_bars"], 40)
        self.assertEqual(diagnostics[-1]["returned_bars"], 0)

    def test_snapshot_marks_partial_payload_yellow_when_thin_but_valid(self) -> None:
        cycle_time = datetime(2026, 5, 26, 13, 10, 0, tzinfo=timezone.utc)
        provider = ThinMarketProvider(
            tick_time=cycle_time,
            m1_last_time=cycle_time - timedelta(minutes=1),
            m5_shortfall=1,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._load_config(Path(tmp_dir))
            snapshot = build_market_snapshot(
                provider,
                config=config,
                symbol="XAUUSD",
                cycle_time_utc=cycle_time,
                build_config=SnapshotBuildConfig(m5_bars=71),
            )
        self.assertEqual(snapshot.payload_health, HealthState.YELLOW)
        self.assertEqual(snapshot.payload_diagnostics["status"], "YELLOW")
        self.assertIn("M5", snapshot.payload_diagnostics["thin_timeframes"])

    def test_snapshot_rejects_partial_payload_below_minimum(self) -> None:
        cycle_time = datetime(2026, 5, 26, 13, 10, 0, tzinfo=timezone.utc)
        provider = ThinMarketProvider(
            tick_time=cycle_time,
            m1_last_time=cycle_time - timedelta(minutes=1),
            m5_shortfall=2,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._load_config(Path(tmp_dir))
            with self.assertRaises(ConfigValidationError):
                build_market_snapshot(
                    provider,
                    config=config,
                    symbol="XAUUSD",
                    cycle_time_utc=cycle_time,
                    build_config=SnapshotBuildConfig(m5_bars=71),
                )

    def test_snapshot_emits_m5_raw_bar_dump_when_m5_closed_bars_are_insufficient(self) -> None:
        cycle_time = datetime(2026, 5, 26, 13, 10, 0, tzinfo=timezone.utc)
        provider = M5OpenBarProvider(
            tick_time=cycle_time,
            m1_last_time=cycle_time - timedelta(minutes=1),
        )
        diagnostics: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._load_config(Path(tmp_dir))
            with self.assertRaises(ConfigValidationError):
                build_market_snapshot(
                    provider,
                    config=config,
                    symbol="XAUUSD",
                    cycle_time_utc=cycle_time,
                    build_config=SnapshotBuildConfig(m5_bars=70),
                    diagnostics_hook=diagnostics.append,
                )
        self.assertTrue(diagnostics)
        payload = diagnostics[-1]
        self.assertEqual(payload["stage"], "closed_bars_insufficient")
        self.assertEqual(payload["timeframe"], "M5")
        self.assertEqual(payload["requested_bars"], 70)
        self.assertEqual(payload["returned_bars"], 70)
        self.assertEqual(payload["closed_bar_count"], 69)
        self.assertEqual(len(payload["m5_raw_bar_dump"]), 70)
        self.assertTrue(payload["m5_raw_bar_dump"][0]["closed"])
        self.assertFalse(payload["m5_raw_bar_dump"][-1]["closed"])
        self.assertEqual(payload["m5_raw_bar_dump"][-1]["close_time_utc"], "2026-05-26T13:15:00+00:00")

    def test_snapshot_rejects_unsupported_symbol(self) -> None:
        cycle_time = datetime(2026, 5, 26, 13, 10, 0, tzinfo=timezone.utc)
        provider = FakeMarketProvider(
            tick_time=cycle_time,
            m1_last_time=cycle_time - timedelta(minutes=1),
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._load_config(Path(tmp_dir))
            with self.assertRaises(ConfigValidationError):
                build_market_snapshot(
                    provider,
                    config=config,
                    symbol="GBPUSD",
                    cycle_time_utc=cycle_time,
                )

    def test_snapshot_marks_feed_red_on_stale_tick(self) -> None:
        cycle_time = datetime(2026, 5, 26, 13, 10, 0, tzinfo=timezone.utc)
        provider = FakeMarketProvider(
            tick_time=cycle_time - timedelta(seconds=20),
            m1_last_time=cycle_time - timedelta(minutes=1),
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._load_config(Path(tmp_dir))
            snapshot = build_market_snapshot(
                provider,
                config=config,
                symbol="XAUUSD",
                cycle_time_utc=cycle_time,
            )
        self.assertEqual(snapshot.feed_health, HealthState.RED)
        self.assertEqual(snapshot.latency_health, HealthState.RED)

    def test_snapshot_rejects_invalid_contract(self) -> None:
        class BadContractProvider(FakeMarketProvider):
            def get_symbol_contract(self, symbol: str) -> dict[str, object]:
                contract = super().get_symbol_contract(symbol)
                contract["point"] = 0.0
                return contract

        cycle_time = datetime(2026, 5, 26, 13, 10, 0, tzinfo=timezone.utc)
        provider = BadContractProvider(
            tick_time=cycle_time,
            m1_last_time=cycle_time - timedelta(minutes=1),
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._load_config(Path(tmp_dir))
            with self.assertRaises(ConfigValidationError):
                build_market_snapshot(
                    provider,
                    config=config,
                    symbol="XAUUSD",
                    cycle_time_utc=cycle_time,
                )

    def test_snapshot_rejects_timestamp_regression(self) -> None:
        cycle_time = datetime(2026, 5, 26, 13, 10, 0, tzinfo=timezone.utc)
        provider = FakeMarketProvider(
            tick_time=cycle_time,
            m1_last_time=cycle_time - timedelta(minutes=1),
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._load_config(Path(tmp_dir))
            with self.assertRaises(ConfigValidationError):
                build_market_snapshot(
                    provider,
                    config=config,
                    symbol="XAUUSD",
                    cycle_time_utc=cycle_time,
                    previous_cycle_time_utc=cycle_time + timedelta(seconds=5),
                )

    def _load_config(self, root: Path):
        news_path = root / "news.json"
        news_path.write_text(
            json.dumps({"generated_at_utc": "2026-05-26T13:00:00+00:00", "events": []}),
            encoding="utf-8",
        )
        base_text = BASE_TEMPLATE.format(news_path=str(news_path).replace("\\", "/"))
        (root / "base.yaml").write_text(textwrap.dedent(base_text).strip() + "\n", encoding="utf-8")
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
