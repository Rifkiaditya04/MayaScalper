"""Indikator dasar untuk decision engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(slots=True)
class CandleStats:
    body: float
    upper_wick: float
    lower_wick: float
    range_size: float


def calculate_ma7(values: Iterable[float], period: int = 7) -> float:
    series = list(values)
    if len(series) < period:
        raise ValueError(f"Need at least {period} values to calculate MA.")
    window = series[-period:]
    return sum(window) / period


def calculate_atr(bars: list[dict[str, float]], period: int = 14) -> float:
    if len(bars) < period + 1:
        raise ValueError(f"Need at least {period + 1} bars to calculate ATR.")

    true_ranges: list[float] = []
    for idx in range(1, len(bars)):
        current = bars[idx]
        previous = bars[idx - 1]
        high = float(current["high"])
        low = float(current["low"])
        prev_close = float(previous["close"])
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges.append(tr)

    window = true_ranges[-period:]
    return sum(window) / period


def calculate_rsi(values: Iterable[float], period: int = 14) -> float:
    closes = list(values)
    if len(closes) < period + 1:
        raise ValueError(f"Need at least {period + 1} closes to calculate RSI.")

    gains: list[float] = []
    losses: list[float] = []
    for idx in range(1, len(closes)):
        delta = closes[idx] - closes[idx - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for idx in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[idx]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[idx]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def candle_stats(bar: dict[str, float]) -> CandleStats:
    open_ = float(bar["open"])
    high = float(bar["high"])
    low = float(bar["low"])
    close = float(bar["close"])
    body = abs(close - open_)
    range_size = max(high - low, 1e-9)
    upper_wick = max(high - max(open_, close), 0.0)
    lower_wick = max(min(open_, close) - low, 0.0)
    return CandleStats(
        body=body,
        upper_wick=upper_wick,
        lower_wick=lower_wick,
        range_size=range_size,
    )
