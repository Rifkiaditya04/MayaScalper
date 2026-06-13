"""News provider protocol for TSP V2."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from ..enums import NewsProviderMode, NewsProviderState


class NewsProviderSnapshot(Protocol):
    provider_mode: NewsProviderMode
    provider_state: NewsProviderState
    snapshot_generated_at_utc: datetime | None
    relevant_events: tuple[dict[str, Any], ...]
    lockout_active: bool
    next_relevant_event_utc: datetime | None


class NewsProvider(Protocol):
    def snapshot(self, *, at_utc: datetime, symbol: str) -> dict[str, Any]:
        ...
