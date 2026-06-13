from __future__ import annotations

from datetime import datetime, timezone
import unittest

from tsp_v2.enums import SessionName
from tsp_v2.sessions import classify_session


class SessionTests(unittest.TestCase):
    def test_classify_london(self) -> None:
        session = classify_session(datetime(2026, 5, 26, 8, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(session, SessionName.LONDON)

    def test_classify_london_ny(self) -> None:
        session = classify_session(datetime(2026, 5, 26, 13, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(session, SessionName.LONDON_NY)

    def test_classify_dead_on_weekend(self) -> None:
        session = classify_session(datetime(2026, 5, 24, 8, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(session, SessionName.DEAD)


if __name__ == "__main__":
    unittest.main()
