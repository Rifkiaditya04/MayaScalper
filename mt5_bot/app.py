"""Bootstrap aplikasi."""

from contextlib import suppress

from mt5_bot.bot import MT5TradingBot
from mt5_bot.config import load_settings
from mt5_bot.logging_utils import configure_logging
from mt5_bot.mt5_client import MT5Client


def run() -> None:
    settings = load_settings()
    logging_bundle = configure_logging(settings)
    logger = logging_bundle.logger
    logger.info("Starting MT5 live rebuild for symbol=%s", settings.symbol)
    logger.info(
        "Log files ready | live=%s | session=%s",
        logging_bundle.live_log_file,
        logging_bundle.session_log_file,
    )

    client = MT5Client(settings=settings, logger=logger)
    try:
        bot = MT5TradingBot(settings=settings, client=client, logger=logger)
        bot.run_forever()
    finally:
        with suppress(Exception):
            client.shutdown()
