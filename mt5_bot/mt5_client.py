"""Wrapper MT5 dengan fokus broker reality."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import importlib
import logging
import math
import re
import time
from typing import Any

from mt5_bot.config import Settings


TIMEFRAME_MAP: dict[str, str] = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
}


@dataclass(slots=True)
class SymbolConstraints:
    symbol: str
    digits: int
    point: float
    spread_points: int
    spread_price: float
    volume_min: float
    volume_max: float
    volume_step: float
    trade_stops_level: int
    min_stop_distance: float
    filling_mode: int | None


@dataclass(slots=True)
class OrderAttempt:
    filling_mode: int | None
    retcode: int | None
    order: int | None
    deal: int | None
    price: float | None
    volume: float | None
    comment: str


@dataclass(slots=True)
class OrderResult:
    ok: bool
    order_ticket: int | None
    position_ticket: int | None
    retcode: int | None
    message: str
    fill_price: float | None = None
    filled_volume: float | None = None
    attempts: list[OrderAttempt] = field(default_factory=list)


class TradeFailureClass(str, Enum):
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class ProtectionResult:
    ok: bool
    retcode: int | None
    message: str
    tp_attached: float | None = None
    sl_attached: float | None = None
    failure_class: TradeFailureClass | None = None
    retryable: bool = False


@dataclass(slots=True)
class CloseResult:
    ok: bool
    ticket: int
    symbol: str
    position_side: str
    close_side: str
    price: float
    filling_mode: int | None
    retcode: int | None
    comment: str
    failure_class: TradeFailureClass | None = None
    retryable: bool = False


class MT5Client:
    MAX_MT5_COMMENT_LEN = 16
    _BROKER_COMMENT_MAP = {
        "ENTRY": "ENTRY",
        "SET_TP_AFTER_ENTRY": "TP_ATTACH",
        "SET_PROTECTION": "PROTECT",
        "SET_BREAK_EVEN": "SET_BE",
        "BOT_CLOSE": "BOT_CLOSE",
        "hard_drawdown_guard": "DD_FLAT",
        "unprotected_entry_recovery": "RECOVER",
        "startup_orphan_recovery": "ORPHAN",
        "progress_below_50pct_after_2_m5": "EXIT_P50",
        "back_to_entry_area_after_2_m5": "EXIT_BE",
        "operator_resume_after_review": "MAN_ACK",
    }

    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger.getChild("mt5_client")
        self._mt5: Any | None = None
        self._initialized = False
        self._retryable_trade_retcode_names = (
            "TRADE_RETCODE_REQUOTE",
            "TRADE_RETCODE_PRICE_CHANGED",
            "TRADE_RETCODE_PRICE_OFF",
            "TRADE_RETCODE_TIMEOUT",
            "TRADE_RETCODE_CONNECTION",
            "TRADE_RETCODE_TOO_MANY_REQUESTS",
            "TRADE_RETCODE_TRADE_CONTEXT_BUSY",
            "TRADE_RETCODE_SERVER_DISABLES_AT",
            "TRADE_RETCODE_CLIENT_DISABLES_AT",
        )
        self._non_retryable_trade_retcode_names = (
            "TRADE_RETCODE_INVALID_STOPS",
            "TRADE_RETCODE_INVALID_PRICE",
            "TRADE_RETCODE_INVALID_VOLUME",
            "TRADE_RETCODE_MARKET_CLOSED",
            "TRADE_RETCODE_TRADE_DISABLED",
            "TRADE_RETCODE_NO_MONEY",
        )

    @property
    def mt5(self) -> Any:
        if self._mt5 is None:
            try:
                self._mt5 = importlib.import_module("MetaTrader5")
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "Package MetaTrader5 belum terpasang di environment Eagle."
                ) from exc
        return self._mt5

    def initialize(self) -> None:
        if self._initialized:
            return

        mt5 = self.mt5
        kwargs: dict[str, Any] = {}
        if self.settings.terminal_path:
            kwargs["path"] = self.settings.terminal_path

        initialized = mt5.initialize(**kwargs)
        if not initialized:
            raise RuntimeError(
                f"MT5 initialize failed. last_error={mt5.last_error()}"
            )

        self.logger.info("MT5 initialized successfully")

        if self.settings.login and self.settings.password and self.settings.server:
            login_ok = mt5.login(
                login=int(self.settings.login),
                password=self.settings.password,
                server=self.settings.server,
            )
            if not login_ok:
                raise RuntimeError(
                    "MT5 login failed. Check MT5_LOGIN, MT5_PASSWORD, and MT5_SERVER in .env. "
                    f"last_error={mt5.last_error()}"
                )
            self.logger.info(
                "MT5 login attached | login=%s | server=%s",
                self.settings.login,
                self.settings.server,
            )
        else:
            self.logger.info(
                "MT5 login fields kosong, client akan memakai session terminal yang sedang aktif."
            )

        self.ensure_symbol_selected(self.settings.symbol)
        self._initialized = True

    def shutdown(self) -> None:
        if self._mt5 is not None:
            try:
                self.mt5.shutdown()
            finally:
                self._initialized = False

    def get_account_info(self):
        info = self.mt5.account_info()
        if info is None:
            raise RuntimeError(
                f"Unable to fetch account info from MT5. last_error={self.mt5.last_error()}"
            )
        return info

    def ensure_symbol_selected(self, symbol: str) -> None:
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(
                f"Symbol not found in MT5: {symbol}. last_error={self.mt5.last_error()}"
            )

        if not info.visible:
            if not self.mt5.symbol_select(symbol, True):
                raise RuntimeError(
                    f"Failed to select symbol {symbol}. last_error={self.mt5.last_error()}"
                )
            self.logger.info("Symbol selected in Market Watch | symbol=%s", symbol)

    def get_symbol_info(self, symbol: str):
        self.ensure_symbol_selected(symbol)
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(
                f"Symbol info unavailable for {symbol}. last_error={self.mt5.last_error()}"
            )
        return info

    def get_symbol_constraints(self, symbol: str) -> SymbolConstraints:
        info = self.get_symbol_info(symbol)
        point = float(info.point)
        spread_points = int(getattr(info, "spread", 0) or 0)
        return SymbolConstraints(
            symbol=symbol,
            digits=int(info.digits),
            point=point,
            spread_points=spread_points,
            spread_price=spread_points * point,
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
            trade_stops_level=int(getattr(info, "trade_stops_level", 0) or 0),
            min_stop_distance=int(getattr(info, "trade_stops_level", 0) or 0) * point,
            filling_mode=getattr(info, "filling_mode", None),
        )

    def get_rates(self, symbol: str, timeframe: str, count: int):
        mapped = TIMEFRAME_MAP.get(timeframe.upper())
        if mapped is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        mt5_timeframe = getattr(self.mt5, mapped)
        rates = self.mt5.copy_rates_from_pos(symbol, mt5_timeframe, 0, count)
        if rates is None:
            raise RuntimeError(
                f"Unable to fetch rates for {symbol} {timeframe}. last_error={self.mt5.last_error()}"
            )
        return rates

    def positions_get(self, symbol: str | None = None, ticket: int | None = None):
        if ticket is not None:
            positions = self.mt5.positions_get(ticket=ticket)
        elif symbol is not None:
            positions = self.mt5.positions_get(symbol=symbol)
        else:
            positions = self.mt5.positions_get()
        if positions is None:
            raise RuntimeError(
                f"positions_get failed. last_error={self.mt5.last_error()}"
            )
        return list(positions)

    def classify_trade_retcode(self, retcode: int | None) -> TradeFailureClass:
        if retcode is None:
            return TradeFailureClass.UNKNOWN

        retryable_codes = {
            getattr(self.mt5, name)
            for name in self._retryable_trade_retcode_names
            if hasattr(self.mt5, name)
        }
        non_retryable_codes = {
            getattr(self.mt5, name)
            for name in self._non_retryable_trade_retcode_names
            if hasattr(self.mt5, name)
        }

        if retcode in retryable_codes:
            return TradeFailureClass.RETRYABLE
        if retcode in non_retryable_codes:
            return TradeFailureClass.NON_RETRYABLE
        return TradeFailureClass.UNKNOWN

    @staticmethod
    def _is_retryable_failure(failure_class: TradeFailureClass) -> bool:
        return failure_class == TradeFailureClass.RETRYABLE

    def _normalize_broker_comment(
        self, raw_comment: str | None, *, fallback: str = "GENERIC"
    ) -> str:
        fallback_clean = self._sanitize_comment_token(fallback) or "GENERIC"
        fallback_clean = fallback_clean[: self.MAX_MT5_COMMENT_LEN]

        raw_value = (raw_comment or "").strip()
        mapped = self._BROKER_COMMENT_MAP.get(raw_value)
        if mapped:
            normalized = mapped
        else:
            normalized = self._sanitize_comment_token(raw_value)
            if not normalized:
                normalized = fallback_clean
            elif len(normalized) > self.MAX_MT5_COMMENT_LEN:
                normalized = fallback_clean

        normalized = normalized[: self.MAX_MT5_COMMENT_LEN] or fallback_clean
        if raw_value != normalized:
            self.logger.debug(
                'event="broker_comment_normalized" raw_reason=%r broker_comment=%s fallback=%s',
                raw_value,
                normalized,
                fallback_clean,
            )
        return normalized

    @staticmethod
    def _sanitize_comment_token(raw_comment: str) -> str:
        ascii_only = raw_comment.encode("ascii", errors="ignore").decode("ascii")
        collapsed = re.sub(r"[^A-Za-z0-9_]+", "_", ascii_only.strip())
        collapsed = re.sub(r"_+", "_", collapsed).strip("_")
        return collapsed.upper()


    def get_latest_tick(self, symbol: str):
        tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(
                f"symbol_info_tick failed for {symbol}. last_error={self.mt5.last_error()}"
            )
        return tick

    def get_server_time(self) -> datetime:
        tick = self.get_latest_tick(self.settings.symbol)
        tick_time = getattr(tick, "time", None)
        if tick_time is None:
            raise RuntimeError("Latest tick does not expose server time")
        return datetime.fromtimestamp(float(tick_time), tz=timezone.utc)

    def send_market_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        comment: str = "ENTRY",
    ) -> OrderResult:
        side_upper = side.upper()
        constraints = self.get_symbol_constraints(symbol)
        normalized_volume = self._normalize_volume(volume, constraints)
        tick = self.get_latest_tick(symbol)

        order_type = (
            self.mt5.ORDER_TYPE_BUY
            if side_upper == "BUY"
            else self.mt5.ORDER_TYPE_SELL
        )
        price = float(tick.ask if side_upper == "BUY" else tick.bid)

        broker_comment = self._normalize_broker_comment(comment, fallback="ENTRY")
        base_request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "magic": self.settings.magic_number,
            "symbol": symbol,
            "volume": normalized_volume,
            "type": order_type,
            "price": price,
            "deviation": self.settings.order_deviation_points,
            "type_time": self.mt5.ORDER_TIME_GTC,
            "sl": 0.0,
            "tp": 0.0,
            "comment": broker_comment,
        }

        self.logger.info(
            "ORDER DEBUG | side=%s symbol=%s volume=%.2f broker_tp=0.00000 price=%.5f",
            side_upper,
            symbol,
            normalized_volume,
            price,
        )

        attempts: list[OrderAttempt] = []
        successful_result = None
        for filling_mode in self._candidate_filling_modes(symbol):
            request = {**base_request, "type_filling": filling_mode}
            result = self.mt5.order_send(request)
            attempt = OrderAttempt(
                filling_mode=filling_mode,
                retcode=getattr(result, "retcode", None),
                order=getattr(result, "order", None),
                deal=getattr(result, "deal", None),
                price=getattr(result, "price", None),
                volume=getattr(result, "volume", None),
                comment=getattr(result, "comment", "") if result is not None else "",
            )
            attempts.append(attempt)

            if result is None:
                self.logger.warning(
                    "Order send returned None | filling=%s last_error=%s",
                    filling_mode,
                    self.mt5.last_error(),
                )
                continue

            if result.retcode == self.mt5.TRADE_RETCODE_DONE:
                successful_result = result
                break

            self.logger.warning(
                "Order rejected by filling mode | filling=%s retcode=%s comment=%s",
                filling_mode,
                result.retcode,
                getattr(result, "comment", ""),
            )

        if successful_result is None:
            last_attempt = attempts[-1] if attempts else None
            return OrderResult(
                ok=False,
                order_ticket=None,
                position_ticket=None,
                retcode=last_attempt.retcode if last_attempt else None,
                message="order_send failed for all filling modes",
                attempts=attempts,
            )

        position_ticket = self._resolve_live_position_ticket(
            symbol=symbol,
            side=side_upper,
            volume=normalized_volume,
            magic=self.settings.magic_number,
            order_ticket=getattr(successful_result, "order", None),
        )

        self.logger.info(
            "BOT POSITION placed | side=%s symbol=%s order=%s position=%s volume=%.2f fill_price=%.5f",
            side_upper,
            symbol,
            getattr(successful_result, "order", None),
            position_ticket,
            float(getattr(successful_result, "volume", normalized_volume) or normalized_volume),
            float(getattr(successful_result, "price", price) or price),
        )

        return OrderResult(
            ok=True,
            order_ticket=getattr(successful_result, "order", None),
            position_ticket=position_ticket,
            retcode=getattr(successful_result, "retcode", None),
            message="order_send success",
            fill_price=float(getattr(successful_result, "price", price) or price),
            filled_volume=float(
                getattr(successful_result, "volume", normalized_volume) or normalized_volume
            ),
            attempts=attempts,
        )

    def attach_tp_after_entry(
        self,
        symbol: str,
        side: str,
        position_ticket: int,
        fill_price: float,
        target_distance: float,
        sl: float = 0.0,
        comment: str = "SET_TP_AFTER_ENTRY",
    ) -> ProtectionResult:
        constraints = self.get_symbol_constraints(symbol)
        computed_tp_result = self._build_valid_tp_from_fill(
            symbol=symbol,
            side=side,
            fill_price=fill_price,
            target_distance=target_distance,
            constraints=constraints,
        )
        if not computed_tp_result.ok:
            self.logger.error(
                'event="protection_rejected_invalid_after_fill" ticket=%s symbol=%s side=%s fill_price=%.5f target_distance=%.5f min_stop_distance=%.5f reason=%s',
                position_ticket,
                symbol,
                side,
                fill_price,
                target_distance,
                constraints.min_stop_distance,
                computed_tp_result.message,
            )
            return computed_tp_result

        tp = computed_tp_result.tp_attached or 0.0
        attempts = max(1, self.settings.protection_attach_retry_count)
        delay_seconds = max(0, self.settings.protection_attach_retry_delay_seconds)
        last_result: ProtectionResult | None = None
        for attempt in range(1, attempts + 1):
            result = self.modify_position_protection(
                symbol=symbol,
                position_ticket=position_ticket,
                sl=sl,
                tp=tp,
                comment=comment,
            )
            if result.ok:
                return result
            last_result = result
            self.logger.warning(
                'event="protection_attach_failed" ticket=%s attempt=%s/%s retcode=%s classification=%s comment=%s fill_price=%.5f requested_tp=%.5f min_stop_distance=%.5f retryable=%s',
                position_ticket,
                attempt,
                attempts,
                result.retcode,
                result.failure_class.value if result.failure_class else TradeFailureClass.UNKNOWN.value,
                result.message,
                fill_price,
                tp,
                constraints.min_stop_distance,
                result.retryable,
            )
            if attempt < attempts and result.retryable:
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue
            break
        return last_result or ProtectionResult(
            ok=False,
            retcode=None,
            message="TP attach failed without result",
            failure_class=TradeFailureClass.UNKNOWN,
            retryable=False,
        )

    def modify_position_protection(
        self,
        symbol: str,
        position_ticket: int,
        sl: float = 0.0,
        tp: float = 0.0,
        comment: str = "SET_PROTECTION",
    ) -> ProtectionResult:
        broker_comment = self._normalize_broker_comment(comment, fallback="PROTECT")
        request = {
            "action": self.mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": position_ticket,
            "sl": sl,
            "tp": tp,
            "comment": broker_comment,
        }
        result = self.mt5.order_send(request)

        if result is None:
            return ProtectionResult(
                ok=False,
                retcode=None,
                message=f"modify protection returned None. last_error={self.mt5.last_error()}",
                failure_class=TradeFailureClass.UNKNOWN,
                retryable=False,
            )

        if result.retcode != self.mt5.TRADE_RETCODE_DONE:
            failure_class = self.classify_trade_retcode(result.retcode)
            self.logger.warning(
                "ENTRY OK BUT TP ATTACH FAILED | ticket=%s retcode=%s class=%s comment=%s",
                position_ticket,
                result.retcode,
                failure_class.value,
                getattr(result, "comment", ""),
            )
            return ProtectionResult(
                ok=False,
                retcode=result.retcode,
                message=getattr(result, "comment", "TP attach failed"),
                failure_class=failure_class,
                retryable=self._is_retryable_failure(failure_class),
            )

        verified = self._verify_position_protection(
            position_ticket=position_ticket,
            expected_sl=sl,
            expected_tp=tp,
        )
        if not verified:
            self.logger.warning(
                "ENTRY OK BUT PROTECTION NOT ATTACHED | ticket=%s target_sl=%.5f target_tp=%.5f",
                position_ticket,
                sl,
                tp,
            )
            return ProtectionResult(
                ok=False,
                retcode=result.retcode,
                message="Protection modify done but verification failed",
                failure_class=TradeFailureClass.UNKNOWN,
                retryable=False,
            )

        if tp > 0:
            self.logger.info(
                "BOT POSITION TP attached | ticket=%s tp=%.5f",
                position_ticket,
                tp,
            )
        if sl > 0:
            self.logger.info(
                "BOT POSITION protection updated | ticket=%s sl=%.5f tp=%.5f",
                position_ticket,
                sl,
                tp,
            )
        return ProtectionResult(
            ok=True,
            retcode=result.retcode,
            message="Protection attached successfully",
            tp_attached=tp,
            sl_attached=sl,
            failure_class=None,
            retryable=False,
        )

    def _build_valid_tp_from_fill(
        self,
        *,
        symbol: str,
        side: str,
        fill_price: float,
        target_distance: float,
        constraints: SymbolConstraints,
    ) -> ProtectionResult:
        normalized_fill = round(float(fill_price), constraints.digits)
        min_stop_distance = float(constraints.min_stop_distance)
        if target_distance < min_stop_distance:
            return ProtectionResult(
                ok=False,
                retcode=None,
                message="INVALID_STOPS_AFTER_FILL:target_distance_below_broker_minimum",
                failure_class=TradeFailureClass.NON_RETRYABLE,
                retryable=False,
            )

        side_upper = side.upper()
        if side_upper == "BUY":
            raw_tp = normalized_fill + target_distance
        elif side_upper == "SELL":
            raw_tp = normalized_fill - target_distance
        else:
            return ProtectionResult(
                ok=False,
                retcode=None,
                message=f"INVALID_SIDE_FOR_PROTECTION:{side}",
                failure_class=TradeFailureClass.NON_RETRYABLE,
                retryable=False,
            )

        normalized_tp = round(raw_tp, constraints.digits)
        realized_distance = abs(normalized_fill - normalized_tp)
        epsilon = constraints.point / 10
        if realized_distance + epsilon < min_stop_distance:
            return ProtectionResult(
                ok=False,
                retcode=None,
                message="INVALID_STOPS_AFTER_FILL:normalized_distance_below_broker_minimum",
                failure_class=TradeFailureClass.NON_RETRYABLE,
                retryable=False,
            )

        return ProtectionResult(
            ok=True,
            retcode=None,
            message="Protection TP normalized from actual fill",
            tp_attached=normalized_tp,
            failure_class=None,
            retryable=False,
        )

    def close_position(self, ticket: int, comment: str = "BOT_CLOSE") -> CloseResult:
        positions = self.positions_get(ticket=ticket)
        if not positions:
            self.logger.warning("Close skipped, position not found | ticket=%s", ticket)
            return CloseResult(
                ok=False,
                ticket=ticket,
                symbol=self.settings.symbol,
                position_side="UNKNOWN",
                close_side="UNKNOWN",
                price=0.0,
                filling_mode=None,
                retcode=None,
                comment="position_not_found",
                failure_class=TradeFailureClass.UNKNOWN,
                retryable=False,
            )

        position = positions[0]
        symbol = position.symbol
        constraints = self.get_symbol_constraints(symbol)
        tick = self.get_latest_tick(symbol)
        close_type = (
            self.mt5.ORDER_TYPE_SELL
            if position.type == self.mt5.ORDER_TYPE_BUY
            else self.mt5.ORDER_TYPE_BUY
        )
        position_side = "BUY" if position.type == self.mt5.ORDER_TYPE_BUY else "SELL"
        close_side = "SELL" if close_type == self.mt5.ORDER_TYPE_SELL else "BUY"
        price = float(tick.bid if close_side == "SELL" else tick.ask)

        broker_comment = self._normalize_broker_comment(comment, fallback="GEN_EXIT")
        base_request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "position": ticket,
            "magic": self.settings.magic_number,
            "volume": self._normalize_volume(float(position.volume), constraints),
            "type": close_type,
            "price": price,
            "deviation": self.settings.order_deviation_points,
            "type_time": self.mt5.ORDER_TIME_GTC,
            "comment": broker_comment,
        }

        last_failure: CloseResult | None = None
        for filling_mode in self._candidate_filling_modes(symbol):
            request = {**base_request, "type_filling": filling_mode}
            result = self.mt5.order_send(request)
            if result is None:
                last_failure = CloseResult(
                    ok=False,
                    ticket=ticket,
                    symbol=symbol,
                    position_side=position_side,
                    close_side=close_side,
                    price=price,
                    filling_mode=filling_mode,
                    retcode=None,
                    comment=f"order_send_none:last_error={self.mt5.last_error()}",
                    failure_class=TradeFailureClass.UNKNOWN,
                    retryable=False,
                )
                continue
            if result.retcode == self.mt5.TRADE_RETCODE_DONE:
                close_result = CloseResult(
                    ok=True,
                    ticket=ticket,
                    symbol=symbol,
                    position_side=position_side,
                    close_side=close_side,
                    price=price,
                    filling_mode=filling_mode,
                    retcode=result.retcode,
                    comment=getattr(result, "comment", ""),
                    failure_class=None,
                    retryable=False,
                )
                self.logger.info(
                    'event="emergency_close_succeeded" ticket=%s retcode=%s comment=%s position_side=%s close_side=%s price=%.5f filling=%s symbol=%s',
                    ticket,
                    result.retcode,
                    close_result.comment,
                    position_side,
                    close_side,
                    price,
                    filling_mode,
                    symbol,
                )
                return close_result

            failure_class = self.classify_trade_retcode(result.retcode)
            last_failure = CloseResult(
                ok=False,
                ticket=ticket,
                symbol=symbol,
                position_side=position_side,
                close_side=close_side,
                price=price,
                filling_mode=filling_mode,
                retcode=result.retcode,
                comment=getattr(result, "comment", ""),
                failure_class=failure_class,
                retryable=self._is_retryable_failure(failure_class),
            )
            self.logger.warning(
                'event="emergency_close_subattempt_failed" ticket=%s retcode=%s classification=%s comment=%s position_side=%s close_side=%s price=%.5f filling=%s symbol=%s retryable=%s',
                ticket,
                result.retcode,
                failure_class.value,
                last_failure.comment,
                position_side,
                close_side,
                price,
                filling_mode,
                symbol,
                last_failure.retryable,
            )

        return last_failure or CloseResult(
            ok=False,
            ticket=ticket,
            symbol=symbol,
            position_side=position_side,
            close_side=close_side,
            price=price,
            filling_mode=None,
            retcode=None,
            comment="close_failed_without_result",
            failure_class=TradeFailureClass.UNKNOWN,
            retryable=False,
        )

    def estimate_loss_per_lot(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        distance: float,
    ) -> float | None:
        side_upper = side.upper()
        if distance <= 0:
            return None

        close_price = (
            entry_price - distance if side_upper == "BUY" else entry_price + distance
        )
        order_type = (
            self.mt5.ORDER_TYPE_BUY if side_upper == "BUY" else self.mt5.ORDER_TYPE_SELL
        )
        profit = self.mt5.order_calc_profit(order_type, symbol, 1.0, entry_price, close_price)
        if profit is None:
            self.logger.warning(
                "order_calc_profit failed | symbol=%s side=%s last_error=%s",
                symbol,
                side_upper,
                self.mt5.last_error(),
            )
            return None
        return abs(float(profit))

    def _candidate_filling_modes(self, symbol: str) -> list[int]:
        info = self.get_symbol_info(symbol)
        preferred = getattr(info, "filling_mode", None)
        candidates = [
            preferred,
            getattr(self.mt5, "ORDER_FILLING_FOK", None),
            getattr(self.mt5, "ORDER_FILLING_IOC", None),
            getattr(self.mt5, "ORDER_FILLING_RETURN", None),
        ]
        unique: list[int] = []
        for item in candidates:
            if item is None or item in unique:
                continue
            unique.append(int(item))
        return unique

    def _resolve_live_position_ticket(
        self,
        symbol: str,
        side: str,
        volume: float,
        magic: int,
        order_ticket: int | None,
        timeout_seconds: float = 3.0,
    ) -> int | None:
        expected_type = (
            self.mt5.ORDER_TYPE_BUY if side == "BUY" else self.mt5.ORDER_TYPE_SELL
        )
        deadline = time.time() + timeout_seconds
        while time.time() <= deadline:
            positions = self.positions_get(symbol=symbol)
            matches = [
                pos
                for pos in positions
                if int(getattr(pos, "type", -1)) == expected_type
                and int(getattr(pos, "magic", -1)) == magic
                and math.isclose(float(getattr(pos, "volume", 0.0)), volume, rel_tol=0.0, abs_tol=1e-9)
            ]
            if matches:
                matches.sort(
                    key=lambda pos: (
                        getattr(pos, "time_update", 0) or 0,
                        getattr(pos, "ticket", 0) or 0,
                    ),
                    reverse=True,
                )
                return int(matches[0].ticket)
            time.sleep(0.2)
        self.logger.warning(
            "Unable to resolve live position ticket | symbol=%s side=%s volume=%.2f order=%s",
            symbol,
            side,
            volume,
            order_ticket,
        )
        return None

    def _verify_position_protection(
        self,
        position_ticket: int,
        expected_sl: float,
        expected_tp: float,
        tolerance: float = 1e-9,
        retries: int = 5,
    ) -> bool:
        for _ in range(retries):
            positions = self.positions_get(ticket=position_ticket)
            if positions:
                live_sl = float(getattr(positions[0], "sl", 0.0) or 0.0)
                live_tp = float(getattr(positions[0], "tp", 0.0) or 0.0)
                sl_ok = expected_sl <= 0 or math.isclose(
                    live_sl, expected_sl, rel_tol=0.0, abs_tol=tolerance
                )
                tp_ok = expected_tp <= 0 or math.isclose(
                    live_tp, expected_tp, rel_tol=0.0, abs_tol=tolerance
                )
                if sl_ok and tp_ok:
                    return True
            time.sleep(0.2)
        return False

    @staticmethod
    def _normalize_volume(volume: float, constraints: SymbolConstraints) -> float:
        clipped = max(constraints.volume_min, min(volume, constraints.volume_max))
        if constraints.volume_step <= 0:
            return round(clipped, 2)
        steps = round((clipped - constraints.volume_min) / constraints.volume_step)
        normalized = constraints.volume_min + (steps * constraints.volume_step)
        digits = MT5Client._decimal_places(constraints.volume_step)
        return round(normalized, digits)

    @staticmethod
    def _decimal_places(value: float) -> int:
        text = f"{value:.10f}".rstrip("0")
        if "." not in text:
            return 0
        return len(text.split(".", 1)[1])

