"""Logging bootstrap dan helper event name."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
import sys
from pathlib import Path

from mt5_bot.config import Settings


@dataclass(slots=True)
class LoggingBundle:
    logger: logging.Logger
    log_dir: Path
    live_log_file: Path
    session_log_file: Path


def configure_logging(settings: Settings) -> LoggingBundle:
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    live_log_file = log_dir / f"mt5_{settings.symbol}_live.log"
    session_log_file = log_dir / f"mt5_{settings.symbol}_{timestamp}.log"

    logger = logging.getLogger("mt5_live_rebuild")
    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    logger.handlers.clear()

    console_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    console_formatter.default_msec_format = "%s,%03d"

    file_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(filename)s:%(lineno)d | %(message)s"
    )
    file_formatter.default_msec_format = "%s,%03d"

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    stream_handler.setFormatter(console_formatter)
    logger.addHandler(stream_handler)

    live_file_handler = logging.FileHandler(live_log_file, encoding="utf-8")
    live_file_handler.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    live_file_handler.setFormatter(file_formatter)
    logger.addHandler(live_file_handler)

    session_file_handler = logging.FileHandler(session_log_file, encoding="utf-8")
    session_file_handler.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    session_file_handler.setFormatter(file_formatter)
    logger.addHandler(session_file_handler)

    logger.propagate = False
    return LoggingBundle(
        logger=logger,
        log_dir=log_dir,
        live_log_file=live_log_file,
        session_log_file=session_log_file,
    )
