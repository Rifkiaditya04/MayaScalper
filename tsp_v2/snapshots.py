"""Immutable market snapshot builder for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Callable, Sequence

from .config import AppConfig
from .config_schema import ConfigValidationError
from .enums import HealthState
from .market_data import MarketDataProvider
from .models import ContractSnapshot, MarketSnapshot
from .news import build_news_snapshot
from .sessions import classify_session


TIMEFRAME_MINUTES: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "H1": 60,
}
ATR_PERIOD = 14
ATR_BASELINE_PERIOD = 20
MIN_BARS_PER_TIMEFRAME = 40
MIN_BARS_BREAKOUT_M5 = 70
PAYLOAD_YELLOW_TOLERANCE = 0


@dataclass(frozen=True, slots=True)
class SnapshotBuildConfig:
    m1_bars: int = MIN_BARS_PER_TIMEFRAME
    # Request one extra raw M5 bar so the closed-bar filter still has 70 closed bars
    # even when the most recent raw bar is still forming.
    m5_bars: int = MIN_BARS_BREAKOUT_M5 + 1
    m15_bars: int = MIN_BARS_PER_TIMEFRAME
    h1_bars: int = MIN_BARS_PER_TIMEFRAME

    def __post_init__(self) -> None:
        minimum = ATR_PERIOD + ATR_BASELINE_PERIOD
        for field_name in ("m1_bars", "m15_bars", "h1_bars"):
            value = getattr(self, field_name)
            if value < minimum:
                raise ValueError(f"{field_name} must be at least {minimum}")
        if self.m5_bars < MIN_BARS_BREAKOUT_M5:
            raise ValueError(f"m5_bars must be at least {MIN_BARS_BREAKOUT_M5}")


def build_market_snapshot(
    provider: MarketDataProvider,
    *,
    config: AppConfig,
    symbol: str,
    cycle_time_utc: datetime,
    previous_cycle_time_utc: datetime | None = None,
    build_config: SnapshotBuildConfig | None = None,
    diagnostics_hook: Callable[[dict[str, Any]], None] | None = None,
) -> MarketSnapshot:
    if symbol.upper() not in config.symbols.allowlist:
        raise ConfigValidationError(f"Unsupported symbol for V2 runtime: {symbol}")

    cfg = build_config or SnapshotBuildConfig()
    cycle_utc = _ensure_utc(cycle_time_utc, field_name="cycle_time_utc")
    _validate_cycle_monotonicity(previous_cycle_time_utc=previous_cycle_time_utc, cycle_time_utc=cycle_utc)
    symbol_name = symbol.upper()
    requested_counts = {
        "M1": cfg.m1_bars,
        "M5": cfg.m5_bars,
        "M15": cfg.m15_bars,
        "H1": cfg.h1_bars,
    }

    def emit_diagnostics(payload: dict[str, Any]) -> None:
        if diagnostics_hook is not None:
            diagnostics_hook(payload)

    def build_raw_bar_dump(bars: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "index": index,
                "timestamp": bar["timestamp"].isoformat(),
                "close_time_utc": bar["close_time_utc"].isoformat(),
                "closed": bar["close_time_utc"] <= cycle_utc,
            }
            for index, bar in enumerate(bars)
        ]

    def build_raw_bar_stats(bars: Sequence[dict[str, Any]]) -> dict[str, Any]:
        close_times = [bar["close_time_utc"] for bar in bars]
        return {
            "closed_count": sum(1 for bar in bars if bar["close_time_utc"] <= cycle_utc),
            "forming_count": sum(
                1 for bar in bars if bar["timestamp"] <= cycle_utc < bar["close_time_utc"]
            ),
            "future_count": sum(1 for bar in bars if bar["timestamp"] > cycle_utc),
            "duplicate_close_time_count": sum(
                count - 1 for count in Counter(close_times).values() if count > 1
            ),
            "oldest_close_time_utc": min(close_times).isoformat() if close_times else None,
            "latest_close_time_utc": max(close_times).isoformat() if close_times else None,
        }

    def build_time_context(
        *,
        timeframe: str,
        raw_bars: Sequence[dict[str, Any]],
        closed_bars: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        timeframe_key = timeframe.lower()
        latest_raw_bar = max(raw_bars, key=lambda bar: bar["close_time_utc"])
        latest_closed_bar = max(closed_bars, key=lambda bar: bar["close_time_utc"]) if closed_bars else None
        latest_tick_timestamp = tick["timestamp"]
        context: dict[str, Any] = {
            "broker_time_utc": cycle_utc.isoformat(),
            "latest_tick_timestamp_utc": latest_tick_timestamp.isoformat(),
            f"{timeframe_key}_latest_raw_open_time_utc": latest_raw_bar["timestamp"].isoformat(),
            f"{timeframe_key}_latest_raw_close_time_utc": latest_raw_bar["close_time_utc"].isoformat(),
            f"{timeframe_key}_latest_closed_bar_close_time_utc": (
                latest_closed_bar["close_time_utc"].isoformat() if latest_closed_bar is not None else None
            ),
            "broker_time_minus_latest_tick_seconds": (cycle_utc - latest_tick_timestamp).total_seconds(),
            "broker_time_minus_latest_raw_bar_seconds": (cycle_utc - latest_raw_bar["close_time_utc"]).total_seconds(),
            "broker_time_minus_latest_closed_bar_seconds": (
                (cycle_utc - latest_closed_bar["close_time_utc"]).total_seconds() if latest_closed_bar is not None else None
            ),
        }
        return context

    def fetch_rates(timeframe: str, count: int) -> Sequence[Any]:
        try:
            return provider.get_rates(symbol, timeframe, count)
        except Exception as exc:
            diagnostics = getattr(getattr(exc, "status", None), "diagnostics", {}) or {}
            emit_diagnostics(
                {
                    "stage": "rates_fetch_failed",
                    "symbol": symbol_name,
                    "broker_time_utc": cycle_utc.isoformat(),
                    "timeframe": timeframe,
                    "cycle_time_utc": cycle_utc.isoformat(),
                    "requested_bars": count,
                    "returned_bars": 0,
                    "closed_bar_count": 0,
                    "rates_none": diagnostics.get("raw_type") is None,
                    "exception_class": exc.__class__.__name__,
                    "message": str(exc),
                    "exception_diagnostics": diagnostics,
                }
            )
            raise

    raw_tick = provider.get_latest_tick(symbol)
    raw_contract = provider.get_symbol_contract(symbol)
    raw_m1 = fetch_rates("M1", cfg.m1_bars)
    raw_m5 = fetch_rates("M5", cfg.m5_bars)
    raw_m15 = fetch_rates("M15", cfg.m15_bars)
    raw_h1 = fetch_rates("H1", cfg.h1_bars)

    raw_counts = {
        "M1": len(raw_m1),
        "M5": len(raw_m5),
        "M15": len(raw_m15),
        "H1": len(raw_h1),
    }

    payload_health, payload_diagnostics = evaluate_payload_health(
        requested_counts=requested_counts,
        raw_counts=raw_counts,
    )
    if payload_health is HealthState.RED:
        emit_diagnostics(
            {
                "stage": "payload_rejected",
                "symbol": symbol_name,
                "broker_time_utc": cycle_utc.isoformat(),
                "cycle_time_utc": cycle_utc.isoformat(),
                "requested_bars": requested_counts,
                "returned_bars": raw_counts,
                "closed_bar_count": {},
                "payload_health": payload_health.value,
                "payload_diagnostics": payload_diagnostics,
            }
        )
        raise ConfigValidationError(
            "Partial payload rejected for snapshot build: "
            f"{payload_diagnostics['status']} | {payload_diagnostics['details']}"
        )

    tick = _normalize_tick(raw_tick)
    contract = build_symbol_contract(symbol=symbol, raw_contract=raw_contract)
    bars_m1_all = _normalize_rates(raw_m1, timeframe="M1")
    bars_m5_all = _normalize_rates(raw_m5, timeframe="M5")
    bars_m15_all = _normalize_rates(raw_m15, timeframe="M15")
    bars_h1_all = _normalize_rates(raw_h1, timeframe="H1")

    bars_m1 = _closed_bars(bars_m1_all, cycle_time_utc=cycle_utc)
    bars_m5 = _closed_bars(bars_m5_all, cycle_time_utc=cycle_utc)
    bars_m15 = _closed_bars(bars_m15_all, cycle_time_utc=cycle_utc)
    bars_h1 = _closed_bars(bars_h1_all, cycle_time_utc=cycle_utc)

    closed_counts = {
        "M1": len(bars_m1),
        "M5": len(bars_m5),
        "M15": len(bars_m15),
        "H1": len(bars_h1),
    }
    minimum_counts = {
        "M1": ATR_PERIOD + ATR_BASELINE_PERIOD,
        "M5": MIN_BARS_BREAKOUT_M5,
        "M15": ATR_PERIOD + ATR_BASELINE_PERIOD,
        "H1": ATR_PERIOD + ATR_BASELINE_PERIOD,
    }
    raw_bars_by_timeframe = {
        "M1": bars_m1_all,
        "M5": bars_m5_all,
        "M15": bars_m15_all,
        "H1": bars_h1_all,
    }
    for timeframe, bars in (("M1", bars_m1), ("M5", bars_m5), ("M15", bars_m15), ("H1", bars_h1)):
        if len(bars) < minimum_counts[timeframe]:
            raw_bars = raw_bars_by_timeframe[timeframe]
            raw_dump_key = f"{timeframe.lower()}_raw_bar_dump"
            raw_stats_key = f"{timeframe.lower()}_raw_bar_stats"
            time_context_key = f"{timeframe.lower()}_time_context"
            emit_diagnostics(
                {
                    "stage": "closed_bars_insufficient",
                    "symbol": symbol_name,
                    "broker_time_utc": cycle_utc.isoformat(),
                    "timeframe": timeframe,
                    "cycle_time_utc": cycle_utc.isoformat(),
                    "requested_bars": requested_counts[timeframe],
                    "returned_bars": raw_counts[timeframe],
                    "closed_bar_count": len(bars),
                    "minimum_closed_bar_count": minimum_counts[timeframe],
                    raw_dump_key: build_raw_bar_dump(raw_bars),
                    raw_stats_key: build_raw_bar_stats(raw_bars),
                    time_context_key: build_time_context(
                        timeframe=timeframe,
                        raw_bars=raw_bars,
                        closed_bars=bars,
                    ),
                    "payload_health": payload_health.value,
                    "payload_diagnostics": payload_diagnostics,
                }
            )
            raise ConfigValidationError(
                f"Not enough closed bars for timeframe {timeframe}: need at least {minimum_counts[timeframe]}"
            )

    news_snapshot = build_news_snapshot(
        cycle_time_utc=cycle_utc,
        config=config,
        symbol=symbol,
    )
    feed_health = _classify_feed_health(
        tick=tick,
        cycle_time_utc=cycle_utc,
        bid=tick["bid"],
        ask=tick["ask"],
        point=contract.point,
    )

    spread_points = (tick["ask"] - tick["bid"]) / contract.point
    indicator_bundle = _build_indicator_bundle(
        bars_m1=bars_m1,
        bars_m5=bars_m5,
        bars_m15=bars_m15,
        bars_h1=bars_h1,
        spread_points=spread_points,
    )
    spread_ratio = (
        spread_points / indicator_bundle["spread_points_baseline"]
        if indicator_bundle["spread_points_baseline"] > 0.0
        else 0.0
    )
    spread_health = _classify_spread_health(spread_ratio)

    payload_diagnostics = {
        **payload_diagnostics,
        "requested_counts": requested_counts,
        "returned_counts": raw_counts,
        "closed_counts": closed_counts,
    }
    emit_diagnostics(
        {
            "stage": "snapshot_ready",
            "symbol": symbol_name,
            "broker_time_utc": cycle_utc.isoformat(),
            "cycle_time_utc": cycle_utc.isoformat(),
            "requested_bars": requested_counts,
            "returned_bars": raw_counts,
            "closed_bar_count": closed_counts,
            "payload_health": payload_health.value,
            "payload_diagnostics": payload_diagnostics,
        }
    )

    return MarketSnapshot(
        cycle_time_utc=cycle_utc,
        symbol=symbol_name,
        tick_bid=tick["bid"],
        tick_ask=tick["ask"],
        spread_points=spread_points,
        spread_ratio=spread_ratio,
        spread_health=spread_health,
        session=classify_session(cycle_utc),
        news=news_snapshot,
        contract=contract,
        feed_health=feed_health,
        latency_health=feed_health,
        bars_h1=tuple(_freeze_bars(bars_h1)),
        bars_m15=tuple(_freeze_bars(bars_m15)),
        bars_m5=tuple(_freeze_bars(bars_m5)),
        bars_m1=tuple(_freeze_bars(bars_m1)),
        indicator_bundle=indicator_bundle,
        payload_health=payload_health,
        payload_diagnostics=payload_diagnostics,
    )


def evaluate_payload_health(
    *,
    requested_counts: dict[str, int],
    raw_counts: dict[str, int],
) -> tuple[HealthState, dict[str, Any]]:
    minimum_counts = {
        "M1": ATR_PERIOD + ATR_BASELINE_PERIOD,
        "M5": MIN_BARS_BREAKOUT_M5,
        "M15": ATR_PERIOD + ATR_BASELINE_PERIOD,
        "H1": ATR_PERIOD + ATR_BASELINE_PERIOD,
    }
    missing: list[str] = []
    thin: list[str] = []
    details: dict[str, Any] = {}

    for timeframe, minimum in minimum_counts.items():
        requested = requested_counts[timeframe]
        available = raw_counts.get(timeframe, 0)
        details[timeframe] = {
            "requested": requested,
            "available": available,
            "minimum": minimum,
        }
        if available < minimum:
            missing.append(timeframe)
        elif available < requested:
            thin.append(timeframe)

    if missing:
        return (
            HealthState.RED,
            {
                "status": "RED",
                "policy": "STRICT_FAIL_LOUD",
                "missing_timeframes": tuple(missing),
                "thin_timeframes": tuple(thin),
                "details": details,
            },
        )
    if thin:
        return (
            HealthState.YELLOW,
            {
                "status": "YELLOW",
                "policy": "STRICT_FAIL_LOUD_WITH_THIN_ALLOWANCE",
                "missing_timeframes": (),
                "thin_timeframes": tuple(thin),
                "details": details,
            },
        )
    return (
        HealthState.GREEN,
        {
            "status": "GREEN",
            "policy": "STRICT_FAIL_LOUD_WITH_THIN_ALLOWANCE",
            "missing_timeframes": (),
            "thin_timeframes": (),
            "details": details,
        },
    )


def build_symbol_contract(*, symbol: str, raw_contract: Any) -> ContractSnapshot:
    point = float(_record_value(raw_contract, "point"))
    tick_size = float(_record_value(raw_contract, "trade_tick_size", default=point))
    tick_value = float(_record_value(raw_contract, "trade_tick_value", default=0.0) or 0.0)
    min_lot = float(_record_value(raw_contract, "volume_min"))
    max_lot = float(_record_value(raw_contract, "volume_max"))
    lot_step = float(_record_value(raw_contract, "volume_step"))
    stop_level = int(_record_value(raw_contract, "trade_stops_level", default=0) or 0)
    freeze_level = int(_record_value(raw_contract, "trade_freeze_level", default=0) or 0)

    if point <= 0.0:
        raise ConfigValidationError(f"Contract point must be positive for symbol {symbol}")
    if tick_size <= 0.0:
        raise ConfigValidationError(f"Contract tick_size must be positive for symbol {symbol}")
    if min_lot <= 0.0 or max_lot <= 0.0 or lot_step <= 0.0:
        raise ConfigValidationError(f"Contract lot bounds are invalid for symbol {symbol}")
    if min_lot > max_lot:
        raise ConfigValidationError(f"Contract volume_min exceeds volume_max for symbol {symbol}")

    return ContractSnapshot(
        symbol=symbol.upper(),
        point=point,
        tick_size=tick_size,
        tick_value=tick_value,
        min_lot=min_lot,
        max_lot=max_lot,
        lot_step=lot_step,
        stop_level_points=stop_level,
        freeze_level_points=freeze_level,
    )


def _normalize_tick(raw_tick: Any) -> dict[str, Any]:
    timestamp = _normalize_timestamp(_record_value(raw_tick, "time", alt_keys=("timestamp",)))
    bid = float(_record_value(raw_tick, "bid"))
    ask = float(_record_value(raw_tick, "ask"))
    if bid <= 0.0 or ask <= 0.0 or ask < bid:
        raise ConfigValidationError("Tick has impossible bid/ask values")
    return {"timestamp": timestamp, "bid": bid, "ask": ask}


def _validate_cycle_monotonicity(*, previous_cycle_time_utc: datetime | None, cycle_time_utc: datetime) -> None:
    if previous_cycle_time_utc is None:
        return
    previous_utc = _ensure_utc(previous_cycle_time_utc, field_name="previous_cycle_time_utc")
    if cycle_time_utc < previous_utc:
        raise ConfigValidationError("cycle_time_utc regressed below previous cycle timestamp")


def _normalize_rates(rates: Sequence[Any], *, timeframe: str) -> list[dict[str, Any]]:
    if not rates:
        raise ConfigValidationError(f"No rates available for timeframe {timeframe}")
    normalized: list[dict[str, Any]] = []
    for record in rates:
        timestamp = _normalize_timestamp(_record_value(record, "time", alt_keys=("timestamp",)))
        open_price = float(_record_value(record, "open"))
        high = float(_record_value(record, "high"))
        low = float(_record_value(record, "low"))
        close = float(_record_value(record, "close"))
        volume = float(_record_value(record, "tick_volume", alt_keys=("volume", "real_volume")))
        if min(open_price, high, low, close) <= 0.0:
            raise ConfigValidationError(f"Malformed OHLC values in {timeframe} rates")
        if high < max(open_price, close, low):
            raise ConfigValidationError(f"Inconsistent OHLC high in {timeframe} rates")
        if low > min(open_price, close, high):
            raise ConfigValidationError(f"Inconsistent OHLC low in {timeframe} rates")
        normalized.append(
            {
                "timestamp": timestamp,
                "close_time_utc": timestamp + timedelta(minutes=TIMEFRAME_MINUTES[timeframe]),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "tick_volume": volume,
                "timeframe": timeframe,
            }
        )
    return normalized


def _last_closed_bars(
    bars: Sequence[dict[str, Any]],
    *,
    timeframe: str,
    cycle_time_utc: datetime,
) -> list[dict[str, Any]]:
    closed = _closed_bars(bars, cycle_time_utc=cycle_time_utc)
    if not closed:
        raise ConfigValidationError(f"No closed bars available for timeframe {timeframe}")
    minimum = ATR_PERIOD + ATR_BASELINE_PERIOD
    if len(closed) < minimum:
        raise ConfigValidationError(
            f"Not enough closed bars for timeframe {timeframe}: need at least {minimum}"
    )
    return closed


def _closed_bars(
    bars: Sequence[dict[str, Any]],
    *,
    cycle_time_utc: datetime,
) -> list[dict[str, Any]]:
    return [bar for bar in bars if bar["close_time_utc"] <= cycle_time_utc]


def _classify_feed_health(
    *,
    tick: dict[str, Any],
    cycle_time_utc: datetime,
    bid: float,
    ask: float,
    point: float,
):
    from .enums import HealthState

    tick_age_seconds = max(0.0, (cycle_time_utc - tick["timestamp"]).total_seconds())
    if point <= 0.0:
        return HealthState.RED
    spread_points = (ask - bid) / point
    if spread_points < 0.0:
        return HealthState.RED
    if tick_age_seconds > 10.0:
        return HealthState.RED
    if tick_age_seconds > 5.0:
        return HealthState.YELLOW
    return HealthState.GREEN


def _classify_spread_health(spread_ratio: float):
    from .enums import HealthState

    if spread_ratio > 1.80:
        return HealthState.RED
    if spread_ratio > 1.20:
        return HealthState.YELLOW
    return HealthState.GREEN


def _build_indicator_bundle(
    *,
    bars_m1: Sequence[dict[str, Any]],
    bars_m5: Sequence[dict[str, Any]],
    bars_m15: Sequence[dict[str, Any]],
    bars_h1: Sequence[dict[str, Any]],
    spread_points: float,
) -> dict[str, Any]:
    m1_atr_values = _rolling_atr_values(bars_m1)
    m5_atr_values = _rolling_atr_values(bars_m5)
    m15_atr_values = _rolling_atr_values(bars_m15)
    h1_atr_values = _rolling_atr_values(bars_h1)
    return {
        "bar_anchor_m1_close_utc": bars_m1[-1]["close_time_utc"],
        "bar_anchor_m5_close_utc": bars_m5[-1]["close_time_utc"],
        "bar_anchor_m15_close_utc": bars_m15[-1]["close_time_utc"],
        "bar_anchor_h1_close_utc": bars_h1[-1]["close_time_utc"],
        "atr_m1": _atr(bars_m1),
        "atr_m1_base": _median_last(m1_atr_values, ATR_BASELINE_PERIOD),
        "atr_m5": _atr(bars_m5),
        "atr_m5_base": _median_last(m5_atr_values, ATR_BASELINE_PERIOD),
        "atr_m15_base": _median_last(m15_atr_values, ATR_BASELINE_PERIOD),
        "atr_h1_base": _median_last(h1_atr_values, ATR_BASELINE_PERIOD),
        "adx_m15": _adx(bars_m15),
        "adx_h1": _adx(bars_h1),
        "h1_slope": _slope_from_closes(bars_h1),
        "m15_slope": _slope_from_closes(bars_m15),
        "spread_points_baseline": max(spread_points, 1.0),
        "tick_volume_m1": float(bars_m1[-1]["tick_volume"]),
        "tick_volume_m1_base": float(
            median(bar["tick_volume"] for bar in bars_m1[-ATR_BASELINE_PERIOD:])
        ),
    }


def _rolling_atr_values(
    bars: Sequence[dict[str, Any]],
    *,
    period: int = ATR_PERIOD,
) -> list[float]:
    values: list[float] = []
    for end_index in range(period + 1, len(bars) + 1):
        values.append(_atr(bars[:end_index], period=period))
    return values


def _atr(bars: Sequence[dict[str, Any]], *, period: int = ATR_PERIOD) -> float:
    tr_values = _true_range_series(bars)
    if len(tr_values) < period:
        raise ConfigValidationError(f"Need at least {period + 1} bars for ATR")
    return sum(tr_values[-period:]) / period


def _true_range_series(bars: Sequence[dict[str, Any]]) -> list[float]:
    if len(bars) < 2:
        raise ConfigValidationError("Need at least 2 bars for true range")
    values: list[float] = []
    for idx in range(1, len(bars)):
        current = bars[idx]
        previous = bars[idx - 1]
        values.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            )
        )
    return values


def _adx(bars: Sequence[dict[str, Any]], *, period: int = ATR_PERIOD) -> float:
    if len(bars) < (period * 2) + 1:
        raise ConfigValidationError(f"Need at least {(period * 2) + 1} bars for ADX")
    tr_values: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for idx in range(1, len(bars)):
        current = bars[idx]
        previous = bars[idx - 1]
        up_move = current["high"] - previous["high"]
        down_move = previous["low"] - current["low"]
        plus_dm.append(up_move if up_move > down_move and up_move > 0.0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0.0 else 0.0)
        tr_values.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            )
        )

    tr_smoothed = sum(tr_values[:period])
    plus_smoothed = sum(plus_dm[:period])
    minus_smoothed = sum(minus_dm[:period])
    dx_values: list[float] = []
    for idx in range(period, len(tr_values)):
        tr_smoothed = tr_smoothed - (tr_smoothed / period) + tr_values[idx]
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
        raise ConfigValidationError(f"Not enough DX values for ADX with period {period}")
    adx = sum(dx_values[:period]) / period
    for value in dx_values[period:]:
        adx = ((adx * (period - 1)) + value) / period
    return adx


def _median_last(values: Sequence[float], count: int) -> float:
    if len(values) < count:
        raise ConfigValidationError(f"Need at least {count} values for median window")
    return float(median(values[-count:]))


def _slope_from_closes(bars: Sequence[dict[str, Any]], *, periods_back: int = 3) -> float:
    if len(bars) <= periods_back:
        raise ConfigValidationError(f"Need at least {periods_back + 1} bars for slope")
    return float(bars[-1]["close"] - bars[-(periods_back + 1)]["close"])


def _freeze_bars(bars: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    frozen: list[dict[str, Any]] = []
    for bar in bars:
        frozen.append(
            {
                "timestamp": bar["timestamp"],
                "close_time_utc": bar["close_time_utc"],
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "tick_volume": bar["tick_volume"],
                "timeframe": bar["timeframe"],
            }
        )
    return frozen


def _normalize_timestamp(raw_value: Any) -> datetime:
    if isinstance(raw_value, datetime):
        return _ensure_utc(raw_value, field_name="timestamp")
    return datetime.fromtimestamp(float(raw_value), tz=timezone.utc)


def _ensure_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ConfigValidationError(f"{field_name} must be timezone-aware UTC datetime")
    return value.astimezone(timezone.utc)


def _record_value(record: Any, key: str, *, alt_keys: Sequence[str] = (), default: Any = None) -> Any:
    if isinstance(record, dict):
        if key in record:
            return record[key]
        for alt_key in alt_keys:
            if alt_key in record:
                return record[alt_key]
        if default is not None:
            return default
        raise ConfigValidationError(f"Missing key '{key}' in market-data record")
    if hasattr(record, key):
        return getattr(record, key)
    for alt_key in alt_keys:
        if hasattr(record, alt_key):
            return getattr(record, alt_key)
    if default is not None:
        return default
    raise ConfigValidationError(f"Missing attribute '{key}' in market-data record")
