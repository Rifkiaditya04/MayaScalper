"""Signal scoring engine for TSP V1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from .config import SignalConfig
from .state import (
    AggressionState,
    ConfidenceTier,
    Direction,
    MarketSnapshot,
    Module,
    Regime,
    RegimeResult,
    RuntimeState,
    SignalScore,
)


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _safe_ratio(numerator: float, denominator: float) -> float:
    if abs(denominator) < 1e-8:
        return 0.0
    return numerator / denominator


@dataclass(frozen=True, slots=True)
class _ScoreParts:
    direction: Direction
    body_score: float
    wick_score: float
    atr_expansion: float
    session_bonus: float
    spread_penalty: float
    htf_alignment: float
    momentum_score: float
    volume_score: float
    entry_hint: float
    invalidation_anchor: float
    setup_metadata: dict[str, float | str | bool]

    @property
    def total(self) -> float:
        return (
            self.body_score
            + self.wick_score
            + self.atr_expansion
            + self.session_bonus
            + self.htf_alignment
            + self.momentum_score
            + self.volume_score
            + self.spread_penalty
        )


def _body_score(snap: MarketSnapshot, direction: Direction) -> float:
    candle = snap.m1
    body = abs(candle.close - candle.open)
    body_ratio = _safe_ratio(body, candle.high - candle.low)
    body_norm = _clamp((body_ratio - 0.45) / (0.85 - 0.45))
    if direction == Direction.LONG:
        close_quality = _clamp(_safe_ratio(candle.close - candle.low, candle.high - candle.low))
    elif direction == Direction.SHORT:
        close_quality = _clamp(_safe_ratio(candle.high - candle.close, candle.high - candle.low))
    else:
        close_quality = 0.0
    return body_norm * close_quality * 20.0


def _module_a_wick_score(snap: MarketSnapshot, direction: Direction) -> float:
    candle = snap.m1
    range_size = max(candle.high - candle.low, 1e-8)
    rejection_wick = (candle.high - candle.close) if direction == Direction.LONG else (candle.close - candle.low)
    rejection_ratio = _safe_ratio(rejection_wick, range_size)
    return _clamp(1.0 - _safe_ratio(rejection_ratio, 0.25)) * 15.0


def _module_b_wick_score(snap: MarketSnapshot, direction: Direction) -> float:
    candle = snap.m1
    midpoint = (candle.high + candle.low) / 2.0
    range_size = max(candle.high - candle.low, 1e-8)
    if direction == Direction.LONG:
        displacement = _safe_ratio(candle.close - midpoint, range_size / 2.0)
    else:
        displacement = _safe_ratio(midpoint - candle.close, range_size / 2.0)
    return _clamp((displacement - 0.50) / 0.50) * 15.0


def _atr_expansion_score(snap: MarketSnapshot, module: Module, boost_multiplier: float) -> float:
    atr_ratio = _safe_ratio(snap.atr_m1, snap.atr_m1_base)
    base_score = _clamp((atr_ratio - 1.0) / 0.75) * 20.0
    if module == Module.BREAKOUT_MOMENTUM:
        return min(20.0, base_score * boost_multiplier)
    return base_score


def _session_bonus(session: str) -> float:
    if session == "OVERLAP":
        return 10.0
    if session in {"LONDON", "NY"}:
        return 7.0
    if session == "ASIA":
        return 3.0
    return 0.0


def _spread_penalty(snap: MarketSnapshot, ratio_start: float) -> float:
    spread_ratio = _safe_ratio(snap.spread_current, snap.spread_baseline)
    if spread_ratio <= ratio_start:
        return 0.0
    penalty_norm = _clamp((spread_ratio - ratio_start) / (2.5 - ratio_start))
    return -(penalty_norm * 20.0)


def _momentum_score(snap: MarketSnapshot, direction: Direction, lookback_bars: int, roc_min_atr_fraction: float) -> float:
    closes = snap.m1_closes_recent
    if len(closes) <= lookback_bars:
        return 0.0
    roc_norm = _safe_ratio(closes[-1] - closes[-(lookback_bars + 1)], snap.atr_m1)
    directional_roc = roc_norm if direction == Direction.LONG else -roc_norm
    if directional_roc < roc_min_atr_fraction:
        return 0.0
    return _clamp((directional_roc - roc_min_atr_fraction) / 0.8) * 15.0


def _module_a_pullback_valid(snap: MarketSnapshot, direction: Direction, cfg: SignalConfig) -> tuple[bool, float]:
    if direction == Direction.LONG:
        retrace = snap.swing_high_m5 - snap.m1.close
    elif direction == Direction.SHORT:
        retrace = snap.m1.close - snap.swing_low_m5
    else:
        return False, 0.0
    depth_atr = _safe_ratio(retrace, snap.atr_m1)
    valid = cfg.pullback_min_depth_atr <= depth_atr <= cfg.pullback_max_depth_atr
    return valid, depth_atr


def _confidence_tier(score: float) -> ConfidenceTier:
    if score > 80.0:
        return ConfidenceTier.ELITE
    if score >= 65.0:
        return ConfidenceTier.GOOD
    if score >= 50.0:
        return ConfidenceTier.NORMAL
    return ConfidenceTier.WEAK


def _dynamic_threshold(regime: RegimeResult, aggression: AggressionState, cfg: SignalConfig) -> float:
    if regime.regime == Regime.TREND:
        base = cfg.threshold_trend
    elif regime.regime == Regime.BREAKOUT:
        base = cfg.threshold_breakout
    else:
        base = cfg.threshold_chop
    aggression_adj = {
        AggressionState.AGGRESSIVE: cfg.aggression_adj_aggressive,
        AggressionState.NORMAL: cfg.aggression_adj_normal,
        AggressionState.DEFENSIVE: cfg.aggression_adj_defensive,
    }[aggression]
    return base + aggression_adj + ((1.0 - regime.confidence) * cfg.confidence_penalty_weight)


def _is_stale(candidate: SignalScore, runtime: RuntimeState | None, cfg: SignalConfig) -> bool:
    if runtime is None or runtime.last_signal is None:
        return False
    previous = runtime.last_signal
    if previous.module != candidate.module or previous.direction != candidate.direction:
        return False
    if runtime.last_signal_age_bars > cfg.stale_bars:
        return False
    return (candidate.score - previous.score) < cfg.stale_score_improvement_min


def _setup_id(symbol: str, timestamp: datetime, module: Module, direction: Direction, entry: float) -> str:
    raw = f"{symbol}|{timestamp.isoformat()}|{module.name}|{direction.name}|{round(entry, 2)}"
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def _module_a_parts(snap: MarketSnapshot, regime: RegimeResult, cfg: SignalConfig) -> _ScoreParts | None:
    direction = regime.direction_bias
    valid_pullback, depth_atr = _module_a_pullback_valid(snap, direction, cfg)
    if direction == Direction.FLAT or not valid_pullback:
        return None
    body_score = _body_score(snap, direction)
    wick_score = _module_a_wick_score(snap, direction)
    atr_expansion = _atr_expansion_score(snap, Module.PULLBACK_CONTINUATION, cfg.breakout_atr_boost_multiplier)
    htf_alignment = 15.0 if regime.direction_bias == direction else 0.0
    momentum = _momentum_score(snap, direction, cfg.roc_lookback_bars, cfg.roc_min_atr_fraction)
    session_bonus = _session_bonus(snap.session)
    spread_penalty = _spread_penalty(snap, cfg.spread_penalty_ratio_start)
    invalidation_anchor = snap.swing_low_m5 if direction == Direction.LONG else snap.swing_high_m5
    return _ScoreParts(
        direction=direction,
        body_score=body_score,
        wick_score=wick_score,
        atr_expansion=atr_expansion,
        session_bonus=session_bonus,
        spread_penalty=spread_penalty,
        htf_alignment=htf_alignment,
        momentum_score=momentum,
        volume_score=0.0,
        entry_hint=snap.ask if direction == Direction.LONG else snap.bid,
        invalidation_anchor=invalidation_anchor,
        setup_metadata={
            "depth_atr": depth_atr,
            "regime_confidence": regime.confidence,
            "session": snap.session,
        },
    )


def _module_b_parts(snap: MarketSnapshot, regime: RegimeResult, cfg: SignalConfig) -> _ScoreParts | None:
    direction = regime.direction_bias
    if direction == Direction.FLAT:
        direction = Direction.LONG if snap.m1.close >= snap.m1.open else Direction.SHORT
    body_score = _body_score(snap, direction)
    wick_score = _module_b_wick_score(snap, direction)
    atr_expansion = _atr_expansion_score(snap, Module.BREAKOUT_MOMENTUM, cfg.breakout_atr_boost_multiplier)
    if regime.direction_bias == Direction.FLAT:
        htf_alignment = regime.confidence * 8.0
    elif regime.direction_bias == direction:
        htf_alignment = 15.0
    else:
        htf_alignment = 0.0
    momentum = _momentum_score(snap, direction, cfg.roc_lookback_bars, cfg.roc_min_atr_fraction)
    session_bonus = _session_bonus(snap.session)
    spread_penalty = _spread_penalty(snap, cfg.spread_penalty_ratio_start)
    volume_ratio = _safe_ratio(snap.tick_vol_m1, snap.tick_vol_m1_base)
    volume_score = _clamp((volume_ratio - 1.0) / 0.5) * 5.0
    invalidation_anchor = snap.swing_low_m5 if direction == Direction.LONG else snap.swing_high_m5
    return _ScoreParts(
        direction=direction,
        body_score=body_score,
        wick_score=wick_score,
        atr_expansion=atr_expansion,
        session_bonus=session_bonus,
        spread_penalty=spread_penalty,
        htf_alignment=htf_alignment,
        momentum_score=momentum,
        volume_score=volume_score,
        entry_hint=snap.ask if direction == Direction.LONG else snap.bid,
        invalidation_anchor=invalidation_anchor,
        setup_metadata={
            "volume_ratio": volume_ratio,
            "regime_confidence": regime.confidence,
            "session": snap.session,
        },
    )


def _build_signal_score(snap: MarketSnapshot, module: Module, parts: _ScoreParts) -> SignalScore:
    score = max(0.0, min(100.0, parts.total))
    return SignalScore(
        module=module,
        direction=parts.direction,
        score=score,
        confidence_tier=_confidence_tier(score),
        body_score=parts.body_score,
        wick_score=parts.wick_score,
        atr_expansion=parts.atr_expansion,
        session_bonus=parts.session_bonus,
        spread_penalty=parts.spread_penalty,
        htf_alignment=parts.htf_alignment,
        momentum_score=parts.momentum_score,
        volume_score=parts.volume_score,
        entry_hint=parts.entry_hint,
        invalidation_anchor=parts.invalidation_anchor,
        setup_id=_setup_id(snap.symbol, snap.timestamp, module, parts.direction, parts.entry_hint),
        signal_timestamp=snap.timestamp,
        setup_metadata=parts.setup_metadata,
    )


def evaluate_signals(
    snap: MarketSnapshot,
    regime: RegimeResult,
    aggression: AggressionState,
    runtime: RuntimeState | None,
    cfg: SignalConfig,
) -> SignalScore | None:
    if regime.regime == Regime.TREND:
        parts = _module_a_parts(snap, regime, cfg)
        module = Module.PULLBACK_CONTINUATION
    elif regime.regime == Regime.BREAKOUT:
        parts = _module_b_parts(snap, regime, cfg)
        module = Module.BREAKOUT_MOMENTUM
    else:
        return None

    if parts is None:
        return None

    signal = _build_signal_score(snap, module, parts)
    if signal.score < _dynamic_threshold(regime, aggression, cfg):
        return None
    if _is_stale(signal, runtime, cfg):
        return None
    return signal


__all__ = ["evaluate_signals"]
