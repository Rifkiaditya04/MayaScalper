"""MT5 bridge implementation for TSP V2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import importlib
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..config_schema import ConfigValidationError


RESPONSE_OK = "OK"
RESPONSE_FAIL_STARTUP = "FAIL_STARTUP"
RESPONSE_DEGRADE_SYMBOL = "DEGRADE_SYMBOL"
RESPONSE_BLOCK_EXECUTION = "BLOCK_EXECUTION"
RESPONSE_TRIGGER_RECONCILIATION = "TRIGGER_RECONCILIATION"
RESPONSE_ESCALATE_KILL_REVIEW = "ESCALATE_KILL_REVIEW"

SUCCESS_RET_CODES = {
    "DONE",
    "DONE_PARTIAL",
    "ACCEPTED",
    "ACKNOWLEDGED",
    "PLACED",
    "FILLED",
}
RETRYABLE_RET_CODES = {
    "REQUOTE",
    "PRICE_CHANGED",
    "PRICE_OFF",
    "TIMEOUT",
    "CONNECTION",
    "TOO_MANY_REQUESTS",
    "TRADE_CONTEXT_BUSY",
    "SERVER_DISABLES_AT",
    "CLIENT_DISABLES_AT",
}
NON_RETRYABLE_RET_CODES = {
    "INVALID_STOPS",
    "INVALID_PRICE",
    "INVALID_VOLUME",
    "MARKET_CLOSED",
    "TRADE_DISABLED",
    "NO_MONEY",
    "NOT_ENOUGH_MONEY",
    "SYMBOL_DISABLED",
    "INVALID",
}


@dataclass(frozen=True, slots=True)
class MT5BridgeStatus:
    ok: bool
    failure_class: str
    response_class: str
    retryable: bool
    fatal: bool
    message: str
    terminal_ready: bool = False
    broker_ready: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "failure_class": self.failure_class,
            "response_class": self.response_class,
            "retryable": self.retryable,
            "fatal": self.fatal,
            "message": self.message,
            "terminal_ready": self.terminal_ready,
            "broker_ready": self.broker_ready,
            "diagnostics": _json_ready(self.diagnostics),
        }


@dataclass(frozen=True, slots=True)
class MT5TradeResult:
    ok: bool
    failure_class: str
    response_class: str
    retryable: bool
    fatal: bool
    terminal: bool
    message: str
    ticket: int | None = None
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "failure_class": self.failure_class,
            "response_class": self.response_class,
            "retryable": self.retryable,
            "fatal": self.fatal,
            "terminal": self.terminal,
            "message": self.message,
            "ticket": self.ticket,
            "request": _json_ready(self.request),
            "response": _json_ready(self.response),
            "diagnostics": _json_ready(self.diagnostics),
        }


class MT5BridgeError(RuntimeError):
    def __init__(self, status: MT5BridgeStatus) -> None:
        super().__init__(status.message)
        self.status = status


@dataclass(slots=True)
class MT5Bridge:
    terminal_path: Path | None = None
    login: int | str | None = None
    password: str | None = None
    server: str | None = None
    timeout_seconds: int = 15
    mt5_module: Any | None = None
    connected: bool = False
    terminal_ready: bool = False
    broker_ready: bool = False
    market_data_usable: bool = False
    market_data_probe_attempted: bool = False
    market_data_probe_error: dict[str, Any] | None = None
    recovery_fully_usable: bool = False
    _market_data_probe_in_progress: bool = False
    last_connect_path: str | None = None
    last_status: MT5BridgeStatus | None = None

    def connect(self) -> MT5BridgeStatus:
        self._reset_market_data_probe_state()
        self.last_connect_path = None
        mt5 = self._resolve_module()
        if mt5 is None:
            self.last_connect_path = "connect_failed"
            return self._store_status(
                self._status(
                    ok=False,
                    failure_class="MT5_PACKAGE_UNAVAILABLE",
                    response_class=RESPONSE_FAIL_STARTUP,
                    retryable=False,
                    fatal=True,
                    message="MT5 package / bridge unavailable",
                    diagnostics={"module": "MetaTrader5"},
                )
            )

        init_kwargs: dict[str, Any] = {"timeout": self.timeout_seconds}
        if self.terminal_path is not None:
            init_kwargs["path"] = str(self.terminal_path)

        login_used = self.login is not None and self.password is not None and self.server is not None
        if login_used:
            init_kwargs["login"] = _coerce_login(self.login)
            init_kwargs["password"] = self.password
            init_kwargs["server"] = self.server

        if not bool(getattr(mt5, "initialize", lambda **_: False)(**init_kwargs)):
            diagnostics = {"initialize_kwargs": _json_ready(init_kwargs)}
            terminal_info = _record_to_dict(_safe_call(mt5, "terminal_info", default=None))
            account_info = _record_to_dict(_safe_call(mt5, "account_info", default=None))
            if terminal_info is not None:
                diagnostics["terminal_info"] = terminal_info
            if account_info is not None:
                diagnostics["account_info"] = account_info
            terminal_connected = bool(terminal_info.get("connected", True)) if terminal_info is not None else False
            if terminal_info is not None and account_info is not None and terminal_connected:
                diagnostics["initialize_fallback"] = "existing_session"
                self.connected = True
                self.terminal_ready = True
                self.broker_ready = True
                self.last_connect_path = "existing_session_fallback"
                status = self._status(
                    ok=True,
                    failure_class="",
                    response_class=RESPONSE_OK,
                    retryable=False,
                    fatal=False,
                    message="MT5 bridge connected via existing terminal session",
                    terminal_ready=True,
                    broker_ready=True,
                    diagnostics=diagnostics,
                )
                return self._store_status(status)
            self._shutdown_module(mt5)
            self.last_connect_path = "connect_failed"
            status = self._disconnect_with_status(
                self._status(
                    ok=False,
                    failure_class="TERMINAL_UNAVAILABLE",
                    response_class=RESPONSE_FAIL_STARTUP,
                    retryable=False,
                    fatal=True,
                    message=_last_error_message(mt5, "MT5 initialize failed"),
                    diagnostics=diagnostics,
                )
            )
            self.last_connect_path = "connect_failed"
            return self._store_status(status)

        diagnostics = {"initialize_kwargs": _json_ready(init_kwargs), "login_used": login_used}
        if login_used:
            if not bool(
                getattr(mt5, "login", lambda **_: False)(
                    login=init_kwargs["login"],
                    password=init_kwargs["password"],
                    server=init_kwargs["server"],
                )
            ):
                self._shutdown_module(mt5)
                self.last_connect_path = "connect_failed"
                status = self._disconnect_with_status(
                    self._status(
                        ok=False,
                        failure_class="BROKER_DISCONNECTED",
                        response_class=RESPONSE_FAIL_STARTUP,
                        retryable=False,
                        fatal=True,
                        message=_last_error_message(mt5, "MT5 login failed"),
                        diagnostics=diagnostics,
                    )
                )
                self.last_connect_path = "connect_failed"
                return self._store_status(status)
            diagnostics["login"] = "ok"

        terminal_info = _record_to_dict(_safe_call(mt5, "terminal_info", default=None))
        account_info = _record_to_dict(_safe_call(mt5, "account_info", default=None))
        broker_ready = account_info is not None
        terminal_ready = terminal_info is not None
        if terminal_info is not None:
            diagnostics["terminal_info"] = terminal_info
        if account_info is not None:
            diagnostics["account_info"] = account_info

        if not terminal_ready:
            self._shutdown_module(mt5)
            self.last_connect_path = "connect_failed"
            status = self._disconnect_with_status(
                self._status(
                    ok=False,
                    failure_class="TERMINAL_UNAVAILABLE",
                    response_class=RESPONSE_FAIL_STARTUP,
                    retryable=False,
                    fatal=True,
                    message="MT5 terminal alive check failed",
                    diagnostics=diagnostics,
                )
            )
            self.last_connect_path = "connect_failed"
            return self._store_status(status)
        if not broker_ready:
            self._shutdown_module(mt5)
            self.last_connect_path = "connect_failed"
            status = self._disconnect_with_status(
                self._status(
                    ok=False,
                    failure_class="BROKER_DISCONNECTED",
                    response_class=RESPONSE_FAIL_STARTUP,
                    retryable=False,
                    fatal=True,
                    message="MT5 terminal connected but broker/account unavailable",
                    diagnostics=diagnostics,
                )
            )
            self.last_connect_path = "connect_failed"
            return self._store_status(status)

        self.connected = True
        self.terminal_ready = True
        self.broker_ready = True
        self.last_connect_path = "initialize_login"
        status = self._status(
            ok=True,
            failure_class="",
            response_class=RESPONSE_OK,
            retryable=False,
            fatal=False,
            message="MT5 bridge connected",
            terminal_ready=True,
            broker_ready=True,
            diagnostics=diagnostics,
        )
        return self._store_status(status)

    def disconnect(self) -> MT5BridgeStatus:
        self._reset_market_data_probe_state()
        mt5 = self._resolve_module()
        if mt5 is None or not self.connected:
            self.connected = False
            self.terminal_ready = False
            self.broker_ready = False
            return self._store_status(
                self._status(
                    ok=True,
                    failure_class="",
                    response_class=RESPONSE_OK,
                    retryable=False,
                    fatal=False,
                    message="MT5 bridge already disconnected",
                    diagnostics={"no_op": True},
                )
            )

        shutdown = getattr(mt5, "shutdown", None)
        if callable(shutdown):
            result = bool(shutdown())
            if not result:
                return self._store_status(
                    self._status(
                        ok=False,
                        failure_class="TERMINAL_UNAVAILABLE",
                        response_class=RESPONSE_BLOCK_EXECUTION,
                        retryable=False,
                        fatal=False,
                        message=_last_error_message(mt5, "MT5 shutdown failed"),
                    )
                )

        self.connected = False
        self.terminal_ready = False
        self.broker_ready = False
        return self._store_status(
            self._status(
                ok=True,
                failure_class="",
                response_class=RESPONSE_OK,
                retryable=False,
                fatal=False,
                message="MT5 bridge disconnected",
            )
        )

    def heartbeat(self) -> MT5BridgeStatus:
        mt5 = self._require_module()
        terminal_info = _record_to_dict(_safe_call(mt5, "terminal_info", default=None))
        if terminal_info is None:
            return self._store_status(
                self._status(
                    ok=False,
                    failure_class="TERMINAL_UNAVAILABLE",
                    response_class=RESPONSE_FAIL_STARTUP,
                    retryable=False,
                    fatal=True,
                    message="MT5 terminal info unavailable",
                )
            )

        account_info = _record_to_dict(_safe_call(mt5, "account_info", default=None))
        trade_allowed = bool(terminal_info.get("trade_allowed", True))
        connected = bool(terminal_info.get("connected", True))
        diagnostics = {
            "terminal_info": terminal_info,
            "account_info": account_info,
            "trade_allowed": trade_allowed,
            "connected": connected,
        }
        if not connected:
            self.connected = False
            self.terminal_ready = True
            self.broker_ready = False
            return self._store_status(
                self._status(
                    ok=False,
                    failure_class="BROKER_DISCONNECTED",
                    response_class=RESPONSE_TRIGGER_RECONCILIATION,
                    retryable=True,
                    fatal=False,
                    message="MT5 terminal alive but broker disconnected",
                    terminal_ready=True,
                    broker_ready=False,
                    diagnostics=diagnostics,
                )
            )
        if account_info is None:
            self.connected = True
            self.terminal_ready = True
            self.broker_ready = False
            return self._store_status(
                self._status(
                    ok=False,
                    failure_class="BROKER_DISCONNECTED",
                    response_class=RESPONSE_TRIGGER_RECONCILIATION,
                    retryable=True,
                    fatal=False,
                    message="MT5 broker/account unavailable during heartbeat",
                    terminal_ready=True,
                    broker_ready=False,
                    diagnostics=diagnostics,
                )
            )
        if not trade_allowed:
            self.connected = True
            self.terminal_ready = True
            self.broker_ready = True
            return self._store_status(
                self._status(
                    ok=False,
                    failure_class="BROKER_DISCONNECTED",
                    response_class=RESPONSE_BLOCK_EXECUTION,
                    retryable=False,
                    fatal=False,
                    message="MT5 terminal trade not allowed",
                    terminal_ready=True,
                    broker_ready=True,
                    diagnostics=diagnostics,
                )
            )

        self.connected = True
        self.terminal_ready = True
        self.broker_ready = True
        return self._store_status(
            self._status(
                ok=True,
                failure_class="",
                response_class=RESPONSE_OK,
                retryable=False,
                fatal=False,
                message="MT5 heartbeat healthy",
                terminal_ready=True,
                broker_ready=True,
                diagnostics=diagnostics,
            )
        )

    def account_info(self) -> dict[str, Any]:
        return self.query_account()

    def query_account(self) -> dict[str, Any]:
        mt5 = self._require_module()
        info = _record_to_dict(_safe_call(mt5, "account_info", default=None))
        if info is None:
            raise MT5BridgeError(
                self._bridge_error_status(
                    mt5,
                    failure_class="BROKER_DISCONNECTED",
                    response_class=RESPONSE_TRIGGER_RECONCILIATION,
                    fatal=False,
                    message="Unable to fetch account info from MT5",
                )
            )
        return info

    def symbol_info(self, symbol: str) -> dict[str, Any]:
        return self.query_symbol_contract(symbol)

    def query_symbol_contract(self, symbol: str) -> dict[str, Any]:
        mt5 = self._require_module()
        symbol_name = _require_symbol(symbol)
        info = _record_to_dict(_safe_call(mt5, "symbol_info", symbol_name, default=None))
        if info is None or not bool(info.get("visible", True)):
            symbol_select = getattr(mt5, "symbol_select", None)
            if callable(symbol_select):
                symbol_select(symbol_name, True)
                info = _record_to_dict(_safe_call(mt5, "symbol_info", symbol_name, default=None))
        if info is None or not bool(info.get("visible", True)):
            raise MT5BridgeError(
                self._bridge_error_status(
                    mt5,
                    failure_class="SYMBOL_UNAVAILABLE",
                    response_class=RESPONSE_DEGRADE_SYMBOL,
                    fatal=False,
                    message=f"Symbol not available in MT5: {symbol_name}",
                    diagnostics={"symbol": symbol_name},
                )
            )
        info["symbol"] = symbol_name
        return info

    def latest_tick(self, symbol: str) -> dict[str, Any]:
        return self.get_latest_tick(symbol)

    def get_latest_tick(self, symbol: str) -> dict[str, Any]:
        mt5 = self._require_module()
        symbol_name = _require_symbol(symbol)
        self.query_symbol_contract(symbol_name)
        captured_at_utc = datetime.now(tz=timezone.utc)
        tick = _record_to_dict(_safe_call(mt5, "symbol_info_tick", symbol_name, default=None))
        if tick is None:
            raise MT5BridgeError(
                self._bridge_error_status(
                    mt5,
                    failure_class="SYMBOL_UNAVAILABLE",
                    response_class=RESPONSE_DEGRADE_SYMBOL,
                    fatal=False,
                    message=f"Unable to fetch latest tick for {symbol_name}",
                    diagnostics={"symbol": symbol_name},
                )
            )
        tick_time_retry_count = 0
        while _record_tick_time_value(tick) is None and tick_time_retry_count < 20:
            time.sleep(0.5)
            tick_time_retry_count += 1
            tick = _record_to_dict(_safe_call(mt5, "symbol_info_tick", symbol_name, default=None))
            if tick is None:
                break
        if tick is None:
            raise MT5BridgeError(
                self._bridge_error_status(
                    mt5,
                    failure_class="SYMBOL_UNAVAILABLE",
                    response_class=RESPONSE_DEGRADE_SYMBOL,
                    fatal=False,
                    message=f"Unable to fetch latest tick for {symbol_name}",
                    diagnostics={"symbol": symbol_name},
                )
            )
        if _record_tick_time_value(tick) is None:
            stream_tick = self._get_latest_tick_from_stream(mt5, symbol_name, captured_at_utc)
            if stream_tick is not None:
                tick = stream_tick
            else:
                fallback_rates = self.get_rates(symbol_name, "M1", 1)
                if not fallback_rates:
                    raise MT5BridgeError(
                        self._bridge_error_status(
                            mt5,
                            failure_class="SYMBOL_UNAVAILABLE",
                            response_class=RESPONSE_DEGRADE_SYMBOL,
                            fatal=False,
                            message=f"Unable to resolve latest tick timestamp for {symbol_name}",
                            diagnostics={"symbol": symbol_name, "fallback": "rates_m1"},
                        )
                    )
                tick = dict(tick)
                fallback_timestamp = fallback_rates[-1]["timestamp"]
                tick["time"] = fallback_timestamp
                tick["time_msc"] = int(fallback_timestamp.timestamp() * 1000)
                tick["source_time_fallback"] = "rates_m1"
        elif tick_time_retry_count > 0:
            tick = dict(tick)
            tick["tick_time_retry_count"] = tick_time_retry_count
        return _normalize_tick_record(tick, symbol_name, captured_at_utc=captured_at_utc)

    def latest_tick_audit(self, symbol: str) -> dict[str, Any]:
        return self.get_latest_tick_audit(symbol)

    def get_latest_tick_audit(self, symbol: str) -> dict[str, Any]:
        mt5 = self._require_module()
        symbol_name = _require_symbol(symbol)
        captured_at_utc = datetime.now(tz=timezone.utc)
        raw_tick = _safe_call(mt5, "symbol_info_tick", symbol_name, default=None)
        raw_tick_dict = _record_to_dict(raw_tick) if raw_tick is not None else None
        tick_time_retry_count = 0
        while (raw_tick_dict is None or _record_tick_time_value(raw_tick_dict) is None) and tick_time_retry_count < 20:
            time.sleep(0.5)
            tick_time_retry_count += 1
            raw_tick = _safe_call(mt5, "symbol_info_tick", symbol_name, default=None)
            raw_tick_dict = _record_to_dict(raw_tick) if raw_tick is not None else None
        if raw_tick_dict is None or _record_tick_time_value(raw_tick_dict) is None:
            stream_tick = self._get_latest_tick_from_stream(mt5, symbol_name, captured_at_utc)
            if stream_tick is not None:
                raw_tick_dict = stream_tick
            else:
                fallback_rates = self.get_rates(symbol_name, "M1", 1)
                if not fallback_rates:
                    raise MT5BridgeError(
                        self._bridge_error_status(
                            mt5,
                            failure_class="SYMBOL_UNAVAILABLE",
                            response_class=RESPONSE_DEGRADE_SYMBOL,
                            fatal=False,
                            message=f"Unable to resolve latest tick timestamp for {symbol_name}",
                            diagnostics={"symbol": symbol_name, "fallback": "rates_m1"},
                        )
                    )
                fallback_timestamp = fallback_rates[-1]["timestamp"]
                fallback_close = float(fallback_rates[-1].get("close", 0.0) or 0.0)
                if fallback_close <= 0.0:
                    fallback_close = float(fallback_rates[-1].get("open", 0.0) or 1.0)
                fallback_spread = max(abs(fallback_close) * 0.0001, 0.01)
                raw_tick_dict = {
                    "symbol": symbol_name,
                    "time": fallback_timestamp,
                    "time_msc": int(fallback_timestamp.timestamp() * 1000),
                    "bid": fallback_close,
                    "ask": fallback_close + fallback_spread,
                    "last": fallback_close,
                    "volume": float(fallback_rates[-1].get("tick_volume", 0.0) or 0.0),
                    "source_time_fallback": "rates_m1",
                }
        normalized = _normalize_tick_record(dict(raw_tick_dict), symbol_name, captured_at_utc=captured_at_utc)
        raw_time = _record_tick_time_value(raw_tick_dict)
        raw_time_msc = raw_tick_dict.get("time_msc")
        return {
            "symbol": symbol_name,
            "raw_tick_type": type(raw_tick).__name__,
            "raw_tick_repr": repr(raw_tick),
            "raw_time_type": type(raw_time).__name__ if raw_time is not None else None,
            "raw_time_value": _json_ready(raw_time),
            "raw_time_msc_type": type(raw_time_msc).__name__ if raw_time_msc is not None else None,
            "raw_time_msc_value": _json_ready(raw_time_msc),
            "tick_time_fallback_used": raw_tick_dict.get("source_time_fallback") is not None,
            "tick_time_fallback_source": raw_tick_dict.get("source_time_fallback"),
            "tick_time_retry_count": tick_time_retry_count,
            "tick_time_retry_used": tick_time_retry_count > 0,
            "source_timestamp_utc_assumption": normalized["source_timestamp_utc_assumption"],
            "source_delta_seconds_from_capture": normalized["source_delta_seconds_from_capture"],
            "broker_time_offset_hours": normalized["broker_time_offset_hours"],
            "broker_time_offset_seconds": normalized["broker_time_offset_seconds"],
            "broker_time_utc": normalized["timestamp"],
            "normalized_timestamp": normalized["timestamp"],
            "normalized_tzinfo": str(normalized["timestamp"].tzinfo),
            "normalized_time": normalized["time"],
            "captured_at_utc": captured_at_utc,
            "broker_delta_seconds_from_capture": normalized["broker_delta_seconds_from_capture"],
            "delta_seconds_from_capture": (normalized["timestamp"] - captured_at_utc).total_seconds(),
            "normalized_tick": _json_ready(normalized),
        }

    def rates(self, symbol: str, timeframe: str, count: int) -> tuple[dict[str, Any], ...]:
        return self.get_rates(symbol, timeframe, count)

    def get_rates(self, symbol: str, timeframe: str, count: int) -> tuple[dict[str, Any], ...]:
        mt5 = self._require_module()
        symbol_name = _require_symbol(symbol)
        self.query_symbol_contract(symbol_name)
        timeframe_const = _resolve_timeframe(mt5, timeframe)
        captured_at_utc = datetime.now(tz=timezone.utc)
        records, last_raw = self._fetch_rates_with_retry(
            mt5,
            symbol_name,
            timeframe_const,
            count,
        )
        pre_recovery_last_error = _last_error_payload(mt5)
        ipc_recovery_attempted = False
        ipc_recovery_ok = False
        post_recovery_last_error: dict[str, Any] = {}
        if not records and self._last_error_indicates_ipc_loss(mt5) and not self._market_data_probe_in_progress:
            ipc_recovery_attempted = True
            ipc_recovery_ok = self._recover_ipc_connection(mt5, symbol_name)
            if ipc_recovery_ok:
                post_recovery_last_error = _last_error_payload(mt5)
                records, last_raw = self._fetch_rates_with_retry(
                    mt5,
                    symbol_name,
                    timeframe_const,
                    count,
                )
                post_recovery_last_error = _last_error_payload(mt5)
        if not records:
            raise MT5BridgeError(
                self._bridge_error_status(
                    mt5,
                    failure_class="CONTRACT_QUERY_FAILURE",
                    response_class=RESPONSE_DEGRADE_SYMBOL,
                    fatal=False,
                    message=f"Unable to fetch rates for {symbol_name} {timeframe}",
                    diagnostics={
                        "symbol": symbol_name,
                        "timeframe": timeframe,
                        "count": count,
                        "raw_type": type(last_raw).__name__ if last_raw is not None else None,
                        "pre_recovery_last_error": pre_recovery_last_error,
                        "ipc_recovery_attempted": ipc_recovery_attempted,
                        "ipc_recovery_ok": ipc_recovery_ok,
                        "post_recovery_last_error": post_recovery_last_error,
                        "recovery_connect_path": self.last_connect_path,
                        "market_data_probe_attempted": self.market_data_probe_attempted,
                        "market_data_usable": self.market_data_usable,
                        "recovery_fully_usable": self.recovery_fully_usable,
                        "market_data_probe_error": _json_ready(self.market_data_probe_error),
                    },
                )
            )
        reference_record = max(records, key=_record_timestamp_for_normalization)
        reference_timestamp = _record_timestamp_for_normalization(reference_record)
        broker_offset_hours = _infer_broker_offset_hours(
            source_timestamp=reference_timestamp,
            captured_at_utc=captured_at_utc,
        )
        return tuple(
            _normalize_rate_record(
                record,
                timeframe=timeframe,
                broker_offset_hours=broker_offset_hours,
            )
            for record in records
        )

    def _fetch_rates_with_retry(
        self,
        mt5: Any,
        symbol_name: str,
        timeframe_const: Any,
        count: int,
    ) -> tuple[list[dict[str, Any]], Any]:
        records: list[dict[str, Any]] = []
        last_raw: Any = None
        for attempt in range(20):
            raw = _safe_call(mt5, "copy_rates_from_pos", symbol_name, timeframe_const, 0, count, default=None)
            last_raw = raw
            records = _normalize_records(raw, record_kind="rates", allow_empty=True)
            if records:
                break
            if attempt < 19:
                time.sleep(0.5)
        return records, last_raw

    def _probe_market_data_usability(self, symbol: str) -> bool:
        symbol_name = _require_symbol(symbol)
        if self._market_data_probe_in_progress:
            return self.market_data_usable

        self.market_data_probe_attempted = True
        self.market_data_probe_error = None
        self._market_data_probe_in_progress = True
        try:
            self.get_latest_tick(symbol_name)
        except MT5BridgeError as exc:
            self.market_data_usable = False
            self.market_data_probe_error = exc.status.to_payload()
            return False
        except Exception as exc:
            self.market_data_usable = False
            self.market_data_probe_error = {
                "exception_class": exc.__class__.__name__,
                "message": str(exc),
            }
            return False
        finally:
            self._market_data_probe_in_progress = False

        self.market_data_usable = True
        return True

    def _recover_ipc_connection(self, mt5: Any, symbol: str) -> bool:
        self._shutdown_module(mt5)
        self.connected = False
        self.terminal_ready = False
        self.broker_ready = False
        status = self.connect()
        connect_ok = bool(status.ok)
        probe_ok = False
        if connect_ok:
            probe_ok = self._probe_market_data_usability(symbol)
        self.recovery_fully_usable = connect_ok and probe_ok
        return connect_ok

    def _last_error_indicates_ipc_loss(self, mt5: Any) -> bool:
        payload = _last_error_payload(mt5)
        code = payload.get("code")
        message = str(payload.get("message") or "")
        return code == -10004 or "No IPC connection" in message

    def positions(self, symbol: str | None = None, ticket: int | None = None) -> tuple[dict[str, Any], ...]:
        return self.query_positions(symbol=symbol, ticket=ticket)

    def query_positions(self, symbol: str | None = None, ticket: int | None = None) -> tuple[dict[str, Any], ...]:
        mt5 = self._require_module()
        kwargs: dict[str, Any] = {}
        if symbol is not None:
            kwargs["symbol"] = _require_symbol(symbol)
        if ticket is not None:
            kwargs["ticket"] = ticket
        raw = _safe_call(
            mt5,
            "positions_get",
            default=None,
            **kwargs,
        )
        records = _normalize_records(raw, record_kind="positions", allow_empty=True)
        if raw is None:
            if symbol is None and ticket is None:
                return ()
            raise MT5BridgeError(
                self._bridge_error_status(
                    mt5,
                    failure_class="BROKER_DISCONNECTED",
                    response_class=RESPONSE_TRIGGER_RECONCILIATION,
                    fatal=False,
                    message="positions_get failed",
                    diagnostics={"symbol": symbol, "ticket": ticket},
                )
            )
        return tuple(_normalize_generic_record(record) for record in records)

    def orders(self, symbol: str | None = None, ticket: int | None = None) -> tuple[dict[str, Any], ...]:
        return self.query_orders(symbol=symbol, ticket=ticket)

    def query_orders(self, symbol: str | None = None, ticket: int | None = None) -> tuple[dict[str, Any], ...]:
        mt5 = self._require_module()
        kwargs: dict[str, Any] = {}
        if symbol is not None:
            kwargs["symbol"] = _require_symbol(symbol)
        if ticket is not None:
            kwargs["ticket"] = ticket
        raw = _safe_call(
            mt5,
            "orders_get",
            default=None,
            **kwargs,
        )
        records = _normalize_records(raw, record_kind="orders", allow_empty=True)
        if raw is None:
            if symbol is None and ticket is None:
                return ()
            raise MT5BridgeError(
                self._bridge_error_status(
                    mt5,
                    failure_class="BROKER_DISCONNECTED",
                    response_class=RESPONSE_TRIGGER_RECONCILIATION,
                    fatal=False,
                    message="orders_get failed",
                    diagnostics={"symbol": symbol, "ticket": ticket},
                )
            )
        return tuple(_normalize_generic_record(record) for record in records)

    def deals(
        self,
        symbol: str | None = None,
        ticket: int | None = None,
        *,
        from_time_utc: datetime | None = None,
        to_time_utc: datetime | None = None,
    ) -> tuple[dict[str, Any], ...]:
        return self.query_deals(
            symbol=symbol,
            ticket=ticket,
            from_time_utc=from_time_utc,
            to_time_utc=to_time_utc,
        )

    def query_deals(
        self,
        symbol: str | None = None,
        ticket: int | None = None,
        *,
        from_time_utc: datetime | None = None,
        to_time_utc: datetime | None = None,
    ) -> tuple[dict[str, Any], ...]:
        mt5 = self._require_module()
        if symbol is None and ticket is None and from_time_utc is None and to_time_utc is None:
            return ()
        kwargs: dict[str, Any] = {}
        if from_time_utc is not None:
            kwargs["date_from"] = _normalize_datetime(from_time_utc)
        if to_time_utc is not None:
            kwargs["date_to"] = _normalize_datetime(to_time_utc)
        if symbol is not None:
            kwargs["group"] = _require_symbol(symbol)
        if ticket is not None:
            kwargs["ticket"] = ticket
        raw = _safe_call(mt5, "history_deals_get", default=None, **kwargs)
        records = _normalize_records(raw, record_kind="deals", allow_empty=True)
        if raw is None:
            raise MT5BridgeError(
                self._bridge_error_status(
                    mt5,
                    failure_class="BROKER_DISCONNECTED",
                    response_class=RESPONSE_TRIGGER_RECONCILIATION,
                    fatal=False,
                    message="history_deals_get failed",
                    diagnostics=_json_ready(kwargs),
                )
            )
        return tuple(_normalize_generic_record(record) for record in records)

    def place_order(self, request: Mapping[str, Any]) -> MT5TradeResult:
        return self.send_order(request)

    def send_order(self, request: Mapping[str, Any]) -> MT5TradeResult:
        mt5 = self._require_module()
        normalized_request = _normalize_order_request(mt5, request)
        raw_response = _safe_call(mt5, "order_send", normalized_request, default=None)
        if raw_response is None:
            status = self._trade_result_status(
                mt5,
                ok=False,
                failure_class="API_HUNG",
                response_class=RESPONSE_ESCALATE_KILL_REVIEW,
                retryable=False,
                fatal=True,
                terminal=False,
                message=_last_error_message(mt5, "order_send returned None"),
                request=normalized_request,
                response={},
                diagnostics={"stage": "order_send"},
            )
            return MT5TradeResult(
                ok=status.ok,
                failure_class=status.failure_class,
                response_class=status.response_class,
                retryable=status.retryable,
                fatal=status.fatal,
                terminal=False,
                message=status.message,
                request=normalized_request,
                response={},
                diagnostics=status.diagnostics,
            )

        response = _record_to_dict(raw_response)
        code = _normalize_retcode(mt5, response)
        ticket = _extract_ticket(response)
        outcome = _classify_trade_code(code)
        message = response.get("comment") or code or "unknown"
        diagnostics = {"code": code, "response": _json_ready(response)}
        if outcome["ok"]:
            result = MT5TradeResult(
                ok=True,
                failure_class=code or "",
                response_class=outcome["response_class"],
                retryable=False,
                fatal=False,
                terminal=outcome["terminal"],
                message=str(message),
                ticket=ticket,
                request=normalized_request,
                response=response,
                diagnostics=diagnostics,
            )
            return result

        status = self._trade_result_status(
            mt5,
            ok=False,
            failure_class=code or "UNKNOWN",
            response_class=outcome["response_class"],
            retryable=outcome["retryable"],
            fatal=outcome["fatal"],
            terminal=outcome["terminal"],
            message=str(message),
            request=normalized_request,
            response=response,
            diagnostics=diagnostics,
            ticket=ticket,
        )
        return MT5TradeResult(
            ok=status.ok,
            failure_class=status.failure_class,
            response_class=status.response_class,
            retryable=status.retryable,
            fatal=status.fatal,
            terminal=outcome["terminal"],
            message=status.message,
            ticket=ticket,
            request=normalized_request,
            response=response,
            diagnostics=status.diagnostics,
        )

    def modify_order(self, request: Mapping[str, Any]) -> MT5TradeResult:
        return self.place_order(request)

    def cancel_order(self, order_ticket: int, *, comment: str | None = None) -> MT5TradeResult:
        mt5 = self._require_module()
        request = {
            "action": getattr(mt5, "TRADE_ACTION_REMOVE", "TRADE_ACTION_REMOVE"),
            "order": int(order_ticket),
        }
        if comment is not None:
            request["comment"] = comment
        return self.place_order(request)

    def close_position(
        self,
        position_ticket: int,
        *,
        volume: float | None = None,
        deviation: int = 20,
        comment: str | None = None,
    ) -> MT5TradeResult:
        mt5 = self._require_module()
        position_records = self.query_positions(ticket=position_ticket)
        if not position_records:
            raise MT5BridgeError(
                self._trade_result_status(
                    mt5,
                    ok=False,
                    failure_class="POSITION_NOT_FOUND",
                    response_class=RESPONSE_TRIGGER_RECONCILIATION,
                    retryable=False,
                    fatal=False,
                    terminal=False,
                    message=f"Position not found for ticket {position_ticket}",
                    request={"position": position_ticket},
                    response={},
                    diagnostics={"position_ticket": position_ticket},
                    ticket=position_ticket,
                )
            )
        position = position_records[0]
        symbol = _require_symbol(position.get("symbol"))
        position_type = position.get("type")
        close_type = _opposite_order_type(mt5, position_type)
        tick = self.latest_tick(symbol)
        position_volume = float(volume if volume is not None else position.get("volume", 0.0))
        if position_volume <= 0.0:
            raise ConfigValidationError(f"Position volume must be positive for ticket {position_ticket}")
        close_price = float(tick["bid"] if close_type == getattr(mt5, "ORDER_TYPE_SELL", "SELL") else tick["ask"])
        request = {
            "action": getattr(mt5, "TRADE_ACTION_DEAL", "TRADE_ACTION_DEAL"),
            "position": int(position_ticket),
            "symbol": symbol,
            "volume": position_volume,
            "type": close_type,
            "price": close_price,
            "deviation": int(deviation),
            "comment": comment or f"CLOSE_{position_ticket}",
        }
        filling = _default_filling_mode(mt5)
        if filling is not None:
            request["type_filling"] = filling
        request["type_time"] = getattr(mt5, "ORDER_TIME_GTC", "ORDER_TIME_GTC")
        return self.place_order(request)

    def query_account_snapshot(self) -> dict[str, Any]:
        return self.query_account()

    def query_symbol_info(self, symbol: str) -> dict[str, Any]:
        return self.query_symbol_contract(symbol)

    def query_positions_snapshot(self, symbol: str | None = None, ticket: int | None = None) -> tuple[dict[str, Any], ...]:
        return self.query_positions(symbol=symbol, ticket=ticket)

    def query_orders_snapshot(self, symbol: str | None = None, ticket: int | None = None) -> tuple[dict[str, Any], ...]:
        return self.query_orders(symbol=symbol, ticket=ticket)

    def query_deals_snapshot(
        self,
        symbol: str | None = None,
        ticket: int | None = None,
        *,
        from_time_utc: datetime | None = None,
        to_time_utc: datetime | None = None,
    ) -> tuple[dict[str, Any], ...]:
        return self.query_deals(symbol=symbol, ticket=ticket, from_time_utc=from_time_utc, to_time_utc=to_time_utc)

    def _resolve_module(self) -> Any | None:
        if self.mt5_module is not None:
            return self.mt5_module
        try:
            self.mt5_module = importlib.import_module("MetaTrader5")
        except ModuleNotFoundError:
            self.mt5_module = None
        return self.mt5_module

    def _require_module(self) -> Any:
        mt5 = self._resolve_module()
        if mt5 is None:
            raise MT5BridgeError(
                self._status(
                    ok=False,
                    failure_class="MT5_PACKAGE_UNAVAILABLE",
                    response_class=RESPONSE_FAIL_STARTUP,
                    retryable=False,
                    fatal=True,
                    message="MT5 package / bridge unavailable",
                    diagnostics={"module": "MetaTrader5"},
                )
            )
        return mt5

    def _get_latest_tick_from_stream(
        self,
        mt5: Any,
        symbol_name: str,
        captured_at_utc: datetime,
    ) -> dict[str, Any] | None:
        copy_ticks_from = getattr(mt5, "copy_ticks_from", None)
        if not callable(copy_ticks_from):
            return None
        start_time = captured_at_utc - timedelta(minutes=5)
        last_raw: Any = None
        records: list[dict[str, Any]] = []
        tick_flags = getattr(mt5, "COPY_TICKS_ALL", 0)
        for attempt in range(10):
            raw = _safe_call(mt5, "copy_ticks_from", symbol_name, start_time, 100, tick_flags, default=None)
            last_raw = raw
            records = _normalize_records(raw, record_kind="ticks", allow_empty=True)
            if records:
                break
            if attempt < 9:
                time.sleep(0.5)
        if not records:
            return None
        latest = max(records, key=_record_timestamp_for_normalization)
        latest = dict(latest)
        latest["source_time_fallback"] = "ticks_stream"
        latest["source_time_fallback_raw_type"] = type(last_raw).__name__ if last_raw is not None else None
        return latest

    def _disconnect_with_status(self, status: MT5BridgeStatus) -> MT5BridgeStatus:
        self.connected = False
        self.terminal_ready = False
        self.broker_ready = False
        self._reset_market_data_probe_state()
        self.recovery_fully_usable = False
        return self._store_status(status)

    def _reset_market_data_probe_state(self) -> None:
        self.market_data_usable = False
        self.market_data_probe_attempted = False
        self.market_data_probe_error = None
        self.recovery_fully_usable = False
        self._market_data_probe_in_progress = False
        self.last_connect_path = None

    def _shutdown_module(self, mt5: Any) -> None:
        shutdown = getattr(mt5, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass

    def _status(
        self,
        *,
        ok: bool,
        failure_class: str,
        response_class: str,
        retryable: bool,
        fatal: bool,
        message: str,
        terminal_ready: bool = False,
        broker_ready: bool = False,
        diagnostics: dict[str, Any] | None = None,
    ) -> MT5BridgeStatus:
        return MT5BridgeStatus(
            ok=ok,
            failure_class=failure_class,
            response_class=response_class,
            retryable=retryable,
            fatal=fatal,
            message=message,
            terminal_ready=terminal_ready,
            broker_ready=broker_ready,
            diagnostics=diagnostics or {},
        )

    def _trade_result_status(
        self,
        mt5: Any,
        *,
        ok: bool,
        failure_class: str,
        response_class: str,
        retryable: bool,
        fatal: bool,
        terminal: bool,
        message: str,
        request: dict[str, Any],
        response: dict[str, Any],
        diagnostics: dict[str, Any],
        ticket: int | None = None,
    ) -> MT5BridgeStatus:
        payload = {
            "request": _json_ready(request),
            "response": _json_ready(response),
            "ticket": ticket,
            "last_error": _last_error_payload(mt5),
        }
        payload.update(diagnostics)
        return self._status(
            ok=ok,
            failure_class=failure_class,
            response_class=response_class,
            retryable=retryable,
            fatal=fatal,
            message=message,
            terminal_ready=self.terminal_ready,
            broker_ready=self.broker_ready,
            diagnostics=payload,
        )

    def _bridge_error_status(
        self,
        mt5: Any,
        *,
        failure_class: str,
        response_class: str,
        fatal: bool,
        message: str,
        diagnostics: dict[str, Any] | None = None,
    ) -> MT5BridgeStatus:
        payload = diagnostics or {}
        payload.setdefault("last_error", _last_error_payload(mt5))
        payload.setdefault("market_data_probe_attempted", self.market_data_probe_attempted)
        payload.setdefault("market_data_usable", self.market_data_usable)
        payload.setdefault("recovery_fully_usable", self.recovery_fully_usable)
        payload.setdefault("market_data_probe_error", _json_ready(self.market_data_probe_error))
        return self._status(
            ok=False,
            failure_class=failure_class,
            response_class=response_class,
            retryable=not fatal,
            fatal=fatal,
            message=message,
            terminal_ready=self.terminal_ready,
            broker_ready=self.broker_ready,
            diagnostics=payload,
        )

    def _store_status(self, status: MT5BridgeStatus) -> MT5BridgeStatus:
        self.last_status = status
        return status


def _classify_trade_code(code: str) -> dict[str, Any]:
    normalized = (code or "").upper()
    if normalized in SUCCESS_RET_CODES:
        return {
            "ok": True,
            "retryable": False,
            "fatal": False,
            "terminal": normalized == "DONE",
            "response_class": RESPONSE_OK if normalized != "DONE_PARTIAL" else RESPONSE_TRIGGER_RECONCILIATION,
        }
    if normalized in RETRYABLE_RET_CODES:
        return {
            "ok": False,
            "retryable": True,
            "fatal": False,
            "terminal": False,
            "response_class": RESPONSE_BLOCK_EXECUTION,
        }
    if normalized in NON_RETRYABLE_RET_CODES:
        return {
            "ok": False,
            "retryable": False,
            "fatal": False,
            "terminal": True,
            "response_class": RESPONSE_BLOCK_EXECUTION,
        }
    if normalized in {"UNKNOWN", ""}:
        return {
            "ok": False,
            "retryable": False,
            "fatal": True,
            "terminal": False,
            "response_class": RESPONSE_ESCALATE_KILL_REVIEW,
        }
    return {
        "ok": False,
        "retryable": False,
        "fatal": False,
        "terminal": False,
        "response_class": RESPONSE_TRIGGER_RECONCILIATION,
    }


def _normalize_retcode(mt5: Any, response: Mapping[str, Any]) -> str:
    raw = response.get("retcode")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().upper()
    if isinstance(raw, int) and not isinstance(raw, bool):
        reverse_map = _reverse_retcode_map(mt5)
        mapped = reverse_map.get(raw)
        if mapped:
            return mapped
        return str(raw)
    if raw is not None:
        return str(raw).strip().upper()
    for key in ("comment", "message", "reason"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return "UNKNOWN"


def _reverse_retcode_map(mt5: Any) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for name in dir(mt5):
        if not name.startswith("TRADE_RETCODE_"):
            continue
        value = getattr(mt5, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            mapping[value] = name.removeprefix("TRADE_RETCODE_")
    return mapping


def _default_filling_mode(mt5: Any) -> Any | None:
    for name in ("ORDER_FILLING_RETURN", "ORDER_FILLING_FOK", "ORDER_FILLING_IOC"):
        if hasattr(mt5, name):
            return getattr(mt5, name)
    return None


def _normalize_order_request(mt5: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(request)
    symbol = _require_symbol(normalized.get("symbol"))
    volume = float(normalized.get("volume", 0.0))
    if volume <= 0.0:
        raise ConfigValidationError("Order request volume must be positive")
    order_type = normalized.get("type")
    if order_type is None:
        raise ConfigValidationError("Order request requires 'type'")
    action = normalized.get("action")
    if action is None:
        action = getattr(mt5, "TRADE_ACTION_DEAL", "TRADE_ACTION_DEAL")
        normalized["action"] = action
    normalized["symbol"] = symbol
    normalized["volume"] = volume
    if "comment" in normalized and normalized["comment"] is not None:
        normalized["comment"] = str(normalized["comment"])
    if "type_time" not in normalized:
        normalized["type_time"] = getattr(mt5, "ORDER_TIME_GTC", "ORDER_TIME_GTC")
    if "type_filling" not in normalized:
        default_filling = _default_filling_mode(mt5)
        if default_filling is not None:
            normalized["type_filling"] = default_filling
    return normalized


def _normalize_tick_record(
    tick: dict[str, Any],
    symbol: str,
    *,
    captured_at_utc: datetime | None = None,
) -> dict[str, Any]:
    raw_time = _record_tick_time_value(tick)
    source_timestamp = _normalize_datetime(raw_time)
    reference_utc = _normalize_datetime(captured_at_utc or datetime.now(tz=timezone.utc))
    broker_offset_hours = _infer_broker_offset_hours(source_timestamp=source_timestamp, captured_at_utc=reference_utc)
    broker_time_utc = source_timestamp - timedelta(hours=broker_offset_hours)
    bid = float(tick.get("bid", 0.0))
    ask = float(tick.get("ask", 0.0))
    if bid <= 0.0 or ask <= 0.0 or ask < bid:
        raise ConfigValidationError(f"Tick has impossible bid/ask values for {symbol}")
    normalized = dict(tick)
    normalized["symbol"] = symbol
    normalized["source_timestamp_utc_assumption"] = source_timestamp
    normalized["source_delta_seconds_from_capture"] = (source_timestamp - reference_utc).total_seconds()
    normalized["broker_time_offset_hours"] = broker_offset_hours
    normalized["broker_time_offset_seconds"] = broker_offset_hours * 3600
    normalized["broker_time_utc"] = broker_time_utc
    normalized["broker_delta_seconds_from_capture"] = (broker_time_utc - reference_utc).total_seconds()
    normalized["time"] = broker_time_utc
    normalized["timestamp"] = broker_time_utc
    normalized["bid"] = bid
    normalized["ask"] = ask
    if "last" in normalized and normalized["last"] is not None:
        normalized["last"] = float(normalized["last"])
    if "volume" in normalized and normalized["volume"] is not None:
        normalized["volume"] = float(normalized["volume"])
    return normalized


def _normalize_rate_record(
    record: dict[str, Any],
    *,
    timeframe: str,
    broker_offset_hours: int = 0,
) -> dict[str, Any]:
    source_timestamp = _record_timestamp_for_normalization(record)
    broker_time_utc = source_timestamp - timedelta(hours=broker_offset_hours)
    normalized = dict(record)
    normalized["timeframe"] = timeframe
    normalized["source_timestamp_utc_assumption"] = source_timestamp
    normalized["broker_time_offset_hours"] = broker_offset_hours
    normalized["broker_time_offset_seconds"] = broker_offset_hours * 3600
    normalized["broker_time_utc"] = broker_time_utc
    normalized["time"] = broker_time_utc
    normalized["timestamp"] = broker_time_utc
    for key in ("open", "high", "low", "close"):
        if key in normalized and normalized[key] is not None:
            normalized[key] = float(normalized[key])
    if "tick_volume" in normalized and normalized["tick_volume"] is not None:
        normalized["tick_volume"] = float(normalized["tick_volume"])
    if "real_volume" in normalized and normalized["real_volume"] is not None:
        normalized["real_volume"] = float(normalized["real_volume"])
    if "volume" in normalized and normalized["volume"] is not None:
        normalized["volume"] = float(normalized["volume"])
    return normalized


def _infer_broker_offset_hours(*, source_timestamp: datetime, captured_at_utc: datetime) -> int:
    source_utc = _normalize_datetime(source_timestamp)
    reference_utc = _normalize_datetime(captured_at_utc)
    delta_seconds = (source_utc - reference_utc).total_seconds()
    if abs(delta_seconds) < 1.0:
        return 0
    return int(round(delta_seconds / 3600.0))


def _record_timestamp_for_normalization(record: Mapping[str, Any]) -> datetime:
    raw_time = record.get("time")
    if raw_time is None:
        raw_time = record.get("timestamp")
    return _normalize_datetime(raw_time)


def _record_tick_time_value(record: Mapping[str, Any]) -> Any:
    raw_time = record.get("time")
    if raw_time is not None:
        return raw_time
    raw_time_msc = record.get("time_msc")
    if raw_time_msc is not None:
        return float(raw_time_msc) / 1000.0
    return record.get("timestamp")


def _normalize_generic_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    for key in ("time", "timestamp", "time_setup", "time_done", "expiration", "time_msc"):
        if key in normalized and normalized[key] is not None:
            try:
                normalized[key] = _normalize_datetime(normalized[key])
            except (TypeError, ValueError):
                pass
    return normalized


def _normalize_records(raw: Any, *, record_kind: str, allow_empty: bool = False) -> list[dict[str, Any]]:
    if raw is None:
        return []
    dtype = getattr(raw, "dtype", None)
    names = getattr(dtype, "names", None)
    ndim = getattr(raw, "ndim", None)
    if isinstance(raw, (list, tuple)) or (names and ndim not in (None, 0)):
        records = [_record_to_dict(item) for item in raw]
    else:
        records = [_record_to_dict(raw)]
    if not records and not allow_empty:
        raise ConfigValidationError(f"{record_kind} query returned no records")
    return records


def _record_to_dict(record: Any) -> dict[str, Any]:
    if record is None:
        return {}
    if isinstance(record, Mapping):
        return dict(record)
    asdict = getattr(record, "_asdict", None)
    if callable(asdict):
        return dict(asdict())
    dtype = getattr(record, "dtype", None)
    names = getattr(dtype, "names", None)
    if names:
        return {name: record[name] for name in names}
    if hasattr(record, "__dict__"):
        return dict(vars(record))
    raise ConfigValidationError(f"Unsupported MT5 record type: {type(record)!r}")


def _normalize_datetime(raw_value: Any) -> datetime:
    if isinstance(raw_value, datetime):
        if raw_value.tzinfo is None:
            return raw_value.replace(tzinfo=timezone.utc)
        return raw_value.astimezone(timezone.utc)
    if isinstance(raw_value, str):
        value = raw_value.strip()
        if not value:
            raise ConfigValidationError("Datetime value must not be blank")
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return datetime.fromtimestamp(float(raw_value), tz=timezone.utc)


def _extract_ticket(record: Mapping[str, Any]) -> int | None:
    for key in ("ticket", "order", "deal", "position", "position_ticket", "order_ticket", "broker_ticket"):
        value = record.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _opposite_order_type(mt5: Any, position_type: Any) -> Any:
    buy = getattr(mt5, "ORDER_TYPE_BUY", "BUY")
    sell = getattr(mt5, "ORDER_TYPE_SELL", "SELL")
    if position_type == buy or str(position_type).upper() == str(buy).upper() or str(position_type).upper() == "BUY":
        return sell
    if position_type == sell or str(position_type).upper() == str(sell).upper() or str(position_type).upper() == "SELL":
        return buy
    raise ConfigValidationError(f"Unsupported position type for close_position: {position_type!r}")


def _resolve_timeframe(mt5: Any, timeframe: str) -> Any:
    tf = timeframe.strip().upper()
    if hasattr(mt5, f"TIMEFRAME_{tf}"):
        return getattr(mt5, f"TIMEFRAME_{tf}")
    if tf.startswith("M") or tf.startswith("H"):
        return tf
    raise ConfigValidationError(f"Unsupported timeframe for MT5 bridge: {timeframe}")


def _safe_call(mt5: Any, method_name: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
    method = getattr(mt5, method_name, None)
    if not callable(method):
        return default
    try:
        return method(*args, **kwargs)
    except TypeError:
        return default


def _last_error_payload(mt5: Any) -> dict[str, Any]:
    last_error = getattr(mt5, "last_error", None)
    if not callable(last_error):
        return {}
    try:
        raw = last_error()
    except Exception:
        return {}
    if isinstance(raw, tuple):
        if len(raw) >= 2:
            return {"code": raw[0], "message": raw[1]}
        if len(raw) == 1:
            return {"code": raw[0], "message": ""}
    if raw is None:
        return {}
    return {"code": raw, "message": ""}


def _last_error_message(mt5: Any, fallback: str) -> str:
    payload = _last_error_payload(mt5)
    code = payload.get("code")
    message = payload.get("message")
    if code is None and not message:
        return fallback
    if message:
        return f"{fallback} | last_error={code}:{message}"
    return f"{fallback} | last_error={code}"


def _require_symbol(symbol: str | None) -> str:
    if symbol is None or not str(symbol).strip():
        raise ConfigValidationError("symbol must be a non-empty string")
    return str(symbol).strip().upper()


def _coerce_login(login: int | str) -> int:
    if isinstance(login, bool):
        raise ConfigValidationError("login must be numeric")
    if isinstance(login, int):
        return login
    text = str(login).strip()
    if not text:
        raise ConfigValidationError("login must be numeric")
    return int(text)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value
