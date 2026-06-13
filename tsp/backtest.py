"""Historical replay adapter and runner for TSP V1."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from math import floor
from statistics import mean
from typing import Any, Sequence

from .bot import TSPBot
from .config import AppConfig
from .data_pipeline import SnapshotBuildConfig


TIMEFRAME_MINUTES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "H1": 60,
}
SUCCESS_RETCODE = 10009
PARTIAL_FILL_RETCODE = 10010
REJECT_RETCODE = 10030
EPSILON = 1e-9


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _record_value(record: Any, key: str) -> Any:
    if isinstance(record, dict):
        return record[key]
    return getattr(record, key)


def _normalize_m1_bars(records: Sequence[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for record in records:
        timestamp_raw = _record_value(record, "time")
        if isinstance(timestamp_raw, datetime):
            timestamp = _to_utc(timestamp_raw)
        else:
            timestamp = datetime.fromtimestamp(float(timestamp_raw), tz=timezone.utc)
        normalized.append(
            {
                "time": timestamp,
                "open": float(_record_value(record, "open")),
                "high": float(_record_value(record, "high")),
                "low": float(_record_value(record, "low")),
                "close": float(_record_value(record, "close")),
                "tick_volume": float(
                    _record_value(record, "tick_volume")
                    if (
                        isinstance(record, dict) and "tick_volume" in record
                    ) or hasattr(record, "tick_volume")
                    else _record_value(record, "real_volume")
                ),
            }
        )
    if not normalized:
        raise ValueError("Backtest replay requires at least one M1 bar")
    normalized.sort(key=lambda item: item["time"])
    return normalized


def _aggregate_bars(m1_bars: Sequence[dict[str, Any]], timeframe: str) -> list[dict[str, Any]]:
    minutes = TIMEFRAME_MINUTES[timeframe]
    if timeframe == "M1":
        return list(m1_bars)
    step = minutes
    aggregated: list[dict[str, Any]] = []
    for start in range(0, len(m1_bars), step):
        chunk = m1_bars[start : start + step]
        if len(chunk) < step:
            continue
        aggregated.append(
            {
                "time": chunk[-1]["time"],
                "open": chunk[0]["open"],
                "high": max(bar["high"] for bar in chunk),
                "low": min(bar["low"] for bar in chunk),
                "close": chunk[-1]["close"],
                "tick_volume": sum(bar["tick_volume"] for bar in chunk),
            }
        )
    return aggregated


@dataclass(frozen=True, slots=True)
class BacktestSymbolInfo:
    digits: int = 2
    point: float = 0.01
    spread: int = 18
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    trade_tick_size: float = 0.01
    trade_tick_value: float = 1.0
    trade_stops_level: int = 30
    trade_freeze_level: int = 10


@dataclass(frozen=True, slots=True)
class BacktestExecutionModel:
    entry_slippage_ticks: float = 1.0
    exit_slippage_ticks: float = 1.0
    spread_multiplier: float = 1.0
    partial_fill_every: int = 0
    partial_fill_ratio: float = 0.5
    reject_every: int = 0
    latency_ms: float = 150.0
    allow_tp_modify: bool = True

    def __post_init__(self) -> None:
        if self.entry_slippage_ticks < 0.0:
            raise ValueError("entry_slippage_ticks must be non-negative")
        if self.exit_slippage_ticks < 0.0:
            raise ValueError("exit_slippage_ticks must be non-negative")
        if self.spread_multiplier <= 0.0:
            raise ValueError("spread_multiplier must be positive")
        if self.partial_fill_every < 0:
            raise ValueError("partial_fill_every must be non-negative")
        if not 0.0 < self.partial_fill_ratio <= 1.0:
            raise ValueError("partial_fill_ratio must be in (0, 1]")
        if self.reject_every < 0:
            raise ValueError("reject_every must be non-negative")
        if self.latency_ms < 0.0:
            raise ValueError("latency_ms must be non-negative")


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    ticket: int
    symbol: str
    side: str
    volume: float
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    pnl_cash: float
    exit_reason: str


@dataclass(frozen=True, slots=True)
class BacktestReport:
    bars_processed: int
    signals_generated: int
    executions_attempted: int
    executions_filled: int
    execution_rejections: int
    closed_trades: tuple[BacktestTrade, ...]
    final_equity: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float | None
    expectancy_cash: float
    assumptions: dict[str, Any]


@dataclass(slots=True)
class BacktestAdapter:
    symbol: str
    m1_bars: Sequence[Any]
    starting_equity: float = 10_000.0
    symbol_info: BacktestSymbolInfo = field(default_factory=BacktestSymbolInfo)
    execution_model: BacktestExecutionModel = field(default_factory=BacktestExecutionModel)
    current_index: int = 0
    _m1: list[dict[str, Any]] = field(init=False, repr=False)
    _timeframe_bars: dict[str, list[dict[str, Any]]] = field(init=False, repr=False)
    _positions: dict[int, dict[str, Any]] = field(init=False, repr=False)
    _closed_trades: list[BacktestTrade] = field(init=False, repr=False)
    _next_ticket: int = field(init=False, repr=False)
    _order_count: int = field(init=False, repr=False)
    _peak_equity: float = field(init=False, repr=False)
    _max_drawdown_pct: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._m1 = _normalize_m1_bars(self.m1_bars)
        self._timeframe_bars = {
            "M1": list(self._m1),
            "M5": _aggregate_bars(self._m1, "M5"),
            "M15": _aggregate_bars(self._m1, "M15"),
            "H1": _aggregate_bars(self._m1, "H1"),
        }
        self._positions: dict[int, dict[str, Any]] = {}
        self._closed_trades: list[BacktestTrade] = []
        self._next_ticket = 10_000
        self._order_count = 0
        self._peak_equity = float(self.starting_equity)
        self._max_drawdown_pct = 0.0
        self.current_index = max(0, min(self.current_index, len(self._m1) - 1))

    @property
    def closed_trades(self) -> tuple[BacktestTrade, ...]:
        return tuple(self._closed_trades)

    @property
    def last_timestamp(self) -> datetime:
        return self._m1[self.current_index]["time"]

    def has_data_for_index(self, index: int) -> bool:
        return 0 <= index < len(self._m1)

    def seek(self, index: int) -> None:
        if not self.has_data_for_index(index):
            raise IndexError(f"Backtest index out of range: {index}")
        self.current_index = index

    def advance(self) -> bool:
        if self.current_index + 1 >= len(self._m1):
            return False
        self.current_index += 1
        self._update_drawdown()
        return True

    def get_rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, Any]]:
        if symbol != self.symbol:
            raise ValueError(f"Unsupported symbol requested: {symbol}")
        if timeframe not in self._timeframe_bars:
            raise ValueError(f"Unsupported timeframe requested: {timeframe}")
        if count <= 0:
            raise ValueError("count must be positive")
        current_time = self.get_server_time()
        series = [
            bar
            for bar in self._timeframe_bars[timeframe]
            if bar["time"] <= current_time
        ]
        if len(series) < count:
            raise ValueError(
                f"Not enough historical bars for {timeframe}: need {count}, have {len(series)}"
            )
        return series[-count:]

    def get_latest_tick(self, symbol: str):
        if symbol != self.symbol:
            raise ValueError(f"Unsupported symbol requested: {symbol}")
        bar = self._m1[self.current_index]
        midpoint = float(bar["close"])
        spread = self.symbol_info.spread * self.symbol_info.point * self.execution_model.spread_multiplier
        bid = midpoint - (spread / 2.0)
        ask = midpoint + (spread / 2.0)
        return type("BacktestTick", (), {"bid": bid, "ask": ask, "time": self.last_timestamp.timestamp()})()

    def get_symbol_info(self, symbol: str) -> BacktestSymbolInfo:
        if symbol != self.symbol:
            raise ValueError(f"Unsupported symbol requested: {symbol}")
        return self.symbol_info

    def get_server_time(self) -> datetime:
        return self.last_timestamp

    def get_equity(self) -> float:
        equity = self.starting_equity + self._realized_pnl() + self._unrealized_pnl()
        self._peak_equity = max(self._peak_equity, equity)
        if self._peak_equity > 0:
            dd_pct = ((self._peak_equity - equity) / self._peak_equity) * 100.0
            self._max_drawdown_pct = max(self._max_drawdown_pct, dd_pct)
        return equity

    def send_market_order(
        self,
        symbol: str,
        action: str,
        volume: float,
        sl: float,
        tp: float | None,
        comment: str,
        magic: int,
    ) -> dict[str, Any]:
        del comment
        if symbol != self.symbol:
            raise ValueError(f"Unsupported symbol requested: {symbol}")
        self._order_count += 1
        if self.execution_model.reject_every > 0 and self._order_count % self.execution_model.reject_every == 0:
            return {"retcode": REJECT_RETCODE, "order": None, "deal": None, "price": None, "volume": None}

        tick = self.get_latest_tick(symbol)
        tick_size = self.symbol_info.trade_tick_size
        slippage = self.execution_model.entry_slippage_ticks * tick_size
        if action == "BUY":
            fill_price = tick.ask + slippage
            side = "LONG"
        else:
            fill_price = tick.bid - slippage
            side = "SHORT"

        filled_volume = self._quantize_volume(volume)
        retcode = SUCCESS_RETCODE
        if (
            self.execution_model.partial_fill_every > 0
            and self._order_count % self.execution_model.partial_fill_every == 0
        ):
            retcode = PARTIAL_FILL_RETCODE
            filled_volume = self._quantize_volume(volume * self.execution_model.partial_fill_ratio)

        ticket = self._next_ticket
        self._next_ticket += 1
        self._positions[ticket] = {
            "ticket": ticket,
            "symbol": symbol,
            "type": side,
            "volume": filled_volume,
            "price_open": fill_price,
            "sl": sl,
            "tp": tp or 0.0,
            "magic": magic,
            "time": self.get_server_time(),
        }
        return {
            "retcode": retcode,
            "order": ticket,
            "deal": ticket,
            "price": fill_price,
            "volume": filled_volume,
            "latency_ms": self.execution_model.latency_ms,
        }

    def modify_position(self, ticket: int, sl: float | None, tp: float | None) -> dict[str, Any]:
        position = self._positions.get(ticket)
        if position is None:
            return {"retcode": REJECT_RETCODE, "sl_confirmed": False, "tp_confirmed": False}
        if sl is not None:
            position["sl"] = sl
        if tp is not None and self.execution_model.allow_tp_modify:
            position["tp"] = tp
        return {"retcode": SUCCESS_RETCODE, "sl_confirmed": True, "tp_confirmed": True}

    def partial_close(self, ticket: int, symbol: str, volume: float, comment: str) -> dict[str, Any]:
        del comment
        position = self._positions.get(ticket)
        if position is None or symbol != self.symbol:
            return {"retcode": REJECT_RETCODE, "volume_executed": 0.0}
        close_volume = min(float(volume), float(position["volume"]))
        if close_volume <= EPSILON:
            return {"retcode": REJECT_RETCODE, "volume_executed": 0.0}
        exit_price = self._exit_price_for_side(str(position["type"]))
        self._record_closed_trade(position, close_volume, exit_price, exit_reason="partial_close")
        position["volume"] = self._quantize_volume(float(position["volume"]) - close_volume)
        if position["volume"] <= EPSILON:
            self._positions.pop(ticket, None)
        return {"retcode": SUCCESS_RETCODE, "volume_executed": close_volume}

    def get_position_by_ticket(self, ticket: int) -> dict[str, Any] | None:
        position = self._positions.get(ticket)
        return dict(position) if position is not None else None

    def get_all_positions(self, magic: int) -> list[dict[str, Any]]:
        return [
            dict(position)
            for position in self._positions.values()
            if int(position["magic"]) == magic
        ]

    def emergency_close(self, ticket: int, symbol: str, volume: float, reason: str) -> dict[str, Any]:
        position = self._positions.get(ticket)
        if position is None or symbol != self.symbol:
            return {"retcode": REJECT_RETCODE}
        close_volume = min(float(volume), float(position["volume"]))
        if close_volume <= EPSILON:
            return {"retcode": REJECT_RETCODE}
        exit_price = self._exit_price_for_side(str(position["type"]))
        self._record_closed_trade(position, close_volume, exit_price, exit_reason=reason)
        remaining = self._quantize_volume(float(position["volume"]) - close_volume)
        if remaining <= EPSILON:
            self._positions.pop(ticket, None)
        else:
            position["volume"] = remaining
        return {"retcode": SUCCESS_RETCODE}

    def build_report(
        self,
        *,
        bars_processed: int,
        signals_generated: int,
        executions_attempted: int,
        executions_filled: int,
        execution_rejections: int,
    ) -> BacktestReport:
        closed = tuple(self._closed_trades)
        wins = [trade for trade in closed if trade.pnl_cash > 0.0]
        losses = [trade for trade in closed if trade.pnl_cash < 0.0]
        gross_profit = sum(trade.pnl_cash for trade in wins)
        gross_loss = abs(sum(trade.pnl_cash for trade in losses))
        profit_factor = None if gross_loss <= EPSILON else gross_profit / gross_loss
        win_rate = 0.0 if not closed else len(wins) / len(closed)
        expectancy_cash = 0.0 if not closed else mean(trade.pnl_cash for trade in closed)
        assumptions = {
            "data_model": "historical_replay_from_m1",
            "tick_model": "close_midpoint_plus_configured_spread",
            "latency_model": "constant_ms",
            "execution_model": asdict(self.execution_model),
            "auto_broker_tp_sl": False,
        }
        return BacktestReport(
            bars_processed=bars_processed,
            signals_generated=signals_generated,
            executions_attempted=executions_attempted,
            executions_filled=executions_filled,
            execution_rejections=execution_rejections,
            closed_trades=closed,
            final_equity=self.get_equity(),
            max_drawdown_pct=self._max_drawdown_pct,
            win_rate=win_rate,
            profit_factor=profit_factor,
            expectancy_cash=expectancy_cash,
            assumptions=assumptions,
        )

    def _realized_pnl(self) -> float:
        return sum(trade.pnl_cash for trade in self._closed_trades)

    def _unrealized_pnl(self) -> float:
        total = 0.0
        tick = self.get_latest_tick(self.symbol)
        for position in self._positions.values():
            current_price = tick.bid if position["type"] == "LONG" else tick.ask
            total += self._cash_pnl(
                side=str(position["type"]),
                entry_price=float(position["price_open"]),
                exit_price=float(current_price),
                volume=float(position["volume"]),
            )
        return total

    def _exit_price_for_side(self, side: str) -> float:
        tick = self.get_latest_tick(self.symbol)
        slippage = self.execution_model.exit_slippage_ticks * self.symbol_info.trade_tick_size
        if side == "LONG":
            return tick.bid - slippage
        return tick.ask + slippage

    def _record_closed_trade(
        self,
        position: dict[str, Any],
        volume: float,
        exit_price: float,
        *,
        exit_reason: str,
    ) -> None:
        self._closed_trades.append(
            BacktestTrade(
                ticket=int(position["ticket"]),
                symbol=str(position["symbol"]),
                side=str(position["type"]),
                volume=float(volume),
                entry_time=_to_utc(position["time"]),
                exit_time=self.get_server_time(),
                entry_price=float(position["price_open"]),
                exit_price=float(exit_price),
                pnl_cash=self._cash_pnl(
                    side=str(position["type"]),
                    entry_price=float(position["price_open"]),
                    exit_price=float(exit_price),
                    volume=float(volume),
                ),
                exit_reason=exit_reason,
            )
        )

    def _cash_pnl(self, *, side: str, entry_price: float, exit_price: float, volume: float) -> float:
        ticks = (exit_price - entry_price) / self.symbol_info.trade_tick_size
        if side != "LONG":
            ticks = -ticks
        return ticks * self.symbol_info.trade_tick_value * volume

    def _quantize_volume(self, volume: float) -> float:
        step = self.symbol_info.volume_step
        raw = max(self.symbol_info.volume_min, min(self.symbol_info.volume_max, volume))
        units = floor((raw / step) + EPSILON)
        return round(units * step, 8)

    def _update_drawdown(self) -> None:
        self.get_equity()


@dataclass(slots=True)
class BacktestRunner:
    config: AppConfig
    adapter: BacktestAdapter
    snapshot_config: SnapshotBuildConfig = field(default_factory=SnapshotBuildConfig)

    def run(self, *, max_steps: int | None = None) -> BacktestReport:
        bot = TSPBot(config=self.config, adapter=self.adapter, snapshot_config=self.snapshot_config)
        warmup_index = self._warmup_index()
        if not self.adapter.has_data_for_index(warmup_index):
            raise ValueError(
                f"Historical replay is too short for snapshot warmup; need index {warmup_index}"
            )
        self.adapter.seek(warmup_index)

        bars_processed = 0
        signals_generated = 0
        executions_attempted = 0
        executions_filled = 0
        execution_rejections = 0

        while True:
            result = bot.process_bar()
            bars_processed += 1
            signals_generated += int(result.signal_generated)
            if result.execution_status is not None:
                executions_attempted += 1
                if result.executed:
                    executions_filled += 1
                elif result.execution_status not in {"BLOCKED", "BUDGET_BLOCKED"}:
                    execution_rejections += 1
            if max_steps is not None and bars_processed >= max_steps:
                break
            if not self.adapter.advance():
                break

        return self.adapter.build_report(
            bars_processed=bars_processed,
            signals_generated=signals_generated,
            executions_attempted=executions_attempted,
            executions_filled=executions_filled,
            execution_rejections=execution_rejections,
        )

    def _warmup_index(self) -> int:
        requirements = (
            self.snapshot_config.m1_bars - 1,
            (self.snapshot_config.m5_bars * TIMEFRAME_MINUTES["M5"]) - 1,
            (self.snapshot_config.m15_bars * TIMEFRAME_MINUTES["M15"]) - 1,
            (self.snapshot_config.h1_bars * TIMEFRAME_MINUTES["H1"]) - 1,
        )
        return max(requirements)


__all__ = [
    "BacktestAdapter",
    "BacktestExecutionModel",
    "BacktestReport",
    "BacktestRunner",
    "BacktestSymbolInfo",
    "BacktestTrade",
]
