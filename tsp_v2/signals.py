"""Deterministic signal engine for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from typing import Mapping

from .enums import Direction, GovernorState, HealthState, RegimeName, SessionName, SignalFamily
from .models import MarketSnapshot, RegimeDecision, SignalDecision, SignalEvaluation


TREND_BASE_THRESHOLD = 0.72
BREAKOUT_BASE_THRESHOLD = 0.75
MICRO_BASE_THRESHOLD = 0.82
TREND_TTL_SECONDS = 120
BREAKOUT_TTL_SECONDS = 90
MICRO_TTL_SECONDS = 90
SIGNAL_THRESHOLD_FLOOR = 0.65
TREND_CONTINUATION_MIN_BODY_ATR = 0.55
TREND_BODY_ATR_SCORE_RANGE = 0.70
BREAKOUT_DISTANCE_ATR_TARGET = 0.80
MICRO_IMPULSE_SCORE_RANGE = 0.80

GOVERNOR_THRESHOLD_ADJUSTMENTS: dict[GovernorState, float] = {
    GovernorState.SURVIVE: 0.08,
    GovernorState.NORMAL: 0.0,
    GovernorState.ATTACK: -0.03,
    GovernorState.HUNTER: -0.05,
    GovernorState.CHASE: -0.06,
    GovernorState.PROTECT: 0.08,
    GovernorState.SPRINT: -0.08,
}
SESSION_QUALITY: dict[SessionName, float] = {
    SessionName.LONDON_NY: 1.00,
    SessionName.LONDON: 0.90,
    SessionName.EARLY_NY: 0.80,
    SessionName.LATE_NY: 0.55,
    SessionName.ASIA: 0.35,
    SessionName.DEAD: 0.0,
}
OFFENSIVE_REGIME_TO_FAMILY: dict[RegimeName, SignalFamily] = {
    RegimeName.TREND: SignalFamily.TREND_CONTINUATION,
    RegimeName.BREAKOUT: SignalFamily.BREAKOUT_MOMENTUM,
    RegimeName.MICRO_MOMENTUM: SignalFamily.MICRO_IMPULSE,
}


@dataclass(frozen=True, slots=True)
class _SignalCandidate:
    family: SignalFamily
    direction: Direction
    score: float
    threshold: float
    setup_context: str
    rationale: str
    diagnostics: dict[str, float | str]
    stale_anchor_utc: datetime
    ttl_seconds: int
    direction_conflict: bool
    session_quality: float
    spread_health_score: float
    latency_health_score: float


def evaluate_signals(
    snapshot: MarketSnapshot,
    regime: RegimeDecision,
    *,
    governor_state: GovernorState = GovernorState.NORMAL,
    active_signal_keys: Mapping[str, datetime] | None = None,
) -> SignalEvaluation:
    family = OFFENSIVE_REGIME_TO_FAMILY.get(regime.regime)
    if family is None:
        return SignalEvaluation(
            accepted=False,
            decision=None,
            reject_reason="regime_conflict",
            signal_key="",
            diagnostics={"regime": regime.regime.value, "message": "Signal family requires offensive regime"},
        )
    if governor_state is GovernorState.KILL_REVIEW:
        return SignalEvaluation(
            accepted=False,
            decision=None,
            reject_reason="regime_conflict",
            signal_key="",
            diagnostics={"governor_state": governor_state.value, "message": "KILL_REVIEW suppresses signal issuance"},
        )

    candidate = _candidate_for_family(snapshot=snapshot, regime=regime, family=family, governor_state=governor_state)
    signal_key = _signal_identity(snapshot=snapshot, candidate=candidate)
    diagnostics = dict(candidate.diagnostics)
    diagnostics.update(
        {
            "signal_family": family.value,
            "governor_state": governor_state.value,
            "signal_key": signal_key,
        }
    )

    if candidate.direction_conflict:
        return SignalEvaluation(False, None, "HTF_conflict", signal_key, diagnostics)
    if candidate.session_quality <= 0.0:
        return SignalEvaluation(False, None, "session_low_quality", signal_key, diagnostics)
    if candidate.spread_health_score <= 0.0:
        return SignalEvaluation(False, None, "spread_degraded", signal_key, diagnostics)
    if family is SignalFamily.MICRO_IMPULSE and candidate.latency_health_score < 1.0:
        return SignalEvaluation(False, None, "latency_degraded", signal_key, diagnostics)
    if _is_stale(snapshot=snapshot, candidate=candidate):
        return SignalEvaluation(False, None, "stale", signal_key, diagnostics)
    if _is_duplicate(signal_key=signal_key, cycle_time_utc=snapshot.cycle_time_utc, active_signal_keys=active_signal_keys):
        return SignalEvaluation(False, None, "duplicate", signal_key, diagnostics)
    if candidate.score < candidate.threshold:
        diagnostics["score_gap"] = round(candidate.threshold - candidate.score, 6)
        return SignalEvaluation(False, None, "weak_score", signal_key, diagnostics)

    decision = SignalDecision(
        setup_id=signal_key,
        signal_family=family,
        symbol=snapshot.symbol,
        direction=candidate.direction,
        score=round(candidate.score, 6),
        threshold=round(candidate.threshold, 6),
        expires_at_utc=snapshot.cycle_time_utc + timedelta(seconds=candidate.ttl_seconds),
        rationale=candidate.rationale,
        lineage=(
            f"REGIME:{regime.regime.value}",
            f"FAMILY:{family.value}",
            f"GOVERNOR:{governor_state.value}",
        ),
    )
    return SignalEvaluation(True, decision, "", signal_key, diagnostics)


def _candidate_for_family(
    *,
    snapshot: MarketSnapshot,
    regime: RegimeDecision,
    family: SignalFamily,
    governor_state: GovernorState,
) -> _SignalCandidate:
    if family is SignalFamily.TREND_CONTINUATION:
        return _trend_candidate(snapshot=snapshot, regime=regime, governor_state=governor_state)
    if family is SignalFamily.BREAKOUT_MOMENTUM:
        return _breakout_candidate(snapshot=snapshot, regime=regime, governor_state=governor_state)
    return _micro_candidate(snapshot=snapshot, regime=regime, governor_state=governor_state)


def _trend_candidate(
    *,
    snapshot: MarketSnapshot,
    regime: RegimeDecision,
    governor_state: GovernorState,
) -> _SignalCandidate:
    last_m5 = snapshot.bars_m5[-1]
    previous_m5 = snapshot.bars_m5[-2]
    ema20_m5 = _ema([float(bar["close"]) for bar in snapshot.bars_m5], 20)
    atr_m5 = float(snapshot.indicator_bundle["atr_m5"])
    body_ratio = _safe_ratio(abs(float(last_m5["close"]) - float(last_m5["open"])), atr_m5)
    pullback_quality = _trend_pullback_quality(snapshot=snapshot, direction=regime.direction_bias, ema20_m5=ema20_m5)
    htf_alignment = 1.0 if regime.direction_bias is not Direction.FLAT else 0.0
    continuation_quality = _clamp((body_ratio - TREND_CONTINUATION_MIN_BODY_ATR) / TREND_BODY_ATR_SCORE_RANGE)
    session_quality = _session_quality(snapshot.session)
    spread_health_score = _spread_health_score(snapshot.spread_health)
    threshold = _adjusted_threshold(TREND_BASE_THRESHOLD, governor_state)
    score = _clamp(
        (0.25 * htf_alignment)
        + (0.20 * pullback_quality)
        + (0.20 * continuation_quality)
        + (0.15 * _clamp(regime.confidence))
        + (0.10 * session_quality)
        + (0.10 * spread_health_score)
    )
    setup_context = (
        f"trend|bias={regime.direction_bias.value}|m5_close={last_m5['close_time_utc'].isoformat()}|"
        f"body_atr={body_ratio:.3f}|pullback={pullback_quality:.3f}|prev_close={previous_m5['close']:.3f}"
    )
    rationale = (
        f"TREND_CONTINUATION {regime.direction_bias.value} score={score:.3f} "
        f"htf={htf_alignment:.2f} pullback={pullback_quality:.2f} body_atr={body_ratio:.2f}"
    )
    diagnostics = {
        "htf_alignment_score": htf_alignment,
        "pullback_quality": round(pullback_quality, 6),
        "continuation_body_atr_ratio": round(body_ratio, 6),
        "continuation_quality": round(continuation_quality, 6),
        "regime_confidence": round(regime.confidence, 6),
        "session_quality": round(session_quality, 6),
        "spread_health_score": round(spread_health_score, 6),
        "base_threshold": TREND_BASE_THRESHOLD,
        "adjusted_threshold": round(threshold, 6),
    }
    return _SignalCandidate(
        family=SignalFamily.TREND_CONTINUATION,
        direction=regime.direction_bias,
        score=score,
        threshold=threshold,
        setup_context=setup_context,
        rationale=rationale,
        diagnostics=diagnostics,
        stale_anchor_utc=snapshot.indicator_bundle["bar_anchor_m5_close_utc"],
        ttl_seconds=TREND_TTL_SECONDS,
        direction_conflict=_trend_direction_conflict(snapshot=snapshot, direction=regime.direction_bias),
        session_quality=session_quality,
        spread_health_score=spread_health_score,
        latency_health_score=_latency_health_score(snapshot.latency_health),
    )


def _breakout_candidate(
    *,
    snapshot: MarketSnapshot,
    regime: RegimeDecision,
    governor_state: GovernorState,
) -> _SignalCandidate:
    last_m5 = snapshot.bars_m5[-1]
    atr_m5 = float(snapshot.indicator_bundle["atr_m5"])
    recent_high = float(regime.raw_scores["breakout_recent_high"])
    recent_low = float(regime.raw_scores["breakout_recent_low"])
    close_price = float(regime.raw_scores["breakout_close_price"])
    reference_level = recent_high if regime.direction_bias is Direction.LONG else recent_low
    break_distance_atr = _safe_ratio(abs(close_price - reference_level), atr_m5)
    breakout_integrity = _clamp(
        (
            _clamp(break_distance_atr / BREAKOUT_DISTANCE_ATR_TARGET)
            + float(regime.raw_scores.get("breakout_body_dominance", 0.0))
        )
        / 2.0
    )
    expansion_strength = _clamp(float(regime.raw_scores.get("breakout_burst_score", 0.0)))
    participation_confirmation = _clamp(float(regime.raw_scores.get("breakout_participation_score", 0.0)))
    session_quality = _session_quality(snapshot.session)
    spread_health_score = _spread_health_score(snapshot.spread_health)
    threshold = _adjusted_threshold(BREAKOUT_BASE_THRESHOLD, governor_state)
    score = _clamp(
        (0.25 * breakout_integrity)
        + (0.20 * expansion_strength)
        + (0.20 * participation_confirmation)
        + (0.15 * _clamp(regime.confidence))
        + (0.10 * session_quality)
        + (0.10 * spread_health_score)
    )
    setup_context = (
        f"breakout|bias={regime.direction_bias.value}|m5_close={last_m5['close_time_utc'].isoformat()}|"
        f"break_atr={break_distance_atr:.3f}|burst={float(regime.raw_scores.get('breakout_burst_ratio', 0.0)):.3f}|"
        f"participation={float(regime.raw_scores.get('breakout_participation_count', 0.0)):.0f}"
    )
    rationale = (
        f"BREAKOUT_MOMENTUM {regime.direction_bias.value} score={score:.3f} "
        f"integrity={breakout_integrity:.2f} burst={expansion_strength:.2f} "
        f"participation={participation_confirmation:.2f}"
    )
    diagnostics = {
        "breakout_integrity": round(breakout_integrity, 6),
        "break_distance_atr": round(break_distance_atr, 6),
        "expansion_strength": round(expansion_strength, 6),
        "participation_confirmation": round(participation_confirmation, 6),
        "regime_confidence": round(regime.confidence, 6),
        "session_quality": round(session_quality, 6),
        "spread_health_score": round(spread_health_score, 6),
        "base_threshold": BREAKOUT_BASE_THRESHOLD,
        "adjusted_threshold": round(threshold, 6),
    }
    return _SignalCandidate(
        family=SignalFamily.BREAKOUT_MOMENTUM,
        direction=regime.direction_bias,
        score=score,
        threshold=threshold,
        setup_context=setup_context,
        rationale=rationale,
        diagnostics=diagnostics,
        stale_anchor_utc=snapshot.indicator_bundle["bar_anchor_m5_close_utc"],
        ttl_seconds=BREAKOUT_TTL_SECONDS,
        direction_conflict=_breakout_direction_conflict(regime=regime),
        session_quality=session_quality,
        spread_health_score=spread_health_score,
        latency_health_score=_latency_health_score(snapshot.latency_health),
    )


def _micro_candidate(
    *,
    snapshot: MarketSnapshot,
    regime: RegimeDecision,
    governor_state: GovernorState,
) -> _SignalCandidate:
    last_m1 = snapshot.bars_m1[-1]
    impulse_ratio = float(regime.raw_scores.get("micro_impulse_ratio", 0.0))
    continuation_score = float(regime.raw_scores.get("micro_continuation_ok", 0.0))
    spread_health_score = _spread_health_score(snapshot.spread_health)
    latency_health_score = _latency_health_score(snapshot.latency_health)
    session_quality = _session_quality(snapshot.session)
    conflict_penalty_component = _micro_conflict_component(snapshot=snapshot, direction=regime.direction_bias)
    threshold = _adjusted_threshold(MICRO_BASE_THRESHOLD, governor_state)
    score = _clamp(
        (0.30 * _clamp((impulse_ratio - 0.80) / MICRO_IMPULSE_SCORE_RANGE))
        + (0.20 * continuation_score)
        + (0.15 * spread_health_score)
        + (0.15 * latency_health_score)
        + (0.10 * session_quality)
        + (0.10 * conflict_penalty_component)
    )
    setup_context = (
        f"micro|bias={regime.direction_bias.value}|m5_close={snapshot.indicator_bundle['bar_anchor_m5_close_utc'].isoformat()}|"
        f"m1_close={last_m1['close_time_utc'].isoformat()}|impulse={impulse_ratio:.3f}|"
        f"latency={snapshot.latency_health.value}|spread={snapshot.spread_ratio:.3f}"
    )
    rationale = (
        f"MICRO_IMPULSE {regime.direction_bias.value} score={score:.3f} "
        f"impulse={impulse_ratio:.2f} continuation={continuation_score:.2f} "
        f"latency={latency_health_score:.2f}"
    )
    diagnostics = {
        "impulse_strength": round(_clamp((impulse_ratio - 0.80) / MICRO_IMPULSE_SCORE_RANGE), 6),
        "impulse_ratio": round(impulse_ratio, 6),
        "m1_continuation_score": round(continuation_score, 6),
        "spread_health_score": round(spread_health_score, 6),
        "latency_health_score": round(latency_health_score, 6),
        "session_quality": round(session_quality, 6),
        "htf_conflict_component": round(conflict_penalty_component, 6),
        "base_threshold": MICRO_BASE_THRESHOLD,
        "adjusted_threshold": round(threshold, 6),
    }
    return _SignalCandidate(
        family=SignalFamily.MICRO_IMPULSE,
        direction=regime.direction_bias,
        score=score,
        threshold=threshold,
        setup_context=setup_context,
        rationale=rationale,
        diagnostics=diagnostics,
        stale_anchor_utc=last_m1["close_time_utc"],
        ttl_seconds=MICRO_TTL_SECONDS,
        direction_conflict=conflict_penalty_component <= 0.0,
        session_quality=session_quality,
        spread_health_score=spread_health_score,
        latency_health_score=latency_health_score,
    )


def _signal_identity(*, snapshot: MarketSnapshot, candidate: _SignalCandidate) -> str:
    anchor = snapshot.indicator_bundle["bar_anchor_m5_close_utc"].isoformat()
    payload = "|".join(
        (
            snapshot.symbol,
            candidate.family.value,
            candidate.direction.value,
            anchor,
            candidate.setup_context,
        )
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def _is_stale(*, snapshot: MarketSnapshot, candidate: _SignalCandidate) -> bool:
    age_seconds = max(0.0, (snapshot.cycle_time_utc - candidate.stale_anchor_utc).total_seconds())
    return age_seconds > candidate.ttl_seconds


def _is_duplicate(
    *,
    signal_key: str,
    cycle_time_utc: datetime,
    active_signal_keys: Mapping[str, datetime] | None,
) -> bool:
    if not signal_key or not active_signal_keys:
        return False
    expiry = active_signal_keys.get(signal_key)
    return expiry is not None and expiry > cycle_time_utc


def _adjusted_threshold(base_threshold: float, governor_state: GovernorState) -> float:
    adjustment = GOVERNOR_THRESHOLD_ADJUSTMENTS.get(governor_state, 0.08)
    return max(SIGNAL_THRESHOLD_FLOOR, base_threshold + adjustment)


def _trend_pullback_quality(*, snapshot: MarketSnapshot, direction: Direction, ema20_m5: float) -> float:
    del ema20_m5
    atr_m5 = float(snapshot.indicator_bundle["atr_m5"])
    prior_bars = snapshot.bars_m5[-3:-1]
    current = snapshot.bars_m5[-1]
    if direction is Direction.LONG:
        counter_bars = sum(1 for bar in prior_bars if float(bar["close"]) <= float(bar["open"]))
        pullback_depth = _safe_ratio(
            max(float(bar["high"]) for bar in prior_bars) - min(float(bar["close"]) for bar in prior_bars),
            atr_m5,
        )
        reclaim = 1.0 if float(current["close"]) > max(float(bar["high"]) for bar in prior_bars) else 0.5
    else:
        counter_bars = sum(1 for bar in prior_bars if float(bar["close"]) >= float(bar["open"]))
        pullback_depth = _safe_ratio(
            max(float(bar["close"]) for bar in prior_bars) - min(float(bar["low"]) for bar in prior_bars),
            atr_m5,
        )
        reclaim = 1.0 if float(current["close"]) < min(float(bar["low"]) for bar in prior_bars) else 0.5
    return _clamp(
        (0.40 * (counter_bars / max(len(prior_bars), 1)))
        + (0.35 * _clamp(pullback_depth / 0.60))
        + (0.25 * reclaim)
    )


def _trend_direction_conflict(*, snapshot: MarketSnapshot, direction: Direction) -> bool:
    last_m5 = snapshot.bars_m5[-1]
    if direction is Direction.LONG:
        return float(last_m5["close"]) <= float(last_m5["open"])
    if direction is Direction.SHORT:
        return float(last_m5["close"]) >= float(last_m5["open"])
    return True


def _breakout_direction_conflict(*, regime: RegimeDecision) -> bool:
    close_price = float(regime.raw_scores["breakout_close_price"])
    recent_high = float(regime.raw_scores["breakout_recent_high"])
    recent_low = float(regime.raw_scores["breakout_recent_low"])
    if regime.direction_bias is Direction.LONG:
        return close_price <= recent_high
    if regime.direction_bias is Direction.SHORT:
        return close_price >= recent_low
    return True


def _micro_conflict_component(*, snapshot: MarketSnapshot, direction: Direction) -> float:
    h1_bias = _bias_from_slope(float(snapshot.indicator_bundle.get("h1_slope", 0.0)))
    m15_bias = _bias_from_slope(float(snapshot.indicator_bundle.get("m15_slope", 0.0)))
    conflicts = 0
    if h1_bias not in (Direction.FLAT, direction):
        conflicts += 1
    if m15_bias not in (Direction.FLAT, direction):
        conflicts += 1
    if conflicts >= 2:
        return 0.0
    if conflicts == 1:
        return 0.5
    return 1.0


def _spread_health_score(spread_health: HealthState) -> float:
    if spread_health is HealthState.RED:
        return 0.0
    if spread_health is HealthState.YELLOW:
        return 0.6
    return 1.0


def _latency_health_score(health: HealthState) -> float:
    if health is HealthState.GREEN:
        return 1.0
    if health is HealthState.YELLOW:
        return 0.5
    return 0.0


def _session_quality(session: SessionName) -> float:
    return SESSION_QUALITY.get(session, 0.0)


def _bias_from_slope(slope: float) -> Direction:
    if slope > 0.0:
        return Direction.LONG
    if slope < 0.0:
        return Direction.SHORT
    return Direction.FLAT


def _ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"Need at least {period} values for EMA")
    multiplier = 2.0 / (period + 1.0)
    ema_value = sum(values[:period]) / period
    for value in values[period:]:
        ema_value = ((value - ema_value) * multiplier) + ema_value
    return ema_value


def _safe_ratio(numerator: float, denominator: float) -> float:
    if abs(denominator) < 1e-8:
        return 0.0
    return numerator / denominator


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))
