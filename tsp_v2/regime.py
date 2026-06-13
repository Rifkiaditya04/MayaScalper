"""Regime classification engine for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from .enums import Direction, HealthState, RegimeName, SessionName
from .models import MarketSnapshot, RegimeDecision


EMA_PERIOD = 20
ATR_PERIOD = 14
TREND_H1_LOOKBACK = 5
TREND_M15_LOOKBACK = 8
TREND_DIRECTION_THRESHOLD = 0.35
TREND_H1_ADX_MIN = 20.0
TREND_M15_ADX_MIN = 22.0
TREND_COMPOSITE_THRESHOLD = 0.65

BREAKOUT_M5_COMPRESSION_WINDOW = 50
BREAKOUT_COMPRESSION_MAX = 0.75
BREAKOUT_BURST_MIN = 1.45
BREAKOUT_RECENT_RANGE_BARS = 10
BREAKOUT_PARTICIPATION_MIN = 2

MICRO_IMPULSE_MIN_BODY_ATR = 0.8
MICRO_EXPIRY_SECONDS = 90.0
MICRO_ALLOWED_SESSIONS = frozenset(
    {SessionName.LONDON, SessionName.LONDON_NY, SessionName.EARLY_NY}
)
MICRO_SPREAD_YELLOW_MAX = 1.75


@dataclass(frozen=True, slots=True)
class _TrendMetrics:
    h1_slope_norm: float
    m15_slope_norm: float
    h1_bias: Direction
    m15_bias: Direction
    adx_h1: float
    adx_m15: float


@dataclass(frozen=True, slots=True)
class _Candidate:
    regime: RegimeName | None
    confidence: float
    direction_bias: Direction
    fail_reason: str
    raw_scores: dict[str, float]


def classify_regime(snapshot: MarketSnapshot) -> RegimeDecision:
    trend_metrics = _compute_trend_metrics(snapshot)
    news_candidate = _news_lockout_candidate(snapshot)
    if news_candidate.regime is RegimeName.NEWS_LOCKOUT:
        return _build_result(news_candidate, diagnostics={})

    trend_candidate = _trend_candidate(snapshot, trend_metrics)
    if trend_candidate.regime is RegimeName.TREND:
        return _build_result(
            trend_candidate,
            diagnostics={
                "trend_fail_reason": "",
                "breakout_fail_reason": "",
                "micro_fail_reason": "",
                "priority_resolution": "TREND",
            },
        )

    breakout_candidate = _breakout_candidate(snapshot)
    if breakout_candidate.regime is RegimeName.BREAKOUT:
        return _build_result(
            breakout_candidate,
            diagnostics={
                "trend_fail_reason": trend_candidate.fail_reason,
                "breakout_fail_reason": "",
                "micro_fail_reason": "",
                "priority_resolution": "BREAKOUT",
            },
        )

    micro_candidate = _micro_candidate(snapshot, trend_metrics)
    if micro_candidate.regime is RegimeName.MICRO_MOMENTUM:
        return _build_result(
            micro_candidate,
            diagnostics={
                "trend_fail_reason": trend_candidate.fail_reason,
                "breakout_fail_reason": breakout_candidate.fail_reason,
                "micro_fail_reason": "",
                "priority_resolution": "MICRO_MOMENTUM",
            },
        )

    return RegimeDecision(
        regime=RegimeName.CHOP,
        confidence=_chop_confidence(snapshot, trend_candidate, breakout_candidate, micro_candidate),
        direction_bias=Direction.FLAT,
        raw_scores={
            "trend_confidence_candidate": trend_candidate.confidence,
            "breakout_confidence_candidate": breakout_candidate.confidence,
            "micro_confidence_candidate": micro_candidate.confidence,
        },
        diagnostics={
            "trend_fail_reason": trend_candidate.fail_reason,
            "breakout_fail_reason": breakout_candidate.fail_reason,
            "micro_fail_reason": micro_candidate.fail_reason,
            "priority_resolution": "CHOP",
        },
    )


def _news_lockout_candidate(snapshot: MarketSnapshot) -> _Candidate:
    if snapshot.news.lockout_active:
        return _Candidate(
            regime=RegimeName.NEWS_LOCKOUT,
            confidence=1.0,
            direction_bias=Direction.FLAT,
            fail_reason="",
            raw_scores={
                "news_lockout_active": 1.0,
                "relevant_events": float(len(snapshot.news.relevant_events)),
            },
        )
    return _Candidate(None, 0.0, Direction.FLAT, "news_clear", {"news_lockout_active": 0.0})


def _trend_candidate(snapshot: MarketSnapshot, metrics: _TrendMetrics) -> _Candidate:
    raw_scores = {
        "trend_h1_slope_norm": metrics.h1_slope_norm,
        "trend_m15_slope_norm": metrics.m15_slope_norm,
        "trend_adx_h1": metrics.adx_h1,
        "trend_adx_m15": metrics.adx_m15,
        "trend_h1_alignment": 1.0 if metrics.h1_bias is not Direction.FLAT else 0.0,
        "trend_m15_alignment": 1.0 if metrics.m15_bias is not Direction.FLAT else 0.0,
    }
    if metrics.h1_bias is Direction.FLAT or metrics.m15_bias is Direction.FLAT or metrics.h1_bias is not metrics.m15_bias:
        return _Candidate(None, 0.0, Direction.FLAT, "htf_conflict", raw_scores)
    if metrics.adx_h1 < TREND_H1_ADX_MIN:
        return _Candidate(None, 0.0, metrics.h1_bias, "adx_h1_below_threshold", raw_scores)
    if metrics.adx_m15 < TREND_M15_ADX_MIN:
        return _Candidate(None, 0.0, metrics.h1_bias, "adx_m15_below_threshold", raw_scores)

    h1_strength = _clamp(abs(metrics.h1_slope_norm) / 0.70)
    m15_strength = _clamp(abs(metrics.m15_slope_norm) / 0.70)
    adx_participation = (
        _clamp((metrics.adx_h1 - TREND_H1_ADX_MIN) / 10.0)
        + _clamp((metrics.adx_m15 - TREND_M15_ADX_MIN) / 10.0)
    ) / 2.0
    composite = (0.35 * 1.0) + (0.25 * h1_strength) + (0.25 * m15_strength) + (0.15 * adx_participation)
    raw_scores.update(
        {
            "trend_h1_strength": h1_strength,
            "trend_m15_strength": m15_strength,
            "trend_adx_participation": adx_participation,
            "trend_composite": composite,
        }
    )
    if composite < TREND_COMPOSITE_THRESHOLD:
        return _Candidate(None, composite, metrics.h1_bias, "composite_below_threshold", raw_scores)

    return _Candidate(RegimeName.TREND, _clamp(composite), metrics.h1_bias, "", raw_scores)


def _breakout_candidate(snapshot: MarketSnapshot) -> _Candidate:
    bars_m5 = list(snapshot.bars_m5)
    bars_m1 = list(snapshot.bars_m1)
    raw_scores: dict[str, float] = {}
    if len(bars_m5) < BREAKOUT_M5_COMPRESSION_WINDOW + EMA_PERIOD:
        return _Candidate(None, 0.0, Direction.FLAT, "insufficient_m5_history", raw_scores)

    current_atr_m5 = _atr(bars_m5[-(ATR_PERIOD + 1) :])
    rolling_m5_atrs = _rolling_atr_values(bars_m5, period=ATR_PERIOD)
    compression_sample = rolling_m5_atrs[-(BREAKOUT_M5_COMPRESSION_WINDOW + 1) : -1]
    compression_baseline = float(median(compression_sample))
    compression_ratio = _safe_ratio(min(compression_sample), compression_baseline)

    current_atr_m1 = snapshot.indicator_bundle["atr_m1"]
    atr_m1_base = snapshot.indicator_bundle["atr_m1_base"]
    burst_ratio = _safe_ratio(current_atr_m1, atr_m1_base)

    recent_range = bars_m5[-BREAKOUT_RECENT_RANGE_BARS:]
    previous_range = recent_range[:-1]
    recent_high = max(bar["high"] for bar in previous_range)
    recent_low = min(bar["low"] for bar in previous_range)
    close_price = recent_range[-1]["close"]
    breakout_direction = Direction.FLAT
    if close_price > recent_high:
        breakout_direction = Direction.LONG
    elif close_price < recent_low:
        breakout_direction = Direction.SHORT

    raw_scores.update(
        {
            "breakout_compression_ratio": compression_ratio,
            "breakout_burst_ratio": burst_ratio,
            "breakout_recent_high": recent_high,
            "breakout_recent_low": recent_low,
            "breakout_close_price": close_price,
        }
    )
    if compression_ratio > BREAKOUT_COMPRESSION_MAX:
        return _Candidate(None, 0.0, breakout_direction, "compression_above_threshold", raw_scores)
    if burst_ratio < BREAKOUT_BURST_MIN:
        return _Candidate(None, 0.0, breakout_direction, "burst_below_threshold", raw_scores)
    if breakout_direction is Direction.FLAT:
        return _Candidate(None, 0.0, Direction.FLAT, "range_not_broken", raw_scores)

    participation_count, participation_scores = _breakout_participation(
        bars_m5=bars_m5,
        bars_m1=bars_m1,
        direction=breakout_direction,
        atr_m5=current_atr_m5,
        burst_ratio=burst_ratio,
    )
    raw_scores.update(participation_scores)
    raw_scores["breakout_participation_count"] = float(participation_count)
    if participation_count < BREAKOUT_PARTICIPATION_MIN:
        return _Candidate(None, 0.0, breakout_direction, "participation_insufficient", raw_scores)

    compression_score = _clamp((BREAKOUT_COMPRESSION_MAX - compression_ratio) / BREAKOUT_COMPRESSION_MAX)
    burst_score = _clamp((burst_ratio - BREAKOUT_BURST_MIN) / 0.75)
    participation_score = _clamp(participation_count / 4.0)
    confidence = _clamp((compression_score * 0.35) + (burst_score * 0.35) + (participation_score * 0.30))
    raw_scores.update(
        {
            "breakout_compression_score": compression_score,
            "breakout_burst_score": burst_score,
            "breakout_participation_score": participation_score,
            "breakout_confidence": confidence,
        }
    )
    return _Candidate(RegimeName.BREAKOUT, confidence, breakout_direction, "", raw_scores)


def _micro_candidate(snapshot: MarketSnapshot, metrics: _TrendMetrics) -> _Candidate:
    bars_m5 = list(snapshot.bars_m5)
    bars_m1 = list(snapshot.bars_m1)
    current_m5 = bars_m5[-1]
    current_m1 = bars_m1[-1]
    micro_direction = Direction.LONG if current_m5["close"] > current_m5["open"] else Direction.SHORT
    body_size = abs(current_m5["close"] - current_m5["open"])
    atr_m5 = snapshot.indicator_bundle["atr_m5"]
    impulse_ratio = _safe_ratio(body_size, atr_m5)
    spread_ok = snapshot.spread_ratio <= MICRO_SPREAD_YELLOW_MAX
    session_ok = snapshot.session in MICRO_ALLOWED_SESSIONS
    age_seconds = max(0.0, (snapshot.cycle_time_utc - current_m1["close_time_utc"]).total_seconds())
    continuation_ok = _m1_continuation(bars_m1, micro_direction)
    severe_htf_contradiction = (
        (metrics.h1_bias is Direction.LONG and micro_direction is Direction.SHORT)
        or (metrics.h1_bias is Direction.SHORT and micro_direction is Direction.LONG)
    ) and (
        (metrics.m15_bias is Direction.LONG and micro_direction is Direction.SHORT)
        or (metrics.m15_bias is Direction.SHORT and micro_direction is Direction.LONG)
    )

    raw_scores = {
        "micro_impulse_ratio": impulse_ratio,
        "micro_spread_ratio": snapshot.spread_ratio,
        "micro_latency_green": 1.0 if snapshot.latency_health is HealthState.GREEN else 0.0,
        "micro_session_ok": 1.0 if session_ok else 0.0,
        "micro_continuation_ok": 1.0 if continuation_ok else 0.0,
        "micro_age_seconds": age_seconds,
        "micro_htf_contradiction": 1.0 if severe_htf_contradiction else 0.0,
    }
    if impulse_ratio < MICRO_IMPULSE_MIN_BODY_ATR:
        return _Candidate(None, 0.0, micro_direction, "weak_impulse", raw_scores)
    if not continuation_ok:
        return _Candidate(None, 0.0, micro_direction, "no_m1_confirmation", raw_scores)
    if not spread_ok:
        return _Candidate(None, 0.0, micro_direction, "spread_degraded", raw_scores)
    if snapshot.latency_health is not HealthState.GREEN:
        return _Candidate(None, 0.0, micro_direction, "latency_not_green", raw_scores)
    if not session_ok:
        return _Candidate(None, 0.0, micro_direction, "session_restricted", raw_scores)
    if age_seconds > MICRO_EXPIRY_SECONDS:
        return _Candidate(None, 0.0, micro_direction, "expired", raw_scores)
    if severe_htf_contradiction:
        return _Candidate(None, 0.0, micro_direction, "severe_htf_contradiction", raw_scores)

    impulse_score = _clamp((impulse_ratio - MICRO_IMPULSE_MIN_BODY_ATR) / 0.7)
    confidence = _clamp((0.45 * impulse_score) + (0.35 * 1.0) + (0.20 * 1.0))
    raw_scores["micro_confidence"] = confidence
    return _Candidate(RegimeName.MICRO_MOMENTUM, confidence, micro_direction, "", raw_scores)


def _compute_trend_metrics(snapshot: MarketSnapshot) -> _TrendMetrics:
    bars_h1 = list(snapshot.bars_h1)
    bars_m15 = list(snapshot.bars_m15)
    h1_ema_series = _ema_series([bar["close"] for bar in bars_h1], EMA_PERIOD)
    m15_ema_series = _ema_series([bar["close"] for bar in bars_m15], EMA_PERIOD)
    h1_slope_norm = _safe_ratio(
        h1_ema_series[-1] - h1_ema_series[-(TREND_H1_LOOKBACK + 1)],
        snapshot.indicator_bundle["atr_h1_base"],
    )
    m15_slope_norm = _safe_ratio(
        m15_ema_series[-1] - m15_ema_series[-(TREND_M15_LOOKBACK + 1)],
        snapshot.indicator_bundle["atr_m15_base"],
    )
    return _TrendMetrics(
        h1_slope_norm=h1_slope_norm,
        m15_slope_norm=m15_slope_norm,
        h1_bias=_bias_from_slope(h1_slope_norm),
        m15_bias=_bias_from_slope(m15_slope_norm),
        adx_h1=float(snapshot.indicator_bundle["adx_h1"]),
        adx_m15=float(snapshot.indicator_bundle["adx_m15"]),
    )


def _breakout_participation(
    *,
    bars_m5: list[dict[str, float]],
    bars_m1: list[dict[str, float]],
    direction: Direction,
    atr_m5: float,
    burst_ratio: float,
) -> tuple[int, dict[str, float]]:
    current_m5 = bars_m5[-1]
    current_m1 = bars_m1[-1]
    m5_body = abs(current_m5["close"] - current_m5["open"])
    m5_range = max(1e-8, current_m5["high"] - current_m5["low"])
    body_dominance = _safe_ratio(m5_body, m5_range)
    wick_rejection = _wick_rejection_quality(current_m5, direction)
    volume_ratio = _safe_ratio(float(current_m1["tick_volume"]), float(median(bar["tick_volume"] for bar in bars_m1[-20:])))
    continuation = 1.0 if _m1_continuation(bars_m1, direction) else 0.0

    flags = {
        "breakout_volume_expansion": 1.0 if volume_ratio >= 1.20 else 0.0,
        "breakout_body_dominance": 1.0 if body_dominance >= 0.60 else 0.0,
        "breakout_wick_rejection": 1.0 if wick_rejection >= 0.55 else 0.0,
        "breakout_m1_continuation": continuation,
        "breakout_volume_ratio": volume_ratio,
        "breakout_body_ratio": body_dominance,
        "breakout_wick_quality": wick_rejection,
        "breakout_m5_body_atr": _safe_ratio(m5_body, atr_m5),
        "breakout_burst_ratio_confirm": burst_ratio,
    }
    count = int(flags["breakout_volume_expansion"]) + int(flags["breakout_body_dominance"]) + int(
        flags["breakout_wick_rejection"]
    ) + int(flags["breakout_m1_continuation"])
    return count, flags


def _m1_continuation(bars_m1: list[dict[str, float]], direction: Direction) -> bool:
    if len(bars_m1) < 2:
        return False
    previous = bars_m1[-2]["close"]
    current = bars_m1[-1]["close"]
    if direction is Direction.LONG:
        return current > previous
    if direction is Direction.SHORT:
        return current < previous
    return False


def _wick_rejection_quality(bar: dict[str, float], direction: Direction) -> float:
    high = bar["high"]
    low = bar["low"]
    open_price = bar["open"]
    close = bar["close"]
    total_range = max(1e-8, high - low)
    if direction is Direction.LONG:
        lower_wick = min(open_price, close) - low
        return _clamp(lower_wick / total_range)
    upper_wick = high - max(open_price, close)
    return _clamp(upper_wick / total_range)


def _bias_from_slope(slope_norm: float) -> Direction:
    if slope_norm > TREND_DIRECTION_THRESHOLD:
        return Direction.LONG
    if slope_norm < -TREND_DIRECTION_THRESHOLD:
        return Direction.SHORT
    return Direction.FLAT


def _ema_series(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        raise ValueError(f"Need at least {period} values for EMA")
    multiplier = 2.0 / (period + 1.0)
    ema_values = [sum(values[:period]) / period]
    for value in values[period:]:
        ema_values.append((value - ema_values[-1]) * multiplier + ema_values[-1])
    return ema_values


def _rolling_atr_values(bars: list[dict[str, float]], *, period: int) -> list[float]:
    values: list[float] = []
    for end_index in range(period + 1, len(bars) + 1):
        values.append(_atr(bars[:end_index]))
    return values


def _atr(bars: list[dict[str, float]], *, period: int = 14) -> float:
    if len(bars) < period + 1:
        raise ValueError(f"Need at least {period + 1} bars for ATR")
    true_ranges: list[float] = []
    for idx in range(1, len(bars)):
        current = bars[idx]
        previous = bars[idx - 1]
        true_ranges.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            )
        )
    return sum(true_ranges[-period:]) / period


def _build_result(candidate: _Candidate, *, diagnostics: dict[str, str]) -> RegimeDecision:
    assert candidate.regime is not None
    return RegimeDecision(
        regime=candidate.regime,
        confidence=_clamp(candidate.confidence),
        direction_bias=candidate.direction_bias,
        raw_scores=candidate.raw_scores,
        diagnostics=diagnostics,
    )


def _chop_confidence(
    snapshot: MarketSnapshot,
    trend_candidate: _Candidate,
    breakout_candidate: _Candidate,
    micro_candidate: _Candidate,
) -> float:
    drag = _clamp(snapshot.spread_ratio / 2.0)
    residual = 1.0 - max(
        trend_candidate.confidence,
        breakout_candidate.confidence,
        micro_candidate.confidence,
    )
    return _clamp((drag + residual) / 2.0)


def _safe_ratio(numerator: float, denominator: float) -> float:
    if abs(denominator) < 1e-8:
        return 0.0
    return numerator / denominator


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))
