"""Canonical enums for TSP V2."""

from __future__ import annotations

from enum import Enum


class RuntimeMode(str, Enum):
    DEVELOPMENT = "DEVELOPMENT"
    BACKTEST = "BACKTEST"
    FORWARD_TEST = "FORWARD_TEST"
    CONTEST = "CONTEST"
    DIAGNOSTIC = "DIAGNOSTIC"


class ProfileName(str, Enum):
    FORWARD_SAFE = "FORWARD_SAFE"
    CONTEST_BALANCED = "CONTEST_BALANCED"
    CONTEST_HUNTER = "CONTEST_HUNTER"
    FINAL_SPRINT = "FINAL_SPRINT"
    DIAGNOSTIC = "DIAGNOSTIC"


class ClockHealth(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    SOFT_FAIL = "SOFT_FAIL"
    HARD_FAIL = "HARD_FAIL"


class SessionName(str, Enum):
    LONDON = "LONDON"
    LONDON_NY = "LONDON_NY"
    EARLY_NY = "EARLY_NY"
    LATE_NY = "LATE_NY"
    ASIA = "ASIA"
    DEAD = "DEAD"


class GovernorState(str, Enum):
    SURVIVE = "SURVIVE"
    NORMAL = "NORMAL"
    ATTACK = "ATTACK"
    HUNTER = "HUNTER"
    CHASE = "CHASE"
    PROTECT = "PROTECT"
    SPRINT = "SPRINT"
    KILL_REVIEW = "KILL_REVIEW"


class RegimeName(str, Enum):
    TREND = "TREND"
    BREAKOUT = "BREAKOUT"
    MICRO_MOMENTUM = "MICRO_MOMENTUM"
    CHOP = "CHOP"
    NEWS_LOCKOUT = "NEWS_LOCKOUT"


class SignalFamily(str, Enum):
    TREND_CONTINUATION = "TREND_CONTINUATION"
    BREAKOUT_MOMENTUM = "BREAKOUT_MOMENTUM"
    MICRO_IMPULSE = "MICRO_IMPULSE"


class RiskAction(str, Enum):
    ENTER = "ENTER"
    BLOCK = "BLOCK"
    SCALE = "SCALE"
    PYRAMID = "PYRAMID"
    REDUCE = "REDUCE"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class NewsProviderMode(str, Enum):
    STATIC_FILE = "STATIC_FILE"
    CALENDAR_SNAPSHOT = "CALENDAR_SNAPSHOT"
    DISABLED_DIAGNOSTIC_ONLY = "DISABLED_DIAGNOSTIC_ONLY"


class NewsProviderState(str, Enum):
    READY = "READY"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED = "DISABLED"


class ExecutionRegistryState(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class HealthState(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class TelemetryCategory(str, Enum):
    SYSTEM = "SYSTEM"
    DEPLOYMENT = "DEPLOYMENT"
    MARKET = "MARKET"
    REGIME = "REGIME"
    SIGNAL = "SIGNAL"
    RISK = "RISK"
    GOVERNOR = "GOVERNOR"
    EXECUTION = "EXECUTION"
    RECOVERY = "RECOVERY"
    TELEMETRY = "TELEMETRY"


class TelemetrySeverity(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class PaceClassification(str, Enum):
    BEHIND = "BEHIND"
    ON_TRACK = "ON_TRACK"
    AHEAD = "AHEAD"
