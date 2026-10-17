"""Application configuration.

Values come from environment variables (.env) with safe defaults.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = BASE_DIR / "data"

load_dotenv(BASE_DIR / ".env")


def _int_env(name: str) -> int | None:
    """Read an integer environment variable, or None if missing/invalid."""
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.isdigit() else None


class Config:
    """Default configuration."""

    # Absolute path -> "sqlite:////abs/path.db" (four slashes is correct).
    SQLALCHEMY_DATABASE_URI: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{DATA_DIR / 'tg_cyberkit.db'}"
    )

    # Telegram credentials (from .env). Never print these.
    TG_API_ID: int | None = _int_env("TG_API_ID")
    TG_API_HASH: str | None = os.getenv("TG_API_HASH") or None
    # Telethon adds ".session" itself -> data/tg_cyberkit.session
    TG_SESSION_PATH: str = os.getenv("TG_SESSION_PATH", str(DATA_DIR / "tg_cyberkit"))
