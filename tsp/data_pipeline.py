"""Market data pipeline and snapshot builder for TSP V1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from statistics import median
from typing import Any, Iterable, Protocol, Sequence

from .state import CandleData, MarketSnapshot


ATR_PERIOD = 14
ATR_BASELINE_PERIOD = 20
COMPRESSION_WINDOW = 10
COMPRESSION_OFFSET_START = 5
COMPRESSION_OFFSET_END = 20
SWING_LOOKBACK_M5 = 6
M1_MIN_BARS = 40
M5_MIN_BARS = 40
M15_MIN_BARS = 40
H1_MIN_BARS = 40
ADX_PERIOD = 14


class MarketDataAdapter(Protocol):
    """Read-only market data interface required by the snapshot builder."""

    def get_rates(self, symbol: str, timeframe: str, count: int) -> Sequence[Any]:
        ...

    def get_latest_tick(self, symbol: str) -> Any:
        ...

    def get_symbol_info(self, symbol: str) -> Any:
        ...

    def get_server_time(self) -> datetime:
        ...


@dataclass(frozen=True, slots=True)
class SnapshotBuildConfig:
    m1_bars: int = M1_MIN_BARS
    m5_bars: int = M5_MIN_BARS
    m15_bars: int = M15_MIN_BARS
    h1_bars: int = H1_MIN_BARS
    recent_close_count: int = 8

    def __post_init__(self) -> None:
        minimum = max(ATR_PERIOD + ATR_BASELINE_PERIOD, COMPRESSION_OFFSET_END + COMPRESSION_WINDOW)
        if self.m1_bars < minimum:
            raise ValueError(f"m1_bars must be at least {minimum}")
        if self.m5_bars < max(ATR_PERIOD + ATR_BASELINE_PERIOD, SWING_LOOKBACK_M5):
            raise ValueError("m5_bars is too small for baseline and swing calculations")
        if self.m15_bars < ATR_PERIOD + ATR_BASELINE_PERIOD:
            raise ValueError("m15_bars is too small for baseline calculations")
        if self.h1_bars < ATR_PERIOD + ATR_BASELINE_PERIOD:
            raise ValueError("h1_bars is too small for baseline calculations")
        if self.recent_close_count < 2:
            raise ValueError("recent_close_count must be at least 2")


@dataclass(frozen=True, slots=True)
class SymbolContract:
    symbol: str
    digits: int
    point: float
    tick_size: float
    tick_value: float
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level: int
    freeze_level: int


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _record_value(record: Any, key: str) -> Any:
    if isinstance(record, dict):
        return record[key]
    return getattr(record, key)


def _normalize_rates(rates: Sequence[Any], timeframe: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for record in rates:
        timestamp_raw = _record_value(record, "time")
        if isinstance(timestamp_raw, datetime):
            timestamp = _to_utc(timestamp_raw)
        else:
            timestamp = datetime.fromtimestamp(float(timestamp_raw), tz=timezone.utc)
        normalized.append(
            {
                "timestamp": timestamp,
                "open": float(_record_value(record, "open")),
                "high": float(_record_value(record, "high")),
                "low": float(_record_value(record, "low")),
                "close": float(_record_value(record, "close")),
                "volume": float(
                    _record_value(record, "tick_volume")
                    if (
                        isinstance(record, dict) and "tick_volume" in record
                    ) or hasattr(record, "tick_volume")
                    else _record_value(record, "real_volume")
                ),
                "timeframe": timeframe,
            }
        )
    if not normalized:
        raise ValueError(f"No rates available for timeframe {timeframe}")
    return normalized


def _last_closed_bars(
    bars: Sequence[dict[str, Any]],
    *,
    timeframe: str,
    server_time: datetime,
) -> list[dict[str, Any]]:
    if not bars:
        raise ValueError(f"No bars available for timeframe {timeframe}")
    step = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}[timeframe]
    closed_cutoff = _to_utc(server_time) - timedelta(minutes=step)
    closed = [bar for bar in bars if bar["timestamp"] <= closed_cutoff]
    if not closed:
        raise ValueError(f"No fully closed bars available for timeframe {timeframe}")
    return closed


def _to_candle(record: dict[str, Any]) -> CandleData:
    return CandleData(
        timestamp=record["timestamp"],
        open=record["open"],
        high=record["high"],
        low=record["low"],
        close=record["close"],
        volume=record["volume"],
        timeframe=record["timeframe"],
    )


def _true_range(current: dict[str, Any], previous: dict[str, Any]) -> float:
    return max(
        current["high"] - current["low"],
        abs(current["high"] - previous["close"]),
        abs(current["low"] - previous["close"]),
    )


def _atr_series(bars: Sequence[dict[str, Any]]) -> list[float]:
    if len(bars) < 2:
        raise ValueError("Need at least 2 bars to compute ATR series")
    return [_true_range(bars[idx], bars[idx - 1]) for idx in range(1, len(bars))]


def _atr(bars: Sequence[dict[str, Any]], period: int = ATR_PERIOD) -> float:
    true_ranges = _atr_series(bars)
    if len(true_ranges) < period:
        raise ValueError(f"Need at least {period + 1} bars to compute ATR")
    window = true_ranges[-period:]
    return sum(window) / period


def _adx(bars: Sequence[dict[str, Any]], period: int = ADX_PERIOD) -> float:
    if len(bars) < (period * 2) + 1:
        raise ValueError(f"Need at least {(period * 2) + 1} bars to compute ADX")

    trs: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for idx in range(1, len(bars)):
        current = bars[idx]
        previous = bars[idx - 1]
        up_move = current["high"] - previous["high"]
        down_move = previous["low"] - current["low"]
        plus_dm.append(up_move if up_move > down_move and up_move > 0.0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0.0 else 0.0)
        trs.append(_true_range(current, previous))

    tr_smoothed = sum(trs[:period])
    plus_smoothed = sum(plus_dm[:period])
    minus_smoothed = sum(minus_dm[:period])
    dx_values: list[float] = []

    for idx in range(period, len(trs)):
        tr_smoothed = tr_smoothed - (tr_smoothed / period) + trs[idx]
        plus_smoothed = plus_smoothed - (plus_smoothed / period) + plus_dm[idx]
        minus_smoothed = minus_smoothed - (minus_smoothed / period) + minus_dm[idx]
        if tr_smoothed <= 0.0:
            dx_values.append(0.0)
            continue
        plus_di = 100.0 * (plus_smoothed / tr_smoothed)
        minus_di = 100.0 * (minus_smoothed / tr_smoothed)
        denominator = plus_di + minus_di
        dx_values.append(0.0 if denominator <= 0.0 else 100.0 * abs(plus_di - minus_di) / denominator)

    if len(dx_values) < period:
        raise ValueError(f"Not enough DX values to compute ADX with period {period}")
    adx = sum(dx_values[:period]) / period
    for value in dx_values[period:]:
        adx = ((adx * (period - 1)) + value) / period
    return adx


def _rolling_atr_values(
    bars: Sequence[dict[str, Any]],
    *,
    period: int = ATR_PERIOD,
) -> list[float]:
    if len(bars) < period + 1:
        raise ValueError(f"Need at least {period + 1} bars to compute rolling ATR values")
    values: list[float] = []
    for end_index in range(period + 1, len(bars) + 1):
        values.append(_atr(bars[:end_index], period=period))
    return values


def _median_last(values: Sequence[float], count: int) -> float:
    if len(values) < count:
        raise ValueError(f"Need at least {count} values for median window")
    return float(median(values[-count:]))


def _compression_window_median(values: Sequence[float]) -> float:
    start = len(values) - COMPRESSION_OFFSET_END
    stop = len(values) - COMPRESSION_OFFSET_START
    if start < 0 or stop - start < COMPRESSION_WINDOW:
        raise ValueError("Not enough ATR history for compression window")
    return float(median(values[start:stop]))


def _slope_from_closes(bars: Sequence[dict[str, Any]], periods_back: int = 3) -> float:
    if len(bars) <= periods_back:
        raise ValueError(f"Need at least {periods_back + 1} bars for slope")
    return float(bars[-1]["close"] - bars[-(periods_back + 1)]["close"])


def _session_name(server_time: datetime) -> str:
    utc_time = _to_utc(server_time).time()
    if time(0, 0) <= utc_time < time(7, 0):
        return "ASIA"
    if time(7, 0) <= utc_time < time(12, 0):
        return "LONDON"
    if time(12, 0) <= utc_time < time(16, 0):
        return "OVERLAP"
    if time(16, 0) <= utc_time < time(21, 0):
        return "NY"
    return "DEAD"


def _infer_news_window(server_time: datetime) -> bool:
    utc_time = _to_utc(server_time)
    minute_of_day = utc_time.hour * 60 + utc_time.minute
    scheduled_windows = (
        (13 * 60 + 25, 13 * 60 + 40),
        (15 * 60 + 55, 16 * 60 + 10),
    )
    return any(start <= minute_of_day <= end for start, end in scheduled_windows)


def _swing_high_low(bars: Sequence[dict[str, Any]], lookback: int = SWING_LOOKBACK_M5) -> tuple[float, float]:
    if len(bars) < lookback:
        raise ValueError(f"Need at least {lookback} M5 bars for swing reference")
    window = bars[-lookback:]
    return max(bar["high"] for bar in window), min(bar["low"] for bar in window)


def build_symbol_contract(symbol: str, raw_info: Any) -> SymbolContract:
    return SymbolContract(
        symbol=symbol,
        digits=int(getattr(raw_info, "digits")),
        point=float(getattr(raw_info, "point")),
        tick_size=float(getattr(raw_info, "trade_tick_size", getattr(raw_info, "point"))),
        tick_value=float(getattr(raw_info, "trade_tick_value", 0.0) or 0.0),
        volume_min=float(getattr(raw_info, "volume_min")),
        volume_max=float(getattr(raw_info, "volume_max")),
        volume_step=float(getattr(raw_info, "volume_step")),
        stops_level=int(getattr(raw_info, "trade_stops_level", 0) or 0),
        freeze_level=int(getattr(raw_info, "trade_freeze_level", 0) or 0),
    )


def build_market_snapshot(
    adapter: MarketDataAdapter,
    *,
    symbol: str,
    cfg: SnapshotBuildConfig | None = None,
    server_time: datetime | None = None,
    is_news_window: bool | None = None,
) -> MarketSnapshot:
    config = cfg or SnapshotBuildConfig()
    current_server_time = server_time or adapter.get_server_time()

    m1_bars = _last_closed_bars(
        _normalize_rates(adapter.get_rates(symbol, "M1", config.m1_bars), "M1"),
        timeframe="M1",
        server_time=current_server_time,
    )
    m5_bars = _last_closed_bars(
        _normalize_rates(adapter.get_rates(symbol, "M5", config.m5_bars), "M5"),
        timeframe="M5",
        server_time=current_server_time,
    )
    m15_bars = _last_closed_bars(
        _normalize_rates(adapter.get_rates(symbol, "M15", config.m15_bars), "M15"),
        timeframe="M15",
        server_time=current_server_time,
    )
    h1_bars = _last_closed_bars(
        _normalize_rates(adapter.get_rates(symbol, "H1", config.h1_bars), "H1"),
        timeframe="H1",
        server_time=current_server_time,
    )

    tick = adapter.get_latest_tick(symbol)
    tick_bid = float(getattr(tick, "bid"))
    tick_ask = float(getattr(tick, "ask"))
    raw_symbol_info = adapter.get_symbol_info(symbol)
    point = float(getattr(raw_symbol_info, "point"))
    spread_points = int(getattr(raw_symbol_info, "spread", 0) or 0)

    m1_atr_values = _rolling_atr_values(m1_bars)
    m5_atr_values = _rolling_atr_values(m5_bars)
    m15_atr_values = _rolling_atr_values(m15_bars)
    h1_atr_values = _rolling_atr_values(h1_bars)
    swing_high, swing_low = _swing_high_low(m5_bars)

    return MarketSnapshot(
        symbol=symbol,
        timestamp=m1_bars[-1]["timestamp"],
        m1=_to_candle(m1_bars[-1]),
        m5=_to_candle(m5_bars[-1]),
        m15=_to_candle(m15_bars[-1]),
        h1=_to_candle(h1_bars[-1]),
        atr_m1=_atr(m1_bars),
        atr_m1_base=_median_last(m1_atr_values, ATR_BASELINE_PERIOD),
        atr_m5=_atr(m5_bars),
        atr_m5_base=_median_last(m5_atr_values, ATR_BASELINE_PERIOD),
        atr_m15_base=_median_last(m15_atr_values, ATR_BASELINE_PERIOD),
        atr_h1_base=_median_last(h1_atr_values, ATR_BASELINE_PERIOD),
        atr_m1_prev_window=_compression_window_median(m1_atr_values),
        atr_m5_prev_window=_compression_window_median(m5_atr_values),
        adx_m5=_adx(m5_bars),
        adx_m15=_adx(m15_bars),
        adx_h1=_adx(h1_bars),
        h1_slope=_slope_from_closes(h1_bars),
        m15_slope=_slope_from_closes(m15_bars),
        spread_current=(tick_ask - tick_bid),
        spread_baseline=spread_points * point,
        tick_vol_m1=float(m1_bars[-1]["volume"]),
        tick_vol_m1_base=float(median(bar["volume"] for bar in m1_bars[-ATR_BASELINE_PERIOD:])),
        swing_high_m5=float(swing_high),
        swing_low_m5=float(swing_low),
        m1_closes_recent=tuple(
            float(bar["close"]) for bar in m1_bars[-config.recent_close_count :]
        ),
        is_news_window=_infer_news_window(current_server_time)
        if is_news_window is None
        else is_news_window,
        session=_session_name(current_server_time),
        bid=tick_bid,
        ask=tick_ask,
        source_server_time=current_server_time,
        is_closed_bar=True,
    )


__all__ = [
    "ATR_BASELINE_PERIOD",
    "ATR_PERIOD",
    "COMPRESSION_OFFSET_END",
    "COMPRESSION_OFFSET_START",
    "COMPRESSION_WINDOW",
    "MarketDataAdapter",
    "SnapshotBuildConfig",
    "SymbolContract",
    "build_market_snapshot",
    "build_symbol_contract",
]
