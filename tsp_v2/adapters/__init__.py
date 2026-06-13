"""Adapter contracts for TSP V2."""

from __future__ import annotations

from .execution_adapter import ExecutionAdapter, MT5ExecutionAdapter
from .market_adapter import MT5MarketAdapter, MarketAdapter, MarketAdapterStatus
from .mt5_bridge import MT5Bridge, MT5BridgeError, MT5BridgeStatus, MT5TradeResult

__all__ = [
    "MT5MarketAdapter",
    "MarketAdapter",
    "MT5ExecutionAdapter",
    "MT5Bridge",
    "MT5BridgeError",
    "MT5BridgeStatus",
    "MT5TradeResult",
    "MarketAdapterStatus",
    "ExecutionAdapter",
]
