"""Application entrypoint for the TSP V1 implementation track."""

from __future__ import annotations

from .bot import TSPBot
from .config import load_config


def run() -> None:
    """Bootstrap the TSP implementation surface without starting live trading."""
    config = load_config()
    bot = TSPBot(config=config)
    bot.bootstrap()
