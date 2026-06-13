"""Market data contracts for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class MarketTick:
    timestamp_utc: datetime
    bid: float
    ask: float


class MarketDataProvider(Protocol):
    def get_broker_time(self) -> datetime:
        ...

    def get_latest_tick(self, symbol: str) -> dict[str, Any]:
        ...

    def get_rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, Any]]:
        ...

    def get_symbol_contract(self, symbol: str) -> dict[str, Any]:
        ...
