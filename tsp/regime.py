"""Regime classification engine for TSP V1."""

from __future__ import annotations

from dataclasses import dataclass

from .config import RegimeConfig
from .state import Direction, MarketSnapshot, Regime, RegimeResult


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


@dataclass(frozen=True, slots=True)
class _SlopeMetrics:
    h1_norm: float
    m15_norm: float
    slope_strength: float
    slope_agree: bool


@dataclass(frozen=True, slots=True)
class _CandidateDecision:
    result: RegimeResult | None
    fail_reason: str | None
    raw_scores: dict[str, float]


def _safe_ratio(numerator: float, denominator: float) -> float:
    if abs(denominator) < 1e-8:
        return 0.0
    return numerator / denominator


def _compute_slope_metrics(snap: MarketSnapshot, cfg: RegimeConfig) -> _SlopeMetrics:
    h1_norm = _safe_ratio(snap.h1_slope, snap.atr_h1_base)
    m15_norm = _safe_ratio(snap.m15_slope, snap.atr_m15_base)
    same_sign = (h1_norm > 0.0 and m15_norm > 0.0) or (h1_norm < 0.0 and m15_norm < 0.0)
    slope_agree = same_sign and abs(h1_norm) >= cfg.slope_agree_min and abs(m15_norm) >= cfg.slope_agree_min
    slope_strength = (abs(h1_norm) + abs(m15_norm)) / 2.0
    return _SlopeMetrics(
        h1_norm=h1_norm,
        m15_norm=m15_norm,
        slope_strength=slope_strength,
        slope_agree=slope_agree,
    )


def derive_direction_bias(snap: MarketSnapshot, cfg: RegimeConfig) -> Direction:
    metrics = _compute_slope_metrics(snap, cfg)
    if (
        metrics.h1_norm >= cfg.slope_bias_long_min
        and metrics.m15_norm >= cfg.slope_bias_long_min
    ):
        return Direction.LONG
    if (
        metrics.h1_norm <= -cfg.slope_bias_long_min
        and metrics.m15_norm <= -cfg.slope_bias_long_min
    ):
        return Direction.SHORT
    return Direction.FLAT


def _news_dead_result(
    snap: MarketSnapshot,
    cfg: RegimeConfig,
    direction_bias: Direction,
    metrics: _SlopeMetrics,
) -> RegimeResult | None:
    spread_ratio = _safe_ratio(snap.spread_current, snap.spread_baseline)
    atr_ratio_m1 = _safe_ratio(snap.atr_m1, snap.atr_m1_base)
    collapse_threshold = cfg.atr_collapse_asia if snap.session == "ASIA" else cfg.atr_collapse_other
    raw_scores = {
        "spread_ratio": spread_ratio,
        "atr_ratio_m1": atr_ratio_m1,
        "collapse_threshold": collapse_threshold,
        "h1_slope_norm": metrics.h1_norm,
        "m15_slope_norm": metrics.m15_norm,
    }
    if snap.is_news_window:
        return RegimeResult(Regime.NEWS_DEAD, 1.0, direction_bias, "", raw_scores)
    if snap.session == "DEAD":
        return RegimeResult(Regime.NEWS_DEAD, 0.95, direction_bias, "", raw_scores)
    if spread_ratio > cfg.dead_spread_ratio:
        confidence = _clamp(0.70 + min(0.30, (spread_ratio - cfg.dead_spread_ratio) / cfg.dead_spread_ratio))
        return RegimeResult(Regime.NEWS_DEAD, confidence, direction_bias, "", raw_scores)
    if atr_ratio_m1 < collapse_threshold:
        confidence = _clamp(0.65 + min(0.35, collapse_threshold - atr_ratio_m1))
        return RegimeResult(Regime.NEWS_DEAD, confidence, direction_bias, "", raw_scores)
    return None


def _trend_result(
    snap: MarketSnapshot,
    cfg: RegimeConfig,
    direction_bias: Direction,
    metrics: _SlopeMetrics,
) -> _CandidateDecision:
    adx_component = _clamp((snap.adx_h1 - (cfg.trend_adx_min - 6.0)) / 12.0)
    atr_component = _clamp((_safe_ratio(snap.atr_m5, snap.atr_m5_base) - 1.0) / 0.4)
    slope_component = _clamp(metrics.slope_strength / 0.35)
    composite = (adx_component + atr_component + slope_component) / 3.0
    raw_scores = {
        "adx_component": adx_component,
        "atr_component": atr_component,
        "slope_component": slope_component,
        "composite": composite,
        "adx_h1": snap.adx_h1,
        "adx_m15": snap.adx_m15,
        "h1_slope_norm": metrics.h1_norm,
        "m15_slope_norm": metrics.m15_norm,
    }
    raw_scores["strength_threshold"] = cfg.trend_strength_min
    raw_scores["adx_threshold"] = cfg.trend_adx_min
    raw_scores["htf_alignment"] = 1.0 if metrics.slope_agree else 0.0
    raw_scores["direction_bias_nonflat"] = 1.0 if direction_bias != Direction.FLAT else 0.0
    if not metrics.slope_agree:
        return _CandidateDecision(None, "htf_slope_conflict", raw_scores)
    if direction_bias == Direction.FLAT:
        return _CandidateDecision(None, "direction_bias_flat", raw_scores)
    if composite < cfg.trend_strength_min:
        return _CandidateDecision(None, "composite_below_threshold", raw_scores)
    if snap.adx_m15 < cfg.trend_adx_min:
        return _CandidateDecision(
            RegimeResult(
                regime=Regime.CHOP,
                confidence=_clamp(composite),
                direction_bias=Direction.FLAT,
                conflict_note="HTF_TREND_PENDING",
                raw_scores=raw_scores,
                diagnostics={"trend_fail_reason": "adx_m15_below_threshold"},
            ),
            "adx_m15_below_threshold",
            raw_scores,
        )
    confidence = _clamp(composite + (cfg.trend_adx_boost if snap.adx_h1 > cfg.trend_adx_min else 0.0))
    return _CandidateDecision(
        RegimeResult(
            Regime.TREND,
            confidence,
            direction_bias,
            "",
            raw_scores,
            diagnostics={"trend_fail_reason": ""},
        ),
        None,
        raw_scores,
    )


def _breakout_secondary_count(snap: MarketSnapshot, cfg: RegimeConfig, direction_bias: Direction) -> tuple[int, dict[str, float]]:
    atr_ratio_m5 = _safe_ratio(snap.atr_m5, snap.atr_m5_base)
    volume_ratio = _safe_ratio(snap.tick_vol_m1, snap.tick_vol_m1_base)
    compression_m5_ratio = _safe_ratio(snap.atr_m5_prev_window, snap.atr_m5_base)
    recent_direction_move = 0.0
    if len(snap.m1_closes_recent) >= 2:
        recent_direction_move = _safe_ratio(
            snap.m1_closes_recent[-1] - snap.m1_closes_recent[0],
            snap.atr_m1,
        )
    direction_emergence = abs(recent_direction_move)
    count = 0
    flags = {
        "m5_expanding": 1.0 if atr_ratio_m5 > cfg.breakout_m5_expansion_min else 0.0,
        "adx_in_range": 1.0 if cfg.breakout_adx_min <= snap.adx_m5 <= cfg.breakout_adx_max else 0.0,
        "m5_compression": 1.0 if compression_m5_ratio < 0.95 else 0.0,
        "volume_expansion": 1.0 if volume_ratio >= cfg.breakout_volume_expansion_min else 0.0,
        "direction_emergence": 1.0 if direction_emergence >= cfg.breakout_direction_emergence_min else 0.0,
        "atr_ratio_m5": atr_ratio_m5,
        "volume_ratio": volume_ratio,
        "compression_m5_ratio": compression_m5_ratio,
        "direction_emergence_value": direction_emergence,
        "direction_bias_nonflat": 1.0 if direction_bias != Direction.FLAT else 0.0,
    }
    for key in ("m5_expanding", "adx_in_range", "m5_compression", "volume_expansion", "direction_emergence"):
        count += int(flags[key])
    return count, flags


def _breakout_result(
    snap: MarketSnapshot,
    cfg: RegimeConfig,
    direction_bias: Direction,
    metrics: _SlopeMetrics,
) -> _CandidateDecision:
    compression_ratio = _safe_ratio(snap.atr_m1_prev_window, snap.atr_m1_base)
    atr_ratio_m1 = _safe_ratio(snap.atr_m1, snap.atr_m1_base)
    raw_scores = {
        "compression_ratio": compression_ratio,
        "compression_threshold": cfg.breakout_compression_max,
        "atr_ratio_m1": atr_ratio_m1,
        "burst_threshold": cfg.breakout_burst_min,
        "secondary_required": float(cfg.breakout_secondary_min_count),
    }
    if compression_ratio >= cfg.breakout_compression_max:
        return _CandidateDecision(None, "compression_above_threshold", raw_scores)
    if atr_ratio_m1 <= cfg.breakout_burst_min:
        return _CandidateDecision(None, "burst_below_threshold", raw_scores)
    secondary_count, secondary = _breakout_secondary_count(snap, cfg, direction_bias)
    raw_scores.update({"secondary_count": float(secondary_count), **secondary})
    if secondary_count < cfg.breakout_secondary_min_count:
        return _CandidateDecision(None, "secondary_confirmations_insufficient", raw_scores)
    confidence = _clamp(
        0.45
        + min(0.25, atr_ratio_m1 / 4.0)
        + min(0.20, (cfg.breakout_compression_max - compression_ratio) / cfg.breakout_compression_max)
        + min(0.10, secondary_count * 0.03)
    )
    raw_scores.update(
        {
            "h1_slope_norm": metrics.h1_norm,
            "m15_slope_norm": metrics.m15_norm,
        }
    )
    breakout_bias = direction_bias
    if breakout_bias == Direction.FLAT and len(snap.m1_closes_recent) >= 2:
        breakout_bias = Direction.LONG if snap.m1_closes_recent[-1] > snap.m1_closes_recent[0] else Direction.SHORT
    return _CandidateDecision(
        RegimeResult(
            Regime.BREAKOUT,
            confidence,
            breakout_bias,
            "",
            raw_scores,
            diagnostics={"breakout_fail_reason": ""},
        ),
        None,
        raw_scores,
    )


def _chop_result(snap: MarketSnapshot, cfg: RegimeConfig, direction_bias: Direction, metrics: _SlopeMetrics) -> RegimeResult:
    spread_ratio = _safe_ratio(snap.spread_current, snap.spread_baseline)
    atr_ratio_m1 = _safe_ratio(snap.atr_m1, snap.atr_m1_base)
    slope_weakness = 1.0 - _clamp(metrics.slope_strength / max(cfg.slope_agree_min * 3.0, 1e-8))
    atr_stagnation = 1.0 - _clamp(abs(atr_ratio_m1 - 1.0))
    spread_drag = _clamp(spread_ratio / max(cfg.dead_spread_ratio, 1e-8))
    confidence = _clamp((slope_weakness + atr_stagnation + spread_drag) / 3.0)
    return RegimeResult(
        regime=Regime.CHOP,
        confidence=confidence,
        direction_bias=Direction.FLAT if not metrics.slope_agree else direction_bias,
        conflict_note="",
        raw_scores={
            "spread_ratio": spread_ratio,
            "atr_ratio_m1": atr_ratio_m1,
            "slope_weakness": slope_weakness,
            "atr_stagnation": atr_stagnation,
            "spread_drag": spread_drag,
        },
    )


def classify_regime(snap: MarketSnapshot, cfg: RegimeConfig) -> RegimeResult:
    metrics = _compute_slope_metrics(snap, cfg)
    direction_bias = derive_direction_bias(snap, cfg)

    dead = _news_dead_result(snap, cfg, direction_bias, metrics)
    if dead is not None:
        return dead

    trend = _trend_result(snap, cfg, direction_bias, metrics)
    breakout = _breakout_result(snap, cfg, direction_bias, metrics)
    diagnostics = {
        "trend_candidate": str(bool(trend.result is not None and trend.result.regime == Regime.TREND)),
        "trend_direction_bias": direction_bias.name,
        "trend_fail_reason": trend.fail_reason or "",
        "breakout_candidate": str(bool(breakout.result is not None and breakout.result.regime == Regime.BREAKOUT)),
        "breakout_direction": (
            breakout.result.direction_bias.name
            if breakout.result is not None and breakout.result.direction_bias is not None
            else direction_bias.name
        ),
        "breakout_fail_reason": breakout.fail_reason or "",
    }
    candidate_scores = {
        "trend_composite": trend.raw_scores.get("composite", 0.0),
        "trend_strength_threshold": trend.raw_scores.get("strength_threshold", cfg.trend_strength_min),
        "trend_adx_threshold": trend.raw_scores.get("adx_threshold", cfg.trend_adx_min),
        "trend_adx_h1": trend.raw_scores.get("adx_h1", snap.adx_h1),
        "trend_adx_m15": trend.raw_scores.get("adx_m15", snap.adx_m15),
        "trend_h1_slope_norm": trend.raw_scores.get("h1_slope_norm", metrics.h1_norm),
        "trend_m15_slope_norm": trend.raw_scores.get("m15_slope_norm", metrics.m15_norm),
        "trend_htf_alignment": trend.raw_scores.get("htf_alignment", 0.0),
        "breakout_compression_ratio": breakout.raw_scores.get("compression_ratio", 0.0),
        "breakout_compression_threshold": breakout.raw_scores.get(
            "compression_threshold",
            cfg.breakout_compression_max,
        ),
        "breakout_atr_ratio_m1": breakout.raw_scores.get("atr_ratio_m1", 0.0),
        "breakout_burst_threshold": breakout.raw_scores.get("burst_threshold", cfg.breakout_burst_min),
        "breakout_secondary_count": breakout.raw_scores.get("secondary_count", 0.0),
        "breakout_secondary_required": breakout.raw_scores.get(
            "secondary_required",
            float(cfg.breakout_secondary_min_count),
        ),
    }
    if trend.result is not None and trend.result.regime == Regime.TREND:
        if breakout.result is not None and breakout.result.regime == Regime.BREAKOUT:
            return RegimeResult(
                regime=trend.result.regime,
                confidence=trend.result.confidence,
                direction_bias=trend.result.direction_bias,
                conflict_note="TREND_WITH_BO_CONFIRM",
                raw_scores={
                    **candidate_scores,
                    **dict(trend.result.raw_scores),
                    "breakout_confirm": 1.0,
                },
                diagnostics=diagnostics,
            )
        return RegimeResult(
            regime=trend.result.regime,
            confidence=trend.result.confidence,
            direction_bias=trend.result.direction_bias,
            conflict_note=trend.result.conflict_note,
            raw_scores={**candidate_scores, **dict(trend.result.raw_scores)},
            diagnostics=diagnostics,
        )
    if trend.result is not None:
        return RegimeResult(
            regime=trend.result.regime,
            confidence=trend.result.confidence,
            direction_bias=trend.result.direction_bias,
            conflict_note=trend.result.conflict_note,
            raw_scores={**candidate_scores, **dict(trend.result.raw_scores)},
            diagnostics=diagnostics,
        )
    if breakout.result is not None:
        return RegimeResult(
            regime=breakout.result.regime,
            confidence=breakout.result.confidence,
            direction_bias=breakout.result.direction_bias,
            conflict_note=breakout.result.conflict_note,
            raw_scores={**candidate_scores, **dict(breakout.result.raw_scores)},
            diagnostics=diagnostics,
        )
    chop = _chop_result(snap, cfg, direction_bias, metrics)
    return RegimeResult(
        regime=chop.regime,
        confidence=chop.confidence,
        direction_bias=chop.direction_bias,
        conflict_note=chop.conflict_note,
        raw_scores={**candidate_scores, **dict(chop.raw_scores)},
        diagnostics=diagnostics,
    )


__all__ = ["classify_regime", "derive_direction_bias"]
