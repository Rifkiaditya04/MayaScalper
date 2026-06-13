from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone

from tsp_v2.enums import GovernorState, PaceClassification, TelemetryCategory, TelemetrySeverity
from tsp_v2.telemetry import (
    AlertRoute,
    TelemetryCollector,
    TelemetryEvent,
    TelemetryValidationError,
    build_runtime_metrics_snapshot,
    emit_event,
    serialize_event,
)


class TelemetryTests(unittest.TestCase):
    def test_event_serialization_is_structured_and_deterministic(self) -> None:
        event = emit_event(
            "GOVERNOR:INFO",
            {
                "timestamp_utc": "2026-05-29T09:00:00+00:00",
                "category": "GOVERNOR",
                "severity": "INFO",
                "event_id": "governor.transition.normal_to_attack",
                "message": "Governor transition NORMAL -> ATTACK",
                "metadata": {"from_state": "NORMAL", "to_state": "ATTACK"},
            },
        )
        payload = json.loads(serialize_event(event))
        self.assertEqual(payload["category"], TelemetryCategory.GOVERNOR.value)
        self.assertEqual(payload["severity"], TelemetrySeverity.INFO.value)
        self.assertEqual(payload["event_id"], "governor.transition.normal_to_attack")
        self.assertEqual(payload["metadata"]["to_state"], "ATTACK")

    def test_alert_routing_maps_error_and_critical(self) -> None:
        collector = TelemetryCollector(session_started_at_utc=_utc(2026, 5, 29, 9, 0, 0))
        warning_event = collector.emit_event(
            category=TelemetryCategory.SYSTEM,
            severity=TelemetrySeverity.WARNING,
            event_id="system.warning",
            message="warning",
        )
        error_event = collector.emit_event(
            category=TelemetryCategory.SYSTEM,
            severity=TelemetrySeverity.ERROR,
            event_id="system.error",
            message="error",
        )
        critical_event = collector.emit_event(
            category=TelemetryCategory.SYSTEM,
            severity=TelemetrySeverity.CRITICAL,
            event_id="system.critical",
            message="critical",
        )
        self.assertEqual(collector.route_alert(warning_event), AlertRoute.OPERATOR_ATTENTION)
        self.assertEqual(collector.route_alert(error_event), AlertRoute.OPERATOR_ATTENTION)
        self.assertEqual(collector.route_alert(critical_event), AlertRoute.IMMEDIATE_ESCALATION)

    def test_metrics_aggregation_and_daily_summary(self) -> None:
        collector = TelemetryCollector(session_started_at_utc=_utc(2026, 5, 29, 8, 0, 0), starting_balance=100000.0)
        collector.record_governor_transition(
            from_state=GovernorState.NORMAL,
            to_state=GovernorState.ATTACK,
            reason="pace_ahead",
            timestamp_utc=_utc(2026, 5, 29, 9, 0, 0),
        )
        collector.record_execution_event(
            kind="filled",
            timestamp_utc=_utc(2026, 5, 29, 9, 5, 0),
            metadata={"result": "WIN"},
        )
        collector.record_execution_event(
            kind="rejected",
            timestamp_utc=_utc(2026, 5, 29, 9, 6, 0),
            metadata={"reason": "spread_degraded"},
        )
        collector.record_recovery_event(
            stage="bootstrap",
            outcome="ready",
            timestamp_utc=_utc(2026, 5, 29, 9, 7, 0),
        )
        collector.record_runtime_metrics(
            build_runtime_metrics_snapshot(
                captured_at_utc=_utc(2026, 5, 29, 10, 0, 0),
                equity=100300.0,
                balance=100250.0,
                drawdown=1.5,
                active_positions=2,
                signal_count=4,
                execution_count=2,
                win_rate=0.5,
                governor_state=GovernorState.ATTACK,
                pace_state=PaceClassification.AHEAD,
            )
        )
        summary = collector.build_daily_summary(summary_date=date(2026, 5, 29))
        self.assertEqual(summary.date, date(2026, 5, 29))
        self.assertEqual(summary.pnl, 250.0)
        self.assertEqual(summary.trades, 2)
        self.assertEqual(summary.wins, 1)
        self.assertEqual(summary.losses, 0)
        self.assertEqual(summary.governor_transitions, 1)
        self.assertEqual(summary.execution_failures, 1)
        self.assertEqual(summary.recovery_events, 1)
        self.assertEqual(summary.governor_state, GovernorState.ATTACK.value)
        self.assertEqual(summary.pace_state, PaceClassification.AHEAD.value)

        records = collector.export_index_records()
        topics = [record.topic for record in records]
        self.assertIn("telemetry.event.governor", topics)
        self.assertIn("telemetry.event.execution", topics)
        self.assertIn("telemetry.summary.daily", topics)

    def test_governor_and_execution_telemetry_emit_structured_payloads(self) -> None:
        collector = TelemetryCollector(session_started_at_utc=_utc(2026, 5, 29, 8, 0, 0))
        governor_event = collector.record_governor_transition(
            from_state=GovernorState.NORMAL,
            to_state=GovernorState.KILL_REVIEW,
            reason="hard_shutdown",
            timestamp_utc=_utc(2026, 5, 29, 9, 0, 0),
        )
        execution_event = collector.record_execution_event(
            kind="intent_rejected",
            timestamp_utc=_utc(2026, 5, 29, 9, 1, 0),
            metadata={"reason": "expired"},
        )
        self.assertEqual(governor_event.category, TelemetryCategory.GOVERNOR)
        self.assertEqual(governor_event.severity, TelemetrySeverity.CRITICAL)
        self.assertEqual(execution_event.category, TelemetryCategory.EXECUTION)
        self.assertEqual(execution_event.severity, TelemetrySeverity.WARNING)
        self.assertEqual(execution_event.event_id, "execution.intent_rejected")

    def test_malformed_payload_rejected(self) -> None:
        with self.assertRaises(TelemetryValidationError):
            emit_event(
                "SYSTEM:INFO",
                {
                    "timestamp_utc": "not-a-datetime",
                    "event_id": "bad",
                    "message": "bad",
                    "metadata": "not-a-mapping",
                },
            )


def _utc(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
