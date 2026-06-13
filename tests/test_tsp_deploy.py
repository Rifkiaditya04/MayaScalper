from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from tests.test_tsp_bot import FakeSymbolInfo, _config
from tsp.deploy import (
    DeploymentGuardrails,
    SingleInstanceLock,
    BrokerClockProfile,
    TSPMT5Adapter,
    _build_legacy_settings,
    resolve_broker_clock_profile,
)


class TestTSPDeploy(unittest.TestCase):
    def test_mt5_adapter_server_time_tracks_latest_tick(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self._time = datetime(2026, 5, 22, 10, 48, 49, tzinfo=timezone.utc)

            def get_latest_tick(self, symbol: str):
                del symbol
                current = self._time
                self._time = self._time + timedelta(minutes=1)
                return type("Tick", (), {"time": current.timestamp() + (3 * 3600)})()

        adapter = TSPMT5Adapter(
            client=FakeClient(),  # type: ignore[arg-type]
            config=_config(),
            execute_orders=False,
            clock_profile=BrokerClockProfile(
                raw_server_time=datetime(2026, 5, 22, 13, 48, 49, tzinfo=timezone.utc),
                normalized_server_time=datetime(2026, 5, 22, 10, 48, 49, tzinfo=timezone.utc),
                offset_hours=3,
                residual_seconds=0.0,
            ),
        )

        first = adapter.get_server_time()
        second = adapter.get_server_time()

        self.assertEqual(first, datetime(2026, 5, 22, 10, 48, 49, tzinfo=timezone.utc))
        self.assertEqual(second, datetime(2026, 5, 22, 10, 49, 49, tzinfo=timezone.utc))

    def test_single_instance_lock_rejects_second_acquire(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "forward.lock"
            first = SingleInstanceLock(lock_path)
            second = SingleInstanceLock(lock_path)
            first.acquire()
            try:
                with self.assertRaises(RuntimeError):
                    second.acquire()
            finally:
                first.release()

    def test_guardrails_accept_valid_forward_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config()
            cfg = replace(
                cfg,
                credentials=replace(
                    cfg.credentials,
                    login="123456",
                    password="secret",
                    server="Demo-Server",
                    terminal_path=tmp,
                ),
                bot=replace(
                    cfg.bot,
                    db_path=Path(tmp) / "runtime" / "db" / "tsp.sqlite3",
                    log_dir=Path(tmp) / "logs",
                    state_dir=Path(tmp) / "runtime" / "state",
                ),
            )
            DeploymentGuardrails.validate(
                config=cfg,
                server_time=datetime.now(timezone.utc),
                symbol_info=FakeSymbolInfo(),
            )

            self.assertTrue(cfg.bot.log_dir.exists())
            self.assertTrue(cfg.bot.state_dir.exists())
            self.assertTrue(cfg.bot.db_path.parent.exists())

    def test_guardrails_reject_clock_skew(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(
                _config(),
                credentials=replace(
                    _config().credentials,
                    login="123456",
                    password="secret",
                    server="Demo-Server",
                    terminal_path=tmp,
                ),
                bot=replace(
                    _config().bot,
                    db_path=Path(tmp) / "runtime" / "db" / "tsp.sqlite3",
                    log_dir=Path(tmp) / "logs",
                    state_dir=Path(tmp) / "runtime" / "state",
                ),
            )
            with self.assertRaises(RuntimeError):
                DeploymentGuardrails.validate(
                    config=cfg,
                    server_time=datetime.now(timezone.utc) - timedelta(minutes=10),
                    symbol_info=FakeSymbolInfo(),
                )

    def test_build_legacy_settings_maps_tsp_config(self) -> None:
        cfg = _config()
        settings = _build_legacy_settings(cfg)

        self.assertEqual(settings.symbol, "XAUUSD")
        self.assertEqual(settings.magic_number, cfg.bot.magic_number)
        self.assertEqual(settings.login, cfg.credentials.login)

    def test_resolve_broker_clock_profile_normalizes_whole_hour_offset(self) -> None:
        now = datetime(2026, 5, 22, 8, 27, 48, tzinfo=timezone.utc)
        raw_server_time = now + timedelta(hours=3, seconds=2)

        profile = resolve_broker_clock_profile(raw_server_time=raw_server_time, now_utc=now)

        self.assertEqual(profile.offset_hours, 3)
        self.assertLessEqual(profile.residual_seconds, 5.0)
        self.assertEqual(profile.normalized_server_time, now + timedelta(seconds=2))


if __name__ == "__main__":
    unittest.main()
