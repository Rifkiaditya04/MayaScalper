# TOURNAMENT SCALPING PREDATOR V1
## FINAL TECHNICAL BLUEPRINT

**Status:** COMPLETE — ALL PHASES LOCKED  
**Architecture sign-off:** Approved as competition-grade implementation spec  
**Target:** XAUUSD-only, Python + MT5, M1/M5/M15/H1 hybrid, competition context

---

## TABLE OF CONTENTS

1. [Architecture Overview](#architecture-overview)
2. [Phase 2.1 — Runtime State & Data Structures](#phase-21--runtime-state--data-structures)
3. [Phase 2.2 — Regime Engine](#phase-22--regime-engine)
4. [Phase 2.3 — Signal Scoring Engine](#phase-23--signal-scoring-engine)
5. [Phase 2.4 — Risk Engine](#phase-24--risk-engine)
6. [Phase 2.5 — Competition Governor](#phase-25--competition-governor)
7. [Phase 2.6 — Execution Contracts](#phase-26--execution-contracts)
8. [Phase 2.7 — Position Lifecycle Manager](#phase-27--position-lifecycle-manager)
9. [Phase 2.8 — Runtime State Final + Main Loop](#phase-28--runtime-state-final--main-loop)
10. [Phase 2.9 — Config Surface](#phase-29--config-surface)
11. [Phase 2.10 — Validation Plan + Contest Ops](#phase-210--validation-plan--contest-ops)
12. [Locked Decisions Summary](#locked-decisions-summary)

---

## ARCHITECTURE OVERVIEW

### Design Philosophy

TSP V1 is a **regime-aware momentum scalper** with adaptive aggression, winner pyramiding, and strict loser containment — built for XAUUSD competition trading.

**Core principle:** Attack real exploitable micro-impulses, not every candle.

### System Architecture

```
MarketSnapshot (M1/M5/M15/H1)
        ↓
  REGIME ENGINE (classify_regime)
        ↓
  COMPETITION GOVERNOR (evaluate_governor)
        ↓
  SIGNAL ENGINE (evaluate_signals)
        ↓
  RISK ENGINE (evaluate_risk)
        ↓
  EXECUTION CONTRACTS (execute_order)
        ↓
  POSITION LIFECYCLE MANAGER (evaluate_lifecycle)
        ↓
  RuntimeState + SQLite persistence
```

### Governance Hierarchy (locked)

```
Kill Switch (absolute)
      ↓
Acute Risk Engine (DD, loss streak, spread)
      ↓
Competition Governor (macro bias only)
      ↓
Strategy Modules (regime, signal, position)
```

### File Map

```
tsp/
├── state.py          # All enums, dataclasses, runtime state
├── regime.py         # Regime classification engine
├── signals.py        # Signal scoring (2 modules)
├── risk.py           # Risk sizing, aggression FSM, pyramid
├── competition.py    # Competition governor, context
├── execution.py      # Execution orchestration (not broker logic)
├── position_manager.py # Lifecycle: TP, BE, trail, partial, orphan
├── bot.py            # Main loop orchestration
├── config.py         # Config loading, validation
└── config.yaml       # Operator-facing config
```

### Key Locked Decisions

| Dimension | Decision |
|---|---|
| Language | Python-idiomatic |
| Substrate | Existing mt5_client.py stack |
| Main loop | M1 bar-based (not tick) |
| Persistence | Hybrid: SQLite + MT5 reconcile |
| Regimes | 4: TREND, BREAKOUT, CHOP, NEWS_DEAD |
| Modules | 2: Pullback Continuation, Breakout Momentum |
| Aggression states | 3: DEFENSIVE, NORMAL, AGGRESSIVE |
| Symbol | XAUUSD ONLY (V1) |

---

## PHASE 2.1 — RUNTIME STATE & DATA STRUCTURES

### Enums

```python
from enum import Enum, auto

class Regime(Enum):
    TREND      = auto()
    BREAKOUT   = auto()
    CHOP       = auto()
    NEWS_DEAD  = auto()

class Module(Enum):
    PULLBACK_CONTINUATION = auto()
    BREAKOUT_MOMENTUM     = auto()
    NONE                  = auto()

class AggressionState(Enum):
    DEFENSIVE  = auto()
    NORMAL     = auto()
    AGGRESSIVE = auto()

class Direction(Enum):
    LONG  = auto()
    SHORT = auto()
    FLAT  = auto()

class TradePhase(Enum):
    IDLE        = auto()
    ENTERED     = auto()
    PYRAMIDED   = auto()
    EXITING     = auto()
    COOLDOWN    = auto()

class ConfidenceTier(Enum):
    WEAK   = "WEAK"
    NORMAL = "NORMAL"
    GOOD   = "GOOD"
    ELITE  = "ELITE"
```

### MarketSnapshot (Immutable)

```python
@dataclass(frozen=True)
class CandleData:
    timestamp:  datetime
    open:       float
    high:       float
    low:        float
    close:      float
    volume:     float
    timeframe:  str

@dataclass(frozen=True)
class MarketSnapshot:
    symbol:             str
    timestamp:          datetime
    m1:                 CandleData
    m5:                 CandleData
    m15:                CandleData
    h1:                 CandleData
    # ATR per TF + baselines (20-bar median)
    atr_m1:             float
    atr_m1_base:        float
    atr_m5:             float
    atr_m5_base:        float
    atr_m15_base:       float
    atr_h1_base:        float
    # Compression windows (10-bar median, 5-20 bars ago)
    atr_m1_prev_window: float
    atr_m5_prev_window: float
    # Slopes (raw, ATR-normalized in regime engine)
    h1_slope:           float
    m15_slope:          float
    # Spread
    spread_current:     float
    spread_baseline:    float
    # Volume proxy
    tick_vol_m1:        float
    tick_vol_m1_base:   float
    # Swing reference (computed in data_pipeline)
    swing_high_m5:      float
    swing_low_m5:       float
    # Recent closes for ROC
    m1_closes_recent:   tuple[float, ...]
    # Context flags
    is_news_window:     bool
    session:            str    # "ASIA","LONDON","NY","OVERLAP","DEAD"
    # Broker live prices
    bid:                float
    ask:                float
```

### SignalScore (Immutable)

```python
@dataclass(frozen=True)
class SignalScore:
    module:              Module
    direction:           Direction
    score:               float
    confidence_tier:     ConfidenceTier
    body_score:          float
    wick_score:          float
    atr_expansion:       float
    session_bonus:       float
    spread_penalty:      float
    htf_alignment:       float
    momentum_score:      float
    volume_score:        float
    entry_hint:          float
    invalidation_anchor: float
    setup_id:            str       # SHA-256 deterministic hash
    signal_timestamp:    datetime
    setup_metadata:      dict
```

### LayerState & PositionState

```python
@dataclass
class LayerState:
    ticket:              int
    direction:           Direction
    entry_price:         float
    sl_price:            float          # Mutable — moves with BE/trail
    tp_price:            Optional[float]
    lot_size:            float          # Mutable — reduced after partial
    r_risk:              float          # Actual committed R%
    initial_r_distance:  float          # IMMUTABLE — abs(entry - initial_sl)
    open_time:           datetime
    layer_index:         int
    module:              Module
    setup_id:            str
    partial_taken:       bool = False
    bars_in_trade:       int = 0
    tp_attach_attempts:  int = 0

    def unrealized_r(self, current_price: float) -> float:
        """Always anchored to initial_r_distance. Never mutable SL."""
        if self.initial_r_distance < 1e-8: return 0.0
        if self.direction == Direction.LONG:
            return (current_price - self.entry_price) / self.initial_r_distance
        return (self.entry_price - current_price) / self.initial_r_distance

@dataclass
class PositionState:
    phase:               TradePhase = TradePhase.IDLE
    direction:           Direction  = Direction.FLAT
    module:              Module     = Module.NONE
    layers:              list[LayerState] = field(default_factory=list)
    last_exit_time:      Optional[datetime] = None
    last_exit_direction: Optional[Direction] = None
    bars_since_exit:     int = 0
    reentry_count:       int = 0
    _unrealized_pnl_r:   float = 0.0

    @property
    def layer_count(self) -> int: return len(self.layers)
    @property
    def total_r_risk(self) -> float: return sum(l.r_risk for l in self.layers)
    @property
    def unrealized_pnl_r(self) -> float: return self._unrealized_pnl_r
    def update_unrealized(self, pnl_r: float) -> None: self._unrealized_pnl_r = pnl_r

    def add_layer(self, layer: LayerState, signal: SignalScore) -> None:
        self.layers.append(layer)
        self.direction = layer.direction
        self.module    = signal.module
        self.phase = TradePhase.PYRAMIDED if len(self.layers) > 1 else TradePhase.ENTERED

    def transition_to_cooldown(self, exit_direction: Direction) -> None:
        self.phase = TradePhase.COOLDOWN
        self.last_exit_time = utcnow()
        self.last_exit_direction = exit_direction
        self.bars_since_exit = 0
        self.layers = []
        self._unrealized_pnl_r = 0.0

    def transition_to_idle(self) -> None:
        self.phase = TradePhase.IDLE
        self.direction = Direction.FLAT
        self.module = Module.NONE
        self.bars_since_exit = 0

    def remove_layer(self, ticket: int) -> Optional[LayerState]:
        for i, l in enumerate(self.layers):
            if l.ticket == ticket: return self.layers.pop(i)
        return None
```

### RiskParams (Frozen Config)

```python
@dataclass(frozen=True)
class RiskParams:
    r_weak:               float = 0.20
    r_normal:             float = 0.50
    r_good:               float = 0.90
    r_elite:              float = 1.25
    r_max_single:         float = 1.50
    mult_defensive:       float = 0.60
    mult_normal:          float = 1.00
    mult_aggressive:      float = 1.20
    equity_floor_ratio:   float = 0.70
    dd_pct_defensive:     float = 2.0
    dd_pct_kill:          float = 4.0
    losses_defensive:     int   = 3
    losses_kill:          int   = 5
    wins_for_aggressive:  int   = 5
    daily_r_for_aggressive: float = 2.5
    dd_max_for_aggressive:  float = 0.5
    pyramid_max_layers:   int   = 2
    pyramid_min_profit_r: float = 1.0
    pyramid_aggregate_cap: float = 2.5
    reentry_max_attempts: int   = 1
    reentry_min_bars:     int   = 3
    spread_min_base_ticks: float = 30.0
```

---

## PHASE 2.2 — REGIME ENGINE

### Overview

Hierarchical classification. First match wins. Single regime output per bar. Confidence 0.0–1.0.

**Priority order:** NEWS_DEAD → TREND → BREAKOUT → CHOP (fallback)

### Regime Definitions

**NEWS_DEAD:** Hard lockout. `is_news_window=True`, session="DEAD", spread_ratio > 4.0, or ATR collapse below session-specific threshold (ASIA: 0.30, other: 0.35).

**TREND:** Slope agreement (both H1 and M15 ATR-normalized slopes same direction) AND composite strength ≥ 0.55. Composite = (ADX_component + ATR_component + slope_component) / 3.0. H1 ADX > 22 gives confidence boost +0.12.

**BREAKOUT:** Prior compression confirmed (atr_m1_prev_window / atr_m1_base < 0.80, 10-bar window) AND current burst (atr_ratio_m1 > 1.50). Requires at least 2 of 5 secondary conditions (M5 expanding, ADX in range 16–40, M5 compression, volume expansion, direction emergence).

**CHOP:** Fallback. Actual confidence from chop signals, no artificial floor.

### TF Conflict Resolution

- H1 TREND but M15 ADX < 22: flag `HTF_TREND_PENDING`, downgrade to CHOP
- Both TF agree: confidence boost +0.12
- TREND + BREAKOUT both qualify: TREND wins, note `TREND_WITH_BO_CONFIRM`

### Slope Normalization

```
h1_slope_norm  = h1_slope  / atr_h1_base
m15_slope_norm = m15_slope / atr_m15_base
slope_agree    = both same sign AND abs > SLOPE_AGREE_MIN (0.10)
slope_strength = (abs(h1_norm) + abs(m15_norm)) / 2.0
```

### RegimeResult

```python
@dataclass(frozen=True)
class RegimeResult:
    regime:         Regime
    confidence:     float
    direction_bias: Direction  # LONG / SHORT / FLAT
    conflict_note:  str        # "" or "HTF_TREND_PENDING", "TREND_WITH_BO_CONFIRM"
    raw_scores:     dict
```

### Direction Bias

Derived from ATR-normalized slopes. Both H1 and M15 must agree for LONG/SHORT. Disagreement → FLAT. Thresholds config-driven (SLOPE_BIAS_LONG_MIN = 0.08).

---

## PHASE 2.3 — SIGNAL SCORING ENGINE

### Architecture

```
MarketSnapshot + RegimeResult
        ↓
  Module Router (regime gates)
        ↓
  Module A: Pullback Continuation (TREND only)
  Module B: Breakout Momentum (BREAKOUT only)
        ↓
  Score Aggregator → threshold filter → SignalScore | None
```

### Scoring Components (0–100 total)

| Component | Max | Module A | Module B |
|---|---|---|---|
| body_score | 20 | ✓ | ✓ |
| wick_score | 15 | continuation | displacement |
| atr_expansion | 20 | ✓ | ✓ (+30% boost) |
| htf_alignment | 15 | strict | permissive |
| momentum_score | 15 | ✓ | ✓ |
| session_bonus | 10 | ✓ | ✓ |
| volume_score | 5 | — | ✓ |
| spread_penalty | -20 | ✓ | ✓ |

### Key Component Formulas

**Body (multiplicative — no cheating via close alone):**
```
body_norm = (body_ratio - 0.45) / (0.85 - 0.45), clamped 0-1
close_quality = 0-1 directional
score = body_norm * close_quality * 20
```

**Wick (separated by module):**
- Module A (continuation cleanliness): rejection wick ratio < 0.25 for full score
- Module B (displacement quality): close displacement from midpoint > 0.50 for score

**Momentum ROC (unit: ATR fractions in N bars):**
```
roc_norm = (close[-1] - close[-N-1]) / atr_m1
roc_directional >= 0.20 for non-zero score
Missing data → 0.0 (no optimism)
```

**HTF alignment:**
- Module A: FLAT bias = 0 (strict, trend module requires direction)
- Module B: FLAT bias = confidence * 8.0 (breakout can initiate direction)
- Counter-HTF: 0 for both (V1 decision)

### Confidence Tier Mapping

```
score > 80.0  → ELITE
score >= 65.0 → GOOD
score >= 50.0 → NORMAL
score < 50.0  → WEAK
```

### Pullback Structural Validation (Module A)

```
LONG: retrace = swing_high_m5 - m1.close
SHORT: retrace = m1.close - swing_low_m5
depth_atr = retrace / atr_m1
Valid: PULLBACK_MIN_DEPTH_ATR (0.30) <= depth_atr <= PULLBACK_MAX_DEPTH_ATR (2.50)
swing reference: rolling 6-bar M5 window (not single candle extreme)
```

### Dynamic Threshold

```
threshold = base_per_regime + aggression_adj + (1 - regime.confidence) * 4.0
aggression_adj: AGGRESSIVE=-7, NORMAL=0, DEFENSIVE=+10
```

### Stale Signal (Lineage-Aware)

Same module + same direction + within 3 bars AND score improvement < 8.0 → suppress. Different direction or module → always fresh.

### Setup ID (Deterministic)

```python
raw = f"{symbol}|{timestamp.isoformat()}|{module}|{direction}|{round(entry, 2)}"
setup_id = sha256(raw).hexdigest()[:16]
```

---

## PHASE 2.4 — RISK ENGINE

### Effective Equity

```python
effective_equity = clamp(current_equity,
    floor   = starting_equity * 0.70,
    ceiling = equity_peak)
```

### Lot Sizing (Single Authority)

```python
def compute_lot_size(entry, invalidation, r_percent, effective_equity, contract):
    sl_distance = abs(entry - invalidation)
    risk_amount = effective_equity * (r_percent / 100.0)
    sl_in_ticks = sl_distance / contract.tick_size
    risk_per_lot = sl_in_ticks * contract.tick_value
    raw_lot = risk_amount / risk_per_lot
    # Quantize via Decimal ROUND_DOWN to volume_step
    return quantize_volume(raw_lot, contract)
```

### Aggression FSM (Pure — no mutation)

```python
# Returns AggressionTransitionResult(new_state, activate_kill, kill_reason)

Kill triggers (activate_kill=True):
  DD >= 4.0% OR consecutive_losses >= 5

DEFENSIVE triggers:
  DD >= 2.0% OR consecutive_losses >= 3

Recovery DEFENSIVE → NORMAL:
  DD < 1.0% AND consecutive_losses <= 1

AGGRESSIVE trigger (from NORMAL):
  consecutive_wins >= 5 AND daily_pnl_r >= 2.5 AND DD < 0.5%

Revert AGGRESSIVE → NORMAL:
  1 loss OR DD >= 1.0%
```

### Pyramid Eligibility Gates (6 gates, all must pass)

1. layer_count < PYRAMID_MAX_LAYERS (2)
2. direction match
3. unrealized_pnl_r >= PYRAMID_MIN_PROFIT_R (1.0R)
4. aggregate_cap: total_r_risk + new_r <= 2.5R
5. new_aggression != DEFENSIVE
6. signal confidence_tier in (GOOD, ELITE)

Pyramid aggregate cap = **initial committed R sum**, not mark-to-market.

### Emergency Exit Rules

1. Kill switch active
2. DD >= 4.0%
3. Spread persists > 3.5x baseline for 2 bars (profit-aware: deep losers let SL govern)
4. News window (profit-aware: flush only if pnl_r < 0.30 OR fresh < 3 bars)
5. NEWS_DEAD regime (entry block only, not forced exit)

### Emergency Flatten Philosophy

```
Flatten when: near breakeven (pnl < +0.30R) OR fresh position (< 3 bars)
Do NOT flatten: profitable runner OR deep loser (< -0.50R) → let SL govern
```

---

## PHASE 2.5 — COMPETITION GOVERNOR

### Governor States

```
SURVIVE  → DD active or loss streak (conservative)
NORMAL   → default
HUNT     → behind pace + time pressure + active regime
PROTECT  → significant lead (lead_protect_r reached)
SPRINT   → final sprint_pct% of competition
```

### GovernorDirective (Immutable)

```python
@dataclass(frozen=True)
class GovernorDirective:
    governor_state:             GovernorState
    aggression_bias:            float    # -1.0 to +1.0 soft hint
    threshold_modifier:         float    # Clamped [-10, +12], additive
    session_risk_budget_r:      float    # Max R committable this session
    allow_aggressive_features:  bool     # Pyramid + reentry eligibility hint
    session_pause:              bool     # Circuit breaker active
    governor_note:              str
```

### Aggression Bias Application (Hierarchy Respected)

```python
def apply_governor_bias(acute_aggression, bias, kill_switch):
    if kill_switch: return DEFENSIVE          # Always wins
    if acute_aggression == DEFENSIVE: return DEFENSIVE  # Acute safety wins
    if bias >= +0.4 and acute == NORMAL: return AGGRESSIVE
    if bias <= -0.4 and acute == AGGRESSIVE: return NORMAL
    if bias <= -0.6: return DEFENSIVE
    return acute_aggression
```

### PnL-Aware Circuit Breaker

Opens only when BOTH conditions met:
- session_loss_count >= circuit_loss_count (3)
- session_pnl_r < circuit_session_pnl_r (-2.0R)

Active only in trading sessions (LONDON, NY, OVERLAP). Resets on session start.

### CompetitionProfile (Mandatory Config)

All targets operator-configured. No hardcoded assumptions. Sprint based on `sprint_pct` (normalized %), not fixed days.

### SQLite Schema

```sql
CREATE TABLE competition_context (
    id, total_days, start_equity, starting_date, total_pnl_r,
    daily_pnl_r, session_pnl_r, session_loss_count,
    session_risk_committed_r, current_session, governor_state,
    days_elapsed, updated_at
);
CREATE TABLE governor_log (
    id, timestamp, governor_state, aggression_bias, threshold_mod,
    budget_r, session_pause, note, total_pnl_r, dd_pct
);
```

---

## PHASE 2.6 — EXECUTION CONTRACTS

### Architecture Boundary

```
execution.py  = orchestration, validation, dedup, structured results
mt5_client.py = broker intelligence (retcode, comment norm, fill fallback)
```

### BrokerExecutionAdapter Protocol (Frozen)

```python
class BrokerExecutionAdapter(Protocol):
    def send_market_order(symbol, action, volume, sl, tp, comment, magic) -> dict
    # Returns: {retcode, order, deal, price}
    # mt5_client owns: comment normalization, retry, fill fallback

    def modify_position(ticket, sl, tp) -> dict
    # CONTRACT: SUCCESS = broker-verified, not just request sent
    # Returns: {retcode, sl_confirmed, tp_confirmed}

    def partial_close(ticket, symbol, volume, comment) -> dict
    # CONTRACT: returns ACTUAL executed volume
    # Returns: {retcode, price, volume_executed}

    def get_position_by_ticket(ticket) -> Optional[dict]
    def get_all_positions(magic) -> list[dict]
    def emergency_close(ticket, symbol, volume, reason) -> dict
    def get_symbol_info(symbol) -> Optional[dict]
    def get_server_time() -> datetime
    def get_equity() -> float
```

### Pre-Execution Validation (6 gates, first-fail)

1. Duplicate: TTL-based ExecutionRegistry (PENDING/COMPLETED states)
2. Signal TTL: time-based, age_seconds > 90 → STALE
3. Spread hard veto: ratio > 2.5x baseline
4. SL distance: sl_distance_ticks < stops_level (open context only)
5. Kill switch
6. Regime lockout (NEWS_DEAD, CHOP)

### Retcode Taxonomy (Authoritative MT5 Constants)

```
10009 → SUCCESS       10010 → PARTIAL_FILL   10004 → REQUOTE
10006 → REJECT        10013 → INVALID_PRICE  10014 → INVALID_VOLUME
10016 → INVALID_STOPS 10019 → NOT_ENOUGH_MONEY
10020 → PRICE_CHANGED (retriable)  10021 → PRICE_CHANGED
10026 → TRADE_DISABLED  10027 → TRADE_DISABLED  10030 → INVALID_FILL
10031 → TIMEOUT (retriable)  10032 → MARKET_CLOSED

Non-retriable: REJECT, TRADE_DISABLED, MARKET_CLOSED, NOT_ENOUGH_MONEY,
               INVALID_VOLUME, INVALID_PRICE, INVALID_STOPS, INVALID_FILL
Retriable:     REQUOTE, PRICE_CHANGED, TIMEOUT, UNKNOWN
```

### ExecutionStatus

```
FILLED, PARTIAL_FILL, FILLED_UNVERIFIED (ticket exists, verify failed),
REJECTED, TIMEOUT, SPREAD_VETOED, STALE_SIGNAL, DUPLICATE,
INVALID_PARAMS, MT5_ERROR
```

### ExecutionResult (Immutable, always returned)

```python
@dataclass(frozen=True)
class ExecutionResult:
    status, ticket, fill_price, fill_lot, sl_confirmed, tp_confirmed,
    retcode, retcode_class, slippage_ticks, latency_ms,
    setup_id, attempt_count, timestamp, note
```

### Volume Quantizer (Decimal, ROUND_DOWN)

```python
from decimal import Decimal, ROUND_DOWN
quantized = (raw / step).to_integral_value(ROUND_DOWN) * step
```

### Key Doctrines

- Entry price: real broker bid/ask, not synthetic
- SL validation: stops_level only at open; freeze_level + stops_level for modify
- Initial TP: NOT sent with order (Position Manager attaches immediately post-fill)
- Partial fill: accept, Position Manager reconciles
- FILLED_UNVERIFIED: distinct from TIMEOUT (position may be live)
- Slippage unit: ticks (not pip)
- Timestamps: timezone-aware UTC always

---

## PHASE 2.7 — POSITION LIFECYCLE MANAGER

### Doctrines

- TP attach: immediate post-fill in execution path (not next bar)
- Lifecycle = verify + bounded recovery only
- R triggers: always anchored to immutable `initial_r_distance`
- BE/trail: one-way ratchet (never move SL against)
- State mutations: returned as `LayerMutation` objects, caller applies atomically

### TP Attach (Post-Fill)

```python
# Module A: entry + (initial_r_distance * 1.8)
# Module B: entry + (initial_r_distance * 2.2)
# Invalid distance (< stops_level): REJECT, do not widen
# Verify: get_position_by_ticket(), check broker_tp within 3 ticks
# Bounded retry: 2 immediate attempts, then lifecycle recovery
# Max attempts: 3 total → kill-switch escalation
```

### Breakeven (0.80R trigger)

```python
unrealized_r = layer.unrealized_r(current_price)  # uses initial_r_distance
if unrealized_r >= 0.80 and not already_at_be:
    be_sl = entry ± (BE_BUFFER_TICKS * tick_size)
    # Validate vs freeze_level (modify context)
    # Only move if new_sl better than current_sl (one-way)
```

### Trailing (1.50R trigger)

```python
if unrealized_r >= 1.50 and already_at_be:
    trail_sl = current_price ∓ (atr_m1 * 1.20)
    # Only move if improvement >= 5 ticks
    # Only move if away from market >= freeze_level
```

### Partial Exit (1.00R trigger)

```python
if unrealized_r >= 1.00 and not partial_taken:
    close_lot = quantize_volume(lot_size * 0.50, contract)
    # Check remain >= volume_min
    # realized_r = gross_r_per_unit * (executed_lot / lot_size)  ← fractional
    # Use actual volume_executed from adapter (not requested)
```

### Priority Order (per bar, per layer)

```
1. TP recovery (if missing) → continue if unresolved (doctrine: no progression without TP)
2. Breakeven
3. Trail (only after BE established)
4. Partial exit (independent)
```

### LifecycleAction Taxonomy

```
Success: TP_ATTACHED, SL_MOVED_TO_BE, SL_TRAILED, PARTIAL_CLOSED, FULL_CLOSED,
         PYRAMID_ADDED, ORPHAN_RECOVERED
Failure: TP_ATTACH_FAILED, TP_ATTACH_ESCALATED, PARTIAL_CLOSE_FAILED,
         EMERGENCY_CLOSE_FAILED
```

### LifecycleResult (Pure — no mutation in evaluator)

```python
@dataclass(frozen=True)
class LifecycleResult:
    events:      tuple[LifecycleEvent, ...]
    mutations:   tuple[LayerMutation, ...]   # Caller applies atomically
    ctx_delta:   Optional[CompetitionContextDelta]
    signal_kill: bool
    kill_reason: str
```

### Orphan Recovery (Startup)

```
No-SL orphan: immediate flatten + kill-switch signal (no synthetic SL)
No-TP orphan: adopt SL-only, no invented TP (lifecycle manages via trail)
Unknown provenance: FLATTEN by default (ADOPT requires expert_mode=true)
Case C (SQLite not broker): mark closed in lifecycle_events
No runtime.snap dependency (self-contained)
```

---

## PHASE 2.8 — RUNTIME STATE FINAL + MAIN LOOP

### RuntimeState (All Fields Explicit)

```python
@dataclass
class RuntimeState:
    # Identity
    symbol, magic, starting_equity, start_time
    # Market
    snap: Optional[MarketSnapshot]
    # Regime + Aggression
    regime: RegimeResult
    aggression: AggressionState
    # Position
    position: PositionState
    # Equity
    equity_current, equity_peak, daily_start_equity
    # Performance
    consecutive_wins, consecutive_losses, daily_pnl_r, total_trades_today
    # Signal tracking
    last_signal, last_signal_age_bars
    # Spread
    spread_elevated_bars
    # Kill switch
    kill_switch_active, kill_reason
    manual_override
    # Competition
    competition_ctx: Optional[CompetitionContext]
    # Risk params
    risk_params: RiskParams
    # Day tracking (explicit, not monkey-patched)
    last_broker_day: Optional[date]
    # Error budget
    consecutive_bar_errors: int
```

### Mutation Doctrine

RuntimeState mutated ONLY by bot.py and its owned helpers (`_handle_*`, `_update_*`, `_apply_*`). All subsystems are PURE — they return results. bot.py applies all state changes.

### Bar Loop Order (20 steps, strict)

```
 0. Kill switch check (top of loop, always first)
 1. Wait for M1 bar close
 2. Fetch market data + equity
 3. Update unrealized PnL
 4. Session boundary check
 5. Day boundary check (broker server time)
 6. Phase transitions (_apply_position_phase_transitions + cooldown)
 7. Regime classification
 8. Aggression transition (pure → caller applies)
 9. Governor directive
10. Spread elevated counter
11. Lifecycle management (active positions only)
12. Emergency risk exit check
13. Effective aggression (governor bias applied)
14. Signal evaluation (if not session_pause)
15. Risk decision (with signal)
16. Session budget gate
17. Execute + onboard (ENTER or PYRAMID)
18. Signal age + bars_in_trade increment
19. Persist last_bar_time + runtime_counters (SUCCESS ONLY — not on exception)
```

### Idempotency

`last_bar_time` persisted AFTER successful bar completion only. On restart, missed bar is reprocessed. ExecutionRegistry dedup prevents duplicate fills.

### Phase Transition Ownership (Canonical)

`_apply_position_phase_transitions(runtime, mt5, db)` — explicit mt5 injection. Detects broker-side closes each bar. Transitions ENTERED→COOLDOWN on all layers closed.

`_apply_cooldown_progression(runtime, db)` — increments bars_since_exit, transitions COOLDOWN→IDLE after reentry_min_bars (no permanent limbo).

### Bootstrap Sequence (9 steps, fail-fast)

```
0. FATAL_STATE gate (refuse startup if unacknowledged fatal)
1. Connect MT5
2. Validate symbol contract (SymbolContract frozen)
3. Load config + risk params
4. Load/init competition context
5. Get current equity
6. Build RuntimeState
7. Orphan recovery
8. Reconcile position from broker
9. Init ExecutionRegistry
```

### SQLite Ownership

All writes through bot.py only. Tables: competition_context, governor_log, execution_log, lifecycle_events, position_layers, regime_log, signal_log, risk_log, runtime_counters, bot_state.

### FATAL_STATE Doctrine

```
Bootstrap refuses startup if FATAL_STATE present.
Clearance: python bot.py --ack-fatal "reason" (never raw SQL DELETE)
Kill switch always breaks loop even if close fails.
Unclosed position → FATAL_STATE persisted for operator.
```

### Error Budget

```
consecutive_bar_errors >= MAX_CONSECUTIVE_BAR_ERRORS (5) → kill switch
Backoff: min(errors * 1.0, 5.0) seconds between retries
Reset to 0 on successful bar completion
```

---

## PHASE 2.9 — CONFIG SURFACE

### Key Doctrines

- Credentials: env vars only (TSP_MT5_LOGIN, TSP_MT5_PASSWORD, TSP_MT5_SERVER)
- Symbol: XAUUSD-only V1 (enforced in loader)
- config_version: required field, mismatches → startup failure
- Unknown keys: strict rejection (typo guard per section)
- All frozen dataclasses, no runtime mutation

### Config File Structure

```yaml
config_version: 1
supported_symbol: "XAUUSD"

bot:        # identity, paths, operational params
risk:       # R sizing, aggression, pyramid, equity clamp
regime:     # all regime thresholds
signal:     # scoring weights, thresholds, ROC params
lifecycle:  # TP RR, BE/trail/partial triggers, orphan doctrine
execution:  # spread veto, slippage, retry, TTL
competition: # contest-specific profile
```

### Validation Gates (startup, fail-fast)

```
Risk tiers: strict monotone r_weak < r_normal < r_good < r_elite <= r_max_single
Aggression: 0 < defensive < normal, aggressive <= 2.0 cap
Kill triggers: defensive < kill (both DD and losses)
TP RR: >= 1.2 (sub-1.2 likely negative expectancy after costs)
BE < trail trigger
partial_size_ratio: (0, 1) exclusive
Spread veto > spread penalty start
Competition: budget > 0, circuit pnl negative, lead_protect < target_total
```

### Expert Knobs

```
orphan_unknown_action: ADOPT requires expert_mode: true
Otherwise default: FLATTEN
```

### Config Drift Governance

```
Fingerprint: SHA-256 of config.yaml stored in bot_state
Startup: compare fingerprint, warn on drift
Mid-competition changes: structural bugs only, fingerprint logged
Rollback: config_last_known_good.yaml, revert specific values
```

### Subsystem Access Pattern

```python
# Correct — inject specific section:
classify_regime(snap, cfg.regime)
evaluate_signals(snap, regime, aggression, runtime, cfg.signal)
evaluate_risk(signal, snap, runtime, contract, cfg.risk)

# Wrong — no global config import in subsystems
```

---

## PHASE 2.10 — VALIDATION PLAN + CONTEST OPS

### Validation Pyramid (No Skipping)

```
Config boot → Unit tests → Backtest → Forward test → Live competition
```

### Backtest Requirements

**History:** 6 months minimum, 9–12 preferred. Must include trending, chop, high vol, low vol, 3+ news shocks.

**Scenario Matrix (all three required):**

| | Scenario A | Scenario B (gate) | Scenario C |
|---|---|---|---|
| Slippage | 2 ticks | 5 ticks | 10 ticks |
| Spread | median | P95 | P99 |
| SL adverse slip | 0 | 3 ticks | 8 ticks |
| TP slip | 0 | 1 tick | 3 ticks |

**Gate: Bot must be profitable in Scenario B minimum.**

### Architecture-Relevant Metric Gates

**Primary (Scenario B):**
- Expectancy > 0.12R/trade
- Max drawdown < 12%
- Profit factor > 1.25
- Max consecutive losses < 8

**Regime sanity:**
- CHOP trade % < 5%
- Spread veto rate < 20%
- Expectancy > 0.05R in Scenario C (execution cost floor)

### Forward Test (5–7 days)

**Structural failures (zero tolerance):**
- Unresolved FATAL_STATE exposure
- Duplicate execution
- Position without SL at any point
- Equity decrease from software bug

**Recoverable anomalies (bounded):**
- Transient TIMEOUT: < 2% of fills
- TP attach deferred: < 5% of entries
- FILLED_UNVERIFIED self-resolved: < 1%

**Required: forced restart test** (kill process with open position → reconcile → no duplicate).

### Latency Targets (Forward Test)

```
Median fill latency:      < 500ms
P95 fill latency:         < 2000ms
TP attach latency:        < 300ms
Broker anomaly rate:      < 1%
If P95 > 3s: consider VPS migration before competition
```

### Deployment Checklist (T-24h)

```
□ Unit tests: 100% critical path passing
□ Backtest: all primary gates passed (Scenario B)
□ Forward test: all structural pass criteria met
□ Forced restart test: passed
□ Config boot: clean on competition account
□ VPS: clock NTP synced, RAM free, disk free > 10GB
□ Env vars: TSP_MT5_LOGIN, TSP_MT5_PASSWORD, TSP_MT5_SERVER set
□ competition_profile: tuned for actual contest duration
□ SQLite: fresh DB, FATAL_STATE absent
□ MT5: auto-reconnect enabled, chart open for XAUUSD
□ Log rotation: configured, max 100MB
□ SQLite: PRAGMA journal_mode=WAL
□ Auto-restart watchdog: configured (check PID, restart if dead)
□ Windows Update: deferred for competition duration
```

### FATAL_STATE Clearance Protocol (never raw SQL)

```
1. Read FATAL_STATE value — understand root cause
2. Log into MT5 terminal — verify all positions for magic number
3. Close any unintended exposure manually
4. python bot.py --ack-fatal "root_cause_summary"
5. Restart and monitor first 5 bars manually
```

### Manual Broker Intervention Doctrine

```
PERMITTED (bot stopped):
  - Full position flatten in MT5 terminal
  - TP attachment when bot stopped due to TP_ATTACH_ESCALATED

FORBIDDEN (bot running):
  - Any position modification
  - Manual new positions with same magic
  - Manual partial close

After any manual intervention: restart bot for reconcile
```

### Contest Ops — State-Driven Doctrine

```
State 1 (ON PACE):      Monitor only. No intervention.
State 2 (AHEAD):        Governor auto-enters PROTECT. Verify. Do not chase more.
State 3 (BEHIND):       Governor auto-enters HUNT. Verify trigger conditions.
State 4 (CRITICALLY BEHIND): Honest assessment. No panic-tuning.
State 5 (SPRINT PHASE): Do not interfere. Trust governor.

Rule: No config changes mid-competition for performance chasing.
      Only structural bug fixes permitted.
```

### VPS Resilience Checklist

```
□ RAM >= 4GB free
□ Disk >= 10GB free
□ Network latency to broker < 50ms
□ NTP synchronized (< 1s drift)
□ MT5 auto-reconnect enabled
□ Windows Update deferred
□ Auto-restart watchdog active
□ SQLite WAL mode + daily checkpoint
□ Log rotation: 100MB max, 7-day retention
```

### Broker Disconnect Playbook

```
< 2min:    Do nothing. Bot retry loop handles.
2–10min:   Verify positions in MT5. Wait for auto-reconnect.
> 10min:   Restart MT5, then restart bot after reconnect.
> 1hr:     Accept outage. Expect spread spikes on reconnect (veto handles).
Never restart MT5 while bot is running.
```

### Governor Scenario Validation (post-backtest queries)

```sql
-- HUNT effectiveness: avg expectancy in HUNT >= NORMAL
-- PROTECT discipline: trade count in PROTECT < 50% of NORMAL rate
-- SPRINT aggression: avg R committed >= normal * 1.1
-- SURVIVE behavior: consecutive losses during SURVIVE <= 2
```

### Post-Competition Analysis

```sql
-- Ticket-linked attribution (setup_id join — no fragile timestamp joins)
CREATE VIEW trade_summary AS
SELECT e.setup_id, e.ticket, s.module, s.direction, s.confidence_tier,
       r.regime_name, g.governor_state, lc.pnl_r AS realized_pnl_r
FROM execution_log e
LEFT JOIN signal_log s ON e.setup_id = s.setup_id
LEFT JOIN regime_log r ON e.setup_id = r.setup_id
LEFT JOIN governor_log g ON e.setup_id = g.setup_id
LEFT JOIN lifecycle_events lc ON e.ticket = lc.ticket AND lc.action = 'FULL_CLOSED';
```

---

## LOCKED DECISIONS SUMMARY

| Domain | Decision |
|---|---|
| Language | Python |
| Substrate | mt5_client.py existing stack |
| Main loop | M1 bar-based |
| Persistence | Hybrid SQLite + MT5 reconcile |
| Symbol | XAUUSD ONLY (V1) |
| Regimes | 4: TREND, BREAKOUT, CHOP, NEWS_DEAD |
| Modules | 2: Pullback Continuation, Breakout Momentum |
| Aggression states | 3: DEFENSIVE, NORMAL, AGGRESSIVE |
| Governor states | 5: SURVIVE, NORMAL, HUNT, PROTECT, SPRINT |
| Slope normalization | ATR-normalized (matching TF ATR) |
| Compression window | 10 bars M1 |
| Breakout | Requires prior compression + burst |
| R definition | 1R = 1% effective equity |
| Effective equity | clamp(current, floor=start×0.70, ceil=peak) |
| R sizing | WEAK=0.20, NORMAL=0.50, GOOD=0.90, ELITE=1.25, cap=1.50 |
| Pyramid | Max 2 layers, +1.0R profit trigger, 2.5R aggregate cap |
| Pyramid aggregate | Initial committed R, not mark-to-market |
| Partial exit | 1.0R trigger, 50%, fractional realized R |
| R baseline | Immutable initial_r_distance (never mutable SL) |
| Breakeven | 0.80R trigger, 3-tick buffer |
| Trail | 1.50R trigger, ATR×1.2 distance |
| TP doctrine | SL mandatory + immediate post-fill TP attach |
| TP invalid | Reject + escalate (never auto-widen) |
| No-SL orphan | Flatten + kill-switch |
| Unknown orphan | FLATTEN default (ADOPT requires expert_mode) |
| Spread veto | Hard: 2.5x baseline (execution); Soft: 1.3x (scoring) |
| Signal TTL | 90 seconds (time-based) |
| Slippage unit | Ticks (not pip) |
| Volume quantizer | Decimal ROUND_DOWN (deterministic) |
| Entry price | Real broker bid/ask |
| Dedup | TTL-based registry (PENDING/COMPLETED) |
| Partial fill | Accept, Position Manager reconciles |
| FILLED_UNVERIFIED | Distinct from TIMEOUT (position may be live) |
| Mutation doctrine | bot.py + owned helpers only |
| PositionState | Mutable aggregate with explicit mutators |
| Fatal state | --ack-fatal CLI (never raw SQL) |
| Broker day | Broker server time (not UTC) |
| Error budget | 5 consecutive errors → kill switch |
| Credentials | Env vars only (never in yaml) |
| Config version | Required, mismatches refuse startup |
| Backtest gate | Scenario B (stress fills) must be profitable |
| History depth | 6 months minimum, regime diversity required |
| TP RR floor | >= 1.2 (enforced in validation) |

---

*FINAL_BLUEPRINT.md — TSP V1 — All 10 Phases Locked*  
*Architecture sign-off: COMPLETE*  
*Ready for implementation*
