"""One-time Telegram login: python login_telegram.py

Asks for your phone number and the login code in the terminal and creates
the session file in data/. Run it once; the collector reuses the session.
"""

import asyncio
import os
from pathlib import Path

from app.collector.client import TelegramConfigError, create_client
from app.config import Config


async def main() -> None:
    client = create_client()
    await client.start()  # interactive: phone, code, 2FA password if enabled
    try:
        me = await client.get_me()
        print(f"Login OK. Account ID: {me.id}")
    finally:
        await client.disconnect()

    session_file = Path(Config.TG_SESSION_PATH + ".session")
    if session_file.exists():
        os.chmod(session_file, 0o600)  # owner-only: the session is like a password
        print(f"Session saved: {session_file}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except TelegramConfigError as exc:
        print(f"Config error: {exc}")
