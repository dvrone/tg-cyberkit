"""Telethon client factory."""

from telethon import TelegramClient

from app.config import DATA_DIR, Config


class TelegramConfigError(RuntimeError):
    """Raised when Telegram credentials are missing from .env."""


def create_client() -> TelegramClient:
    """Build a TelegramClient from .env settings.

    Call this INSIDE a running event loop (inside an async function run by
    asyncio.run), because Python 3.14 no longer creates a loop implicitly.
    """
    if Config.TG_API_ID is None or not Config.TG_API_HASH:
        raise TelegramConfigError(
            "TG_API_ID (a number) and TG_API_HASH must be set in the .env file."
        )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return TelegramClient(Config.TG_SESSION_PATH, Config.TG_API_ID, Config.TG_API_HASH)
