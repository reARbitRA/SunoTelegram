"""Entry point for the Suno Music Downloader Telegram bot.

Usage::

    python main.py

Reads configuration from environment variables / a ``.env`` file (see
``.env.example``). The most important one is ``TELEGRAM_BOT_TOKEN``.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from sunobot.bot import SunoBot
from sunobot.config import Settings


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=getattr(logging, level, logging.INFO),
    )
    for noisy in ("httpx", "urllib3", "charset_normalizer", "aiohttp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def _async_main() -> None:
    settings = Settings.from_env()
    _setup_logging(settings.log_level)
    bot = SunoBot(settings)

    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        logging.getLogger(__name__).info("Shutdown signal received.")
        loop.create_task(bot.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # pragma: no cover - some platforms
            pass

    await bot.run()


def main() -> None:
    try:
        asyncio.run(_async_main())
    except ValueError as exc:  # config problems (e.g. missing token)
        print(f"\n[config error] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
