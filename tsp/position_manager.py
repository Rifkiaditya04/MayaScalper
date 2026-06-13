"""Position lifecycle manager for TSP V1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from enum import Enum
from typing import Any

from .competition import CompetitionContextDelta
from .config import BotConfig, LifecycleConfig
from .data_pipeline import SymbolContract
from .execution import BrokerExecutionAdapter
from .state import Direction, LayerState, Module, RuntimeState


SUCCESS_RETCODES = {10009, 10010}


class LifecycleAction(str, Enum):
    TP_ATTACHED = "TP_ATTACHED"
    SL_MOVED_TO_BE = "SL_MOVED_TO_BE"
    SL_TRAILED = "SL_TRAILED"
    PARTIAL_CLOSED = "PARTIAL_CLOSED"
    FULL_CLOSED = "FULL_CLOSED"
    PYRAMID_ADDED = "PYRAMID_ADDED"
    ORPHAN_RECOVERED = "ORPHAN_RECOVERED"
    TP_ATTACH_FAILED = "TP_ATTACH_FAILED"
    TP_ATTACH_ESCALATED = "TP_ATTACH_ESCALATED"
    PARTIAL_CLOSE_FAILED = "PARTIAL_CLOSE_FAILED"
    EMERGENCY_CLOSE_FAILED = "EMERGENCY_CLOSE_FAILED"


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    action: LifecycleAction
    ticket: int | None
    note: str
    timestamp: datetime
    pnl_r: float | None = None


@dataclass(frozen=True, slots=True)
class LayerMutation:
    ticket: int
    new_sl_price: float | None = None
    new_tp_price: float | None = None
    new_lot_size: float | None = None
    partial_taken: bool | None = None
    tp_attach_attempts: int | None = None


@dataclass(frozen=True, slots=True)
class LifecycleResult:
    events: tuple[LifecycleEvent, ...]
    mutations: tuple[LayerMutation, ...]
    ctx_delta: CompetitionContextDelta | None
    signal_kill: bool
    kill_reason: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _success(retcode: Any) -> bool:
    return retcode in SUCCESS_RETCODES


def _quantize_volume(raw_lot: float, contract: SymbolContract) -> float:
    step = Decimal(str(contract.volume_step))
    quantized = (
        Decimal(str(raw_lot)) / step
    ).to_integral_value(rounding=ROUND_DOWN) * step
    return float(quantized)


def _module_tp_rr(module: Module, cfg: LifecycleConfig) -> float:
    if module == Module.BREAKOUT_MOMENTUM:
        return cfg.tp_rr_breakout
    return cfg.tp_rr_pullback


def _target_tp_price(layer: LayerState, cfg: LifecycleConfig) -> float:
    distance = layer.initial_r_distance * _module_tp_rr(layer.module, cfg)
    if layer.direction == Direction.LONG:
        return layer.entry_price + distance
    return layer.entry_price - distance


def _price_distance_ok(price_a: float, price_b: float, ticks: float, contract: SymbolContract) -> bool:
    return abs(price_a - price_b) <= (ticks * contract.tick_size)


def _current_price(layer: LayerState, runtime: RuntimeState) -> float:
    snap = runtime.snap
    if snap is None:
        raise ValueError("runtime.snap is required for lifecycle evaluation")
    return snap.bid if layer.direction == Direction.LONG else snap.ask


def _be_target(layer: LayerState, cfg: LifecycleConfig, contract: SymbolContract) -> float:
    buffer_distance = cfg.be_buffer_ticks * contract.tick_size
    if layer.direction == Direction.LONG:
        return layer.entry_price + buffer_distance
    return layer.entry_price - buffer_distance


def _already_at_be(layer: LayerState, cfg: LifecycleConfig, contract: SymbolContract) -> bool:
    target = _be_target(layer, cfg, contract)
    if layer.direction == Direction.LONG:
        return layer.sl_price >= target
    return layer.sl_price <= target


def _better_sl(layer: LayerState, new_sl: float) -> bool:
    if layer.direction == Direction.LONG:
        return new_sl > layer.sl_price
    return new_sl < layer.sl_price


def _freeze_gap_ok(layer: LayerState, new_sl: float, runtime: RuntimeState, contract: SymbolContract) -> bool:
    market_price = _current_price(layer, runtime)
    required_gap = contract.freeze_level * contract.tick_size
    if layer.direction == Direction.LONG:
        return (market_price - new_sl) >= required_gap
    return (new_sl - market_price) >= required_gap


def _tp_missing_or_mismatched(
    layer: LayerState,
    broker_position: dict[str, Any] | None,
    cfg: LifecycleConfig,
    contract: SymbolContract,
) -> tuple[bool, float]:
    target_tp = _target_tp_price(layer, cfg)
    if broker_position is None:
        return True, target_tp
    broker_tp = broker_position.get("tp")
    if broker_tp is None or float(broker_tp) <= 0.0:
        return True, target_tp
    return (not _price_distance_ok(float(broker_tp), target_tp, 3.0, contract)), target_tp


def evaluate_lifecycle(
    adapter: BrokerExecutionAdapter,
    runtime: RuntimeState,
    cfg: LifecycleConfig,
    contract: SymbolContract,
) -> LifecycleResult:
    events: list[LifecycleEvent] = []
    mutations: list[LayerMutation] = []
    signal_kill = False
    kill_reason = ""

    for layer in runtime.position.layers:
        broker_position = adapter.get_position_by_ticket(layer.ticket)
        tp_missing, target_tp = _tp_missing_or_mismatched(layer, broker_position, cfg, contract)
        if tp_missing:
            distance_ticks = abs(target_tp - layer.entry_price) / contract.tick_size if contract.tick_size > 0 else 0.0
            next_attempts = layer.tp_attach_attempts + 1
            if distance_ticks < contract.stops_level:
                signal_kill = True
                kill_reason = "tp_invalid_distance"
                events.append(
                    LifecycleEvent(
                        action=LifecycleAction.TP_ATTACH_ESCALATED,
                        ticket=layer.ticket,
                        note="tp_distance_below_stops_level",
                        timestamp=_utcnow(),
                    )
                )
                mutations.append(LayerMutation(ticket=layer.ticket, tp_attach_attempts=next_attempts))
                continue

            response = adapter.modify_position(layer.ticket, layer.sl_price, target_tp)
            if _success(response.get("retcode")) and response.get("tp_confirmed", False):
                events.append(
                    LifecycleEvent(
                        action=LifecycleAction.TP_ATTACHED,
                        ticket=layer.ticket,
                        note="tp_verified",
                        timestamp=_utcnow(),
                    )
                )
                mutations.append(
                    LayerMutation(
                        ticket=layer.ticket,
                        new_tp_price=target_tp,
                        tp_attach_attempts=next_attempts,
                    )
                )
            else:
                action = (
                    LifecycleAction.TP_ATTACH_ESCALATED
                    if next_attempts >= cfg.tp_attach_retry_limit
                    else LifecycleAction.TP_ATTACH_FAILED
                )
                if action == LifecycleAction.TP_ATTACH_ESCALATED:
                    signal_kill = True
                    kill_reason = "tp_attach_escalated"
                events.append(
                    LifecycleEvent(
                        action=action,
                        ticket=layer.ticket,
                        note="tp_attach_modify_failed",
                        timestamp=_utcnow(),
                    )
                )
                mutations.append(LayerMutation(ticket=layer.ticket, tp_attach_attempts=next_attempts))
            continue

        current_price = _current_price(layer, runtime)
        unrealized_r = layer.unrealized_r(current_price)

        if unrealized_r >= cfg.be_trigger_r and not _already_at_be(layer, cfg, contract):
            target_sl = _be_target(layer, cfg, contract)
            if _better_sl(layer, target_sl) and _freeze_gap_ok(layer, target_sl, runtime, contract):
                response = adapter.modify_position(layer.ticket, target_sl, layer.tp_price)
                if _success(response.get("retcode")) and response.get("sl_confirmed", False):
                    events.append(
                        LifecycleEvent(
                            action=LifecycleAction.SL_MOVED_TO_BE,
                            ticket=layer.ticket,
                            note="be_verified",
                            timestamp=_utcnow(),
                            pnl_r=unrealized_r,
                        )
                    )
                    mutations.append(LayerMutation(ticket=layer.ticket, new_sl_price=target_sl))

        if unrealized_r >= cfg.trail_trigger_r and _already_at_be(layer, cfg, contract):
            if layer.direction == Direction.LONG:
                target_sl = current_price - (runtime.snap.atr_m1 * cfg.trail_atr_multiplier)
            else:
                target_sl = current_price + (runtime.snap.atr_m1 * cfg.trail_atr_multiplier)
            improvement_ticks = abs(target_sl - layer.sl_price) / contract.tick_size if contract.tick_size > 0 else 0.0
            if (
                _better_sl(layer, target_sl)
                and improvement_ticks >= cfg.trail_min_improve_ticks
                and _freeze_gap_ok(layer, target_sl, runtime, contract)
            ):
                response = adapter.modify_position(layer.ticket, target_sl, layer.tp_price)
                if _success(response.get("retcode")) and response.get("sl_confirmed", False):
                    events.append(
                        LifecycleEvent(
                            action=LifecycleAction.SL_TRAILED,
                            ticket=layer.ticket,
                            note="trail_verified",
                            timestamp=_utcnow(),
                            pnl_r=unrealized_r,
                        )
                    )
                    mutations.append(LayerMutation(ticket=layer.ticket, new_sl_price=target_sl))

        if unrealized_r >= cfg.partial_trigger_r and not layer.partial_taken:
            close_lot = _quantize_volume(layer.lot_size * cfg.partial_size_ratio, contract)
            remain = layer.lot_size - close_lot
            if close_lot >= contract.volume_min and remain >= contract.volume_min:
                response = adapter.partial_close(
                    layer.ticket,
                    runtime.symbol,
                    close_lot,
                    "PARTIAL_CLOSE",
                )
                volume_executed = float(response.get("volume_executed", 0.0) or 0.0)
                if _success(response.get("retcode")) and volume_executed > 0.0:
                    new_lot_size = max(0.0, layer.lot_size - volume_executed)
                    events.append(
                        LifecycleEvent(
                            action=LifecycleAction.PARTIAL_CLOSED,
                            ticket=layer.ticket,
                            note="partial_close_verified",
                            timestamp=_utcnow(),
                            pnl_r=unrealized_r * (volume_executed / layer.lot_size),
                        )
                    )
                    mutations.append(
                        LayerMutation(
                            ticket=layer.ticket,
                            new_lot_size=new_lot_size,
                            partial_taken=True,
                        )
                    )
                else:
                    events.append(
                        LifecycleEvent(
                            action=LifecycleAction.PARTIAL_CLOSE_FAILED,
                            ticket=layer.ticket,
                            note="partial_close_failed",
                            timestamp=_utcnow(),
                        )
                    )

    return LifecycleResult(
        events=tuple(events),
        mutations=tuple(mutations),
        ctx_delta=None,
        signal_kill=signal_kill,
        kill_reason=kill_reason,
    )


def recover_orphans(
    broker_positions: list[dict[str, Any]],
    *,
    known_tickets: set[int],
    bot_cfg: BotConfig,
    lifecycle_cfg: LifecycleConfig,
) -> LifecycleResult:
    events: list[LifecycleEvent] = []
    signal_kill = False
    kill_reason = ""

    for position in broker_positions:
        ticket = int(position.get("ticket", 0) or 0)
        sl = float(position.get("sl", 0.0) or 0.0)
        tp = float(position.get("tp", 0.0) or 0.0)
        if sl <= 0.0:
            signal_kill = True
            kill_reason = "orphan_no_sl"
            events.append(
                LifecycleEvent(
                    action=LifecycleAction.ORPHAN_RECOVERED,
                    ticket=ticket,
                    note="flatten_no_sl_orphan",
                    timestamp=_utcnow(),
                )
            )
            continue
        if ticket not in known_tickets:
            if lifecycle_cfg.orphan_unknown_action == "ADOPT" and bot_cfg.expert_mode:
                events.append(
                    LifecycleEvent(
                        action=LifecycleAction.ORPHAN_RECOVERED,
                        ticket=ticket,
                        note="adopt_unknown_orphan",
                        timestamp=_utcnow(),
                    )
                )
            else:
                events.append(
                    LifecycleEvent(
                        action=LifecycleAction.ORPHAN_RECOVERED,
                        ticket=ticket,
                        note="flatten_unknown_orphan",
                        timestamp=_utcnow(),
                    )
                )
        elif tp <= 0.0:
            events.append(
                LifecycleEvent(
                    action=LifecycleAction.ORPHAN_RECOVERED,
                    ticket=ticket,
                    note="adopt_sl_only_missing_tp",
                    timestamp=_utcnow(),
                )
            )

    return LifecycleResult(
        events=tuple(events),
        mutations=tuple(),
        ctx_delta=None,
        signal_kill=signal_kill,
        kill_reason=kill_reason,
    )


__all__ = [
    "LayerMutation",
    "LifecycleAction",
    "LifecycleEvent",
    "LifecycleResult",
    "evaluate_lifecycle",
    "recover_orphans",
]
