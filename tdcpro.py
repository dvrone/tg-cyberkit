#!/usr/bin/env python3

"""
TDC Pro V1.1
============

Telegram Data Collector

Stack:
    Telethon
    SQLAlchemy 2.x
    SQLite
    python-dotenv
    Rich

Purpose:
    Collect historical messages from authorized Telegram
    groups/channels for offline analysis.

This tool:
    - READS Telegram history
    - STORES messages locally
    - EXPORTS CSV
    - DOES NOT send messages
    - DOES NOT interact with users
    - DOES NOT perform trades
    - DOES NOT automatically classify users

Use only with Telegram chats/data you are authorized to access.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import (BarColumn, Progress, SpinnerColumn,
                           TaskProgressColumn, TextColumn, TimeElapsedColumn)
from rich.table import Table
from rich.traceback import install
from sqlalchemy import (BigInteger, Boolean, DateTime, Integer, String, Text,
                        create_engine, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from telethon import TelegramClient
from telethon.errors import FloodWaitError

# ============================================================
# Rich setup
# ============================================================

install()

console = Console()


# ============================================================
# Environment
# ============================================================

load_dotenv()

API_ID = os.getenv("TG_API_ID")
API_HASH = os.getenv("TG_API_HASH")

SESSION_NAME = os.getenv(
    "TG_SESSION",
    "tdc_pro",
)

DEFAULT_DB = "tdcpro.db"


# ============================================================
# Database models
# ============================================================


class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
    )

    chat_title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    chat_username: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    message_id: Mapped[int] = mapped_column(
        Integer,
        index=True,
    )

    date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )

    sender_id: Mapped[int | None] = mapped_column(
        BigInteger,
        index=True,
        nullable=True,
    )

    username: Mapped[str | None] = mapped_column(
        String(255),
        index=True,
        nullable=True,
    )

    first_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    last_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    text: Mapped[str] = mapped_column(
        Text,
        default="",
    )

    reply_to_message_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    has_media: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    media_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    message_link: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


# ============================================================
# Utility functions
# ============================================================


def parse_datetime(value: str) -> datetime:
    """
    Parse ISO-8601 datetime.

    Examples:

        2026-10-01

        2026-10-01T12:30:00

        2026-10-01T12:30:00+05:00
    """

    try:
        dt = datetime.fromisoformat(value)

    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid datetime: {value}")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def normalize_datetime(
    dt: datetime,
) -> datetime:

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def get_message_text(message) -> str:

    return message.message or ""


def get_media_type(message) -> str | None:

    if not message.media:
        return None

    return type(message.media).__name__


def build_message_link(
    chat,
    message_id: int,
) -> str | None:

    username = getattr(
        chat,
        "username",
        None,
    )

    if username:
        return f"https://t.me/" f"{username}/" f"{message_id}"

    chat_id = getattr(
        chat,
        "id",
        None,
    )

    if chat_id is None:
        return None

    chat_id_string = str(chat_id)

    if chat_id_string.startswith("-100"):

        internal_id = chat_id_string[4:]

        return f"https://t.me/c/" f"{internal_id}/" f"{message_id}"

    return None


# ============================================================
# Rich UI
# ============================================================


def print_banner():

    console.print(
        Panel.fit(
            "[bold cyan]TDC PRO[/bold cyan]\n"
            "[white]Telegram Data Collector V1.1[/white]\n\n"
            "[dim]Telethon + SQLAlchemy + SQLite + Rich[/dim]",
            border_style="cyan",
            padding=(1, 4),
        )
    )


def print_config(
    chats: list[str],
    start_date: datetime,
    end_date: datetime,
    db_path: str,
    csv_path: str | None,
):

    table = Table(
        title="Collection Configuration",
        show_header=False,
        border_style="blue",
    )

    table.add_column(
        "Setting",
        style="cyan",
    )

    table.add_column(
        "Value",
        style="white",
    )

    table.add_row(
        "Chats",
        str(len(chats)),
    )

    table.add_row(
        "From",
        start_date.isoformat(),
    )

    table.add_row(
        "To",
        end_date.isoformat(),
    )

    table.add_row(
        "Database",
        db_path,
    )

    table.add_row(
        "CSV",
        csv_path or "Disabled",
    )

    console.print(table)
    console.print()


def print_summary(
    chat_name: str,
    scanned: int,
    saved: int,
    duplicates: int,
    elapsed: float,
):

    table = Table(
        title=f"Collection Summary — {chat_name}",
        border_style="green",
    )

    table.add_column(
        "Metric",
        style="cyan",
    )

    table.add_column(
        "Value",
        justify="right",
        style="green",
    )

    table.add_row(
        "Scanned",
        f"{scanned:,}",
    )

    table.add_row(
        "Saved",
        f"{saved:,}",
    )

    table.add_row(
        "Duplicates",
        f"{duplicates:,}",
    )

    table.add_row(
        "Time",
        f"{elapsed:.2f}s",
    )

    if elapsed > 0:

        rate = scanned / elapsed

        table.add_row(
            "Rate",
            f"{rate:,.1f} msg/s",
        )

    console.print(table)
    console.print()


# ============================================================
# Database
# ============================================================


def create_database(
    db_path: str,
):

    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
    )

    Base.metadata.create_all(engine)

    return engine


# ============================================================
# Save message
# ============================================================


def save_message(
    session: Session,
    message,
    chat,
) -> bool:

    existing = session.scalar(
        select(Message.id).where(
            Message.chat_id == chat.id,
            Message.message_id == message.id,
        )
    )

    if existing is not None:
        return False

    sender = message.sender

    sender_id = None
    username = None
    first_name = None
    last_name = None

    if sender:

        sender_id = getattr(
            sender,
            "id",
            None,
        )

        username = getattr(
            sender,
            "username",
            None,
        )

        first_name = getattr(
            sender,
            "first_name",
            None,
        )

        last_name = getattr(
            sender,
            "last_name",
            None,
        )

    reply_to_message_id = None

    if message.reply_to:

        reply_to_message_id = message.reply_to.reply_to_msg_id

    row = Message(
        chat_id=chat.id,
        chat_title=getattr(
            chat,
            "title",
            None,
        ),
        chat_username=getattr(
            chat,
            "username",
            None,
        ),
        message_id=message.id,
        date=normalize_datetime(message.date),
        sender_id=sender_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        text=get_message_text(message),
        reply_to_message_id=(reply_to_message_id),
        has_media=bool(message.media),
        media_type=get_media_type(message),
        message_link=build_message_link(
            chat,
            message.id,
        ),
        collected_at=datetime.now(timezone.utc),
    )

    session.add(row)

    return True


# ============================================================
# Collect one chat
# ============================================================


async def collect_chat(
    client: TelegramClient,
    session: Session,
    chat_input: str,
    start_date: datetime,
    end_date: datetime,
):

    console.print(
        Panel(
            f"[bold]Chat:[/bold] {chat_input}\n"
            f"[bold]From:[/bold] {start_date.isoformat()}\n"
            f"[bold]To:[/bold]   {end_date.isoformat()}",
            title="Collection Started",
            border_style="cyan",
        )
    )

    chat = await client.get_entity(chat_input)

    chat_name = (
        getattr(chat, "title", None) or getattr(chat, "username", None) or str(chat.id)
    )

    scanned = 0
    saved = 0
    duplicates = 0

    started = time.monotonic()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]" "{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:

        task = progress.add_task(
            f"[cyan]Collecting {chat_name}",
            total=None,
        )

        try:

            async for message in client.iter_messages(
                chat,
                reverse=False,
            ):

                scanned += 1

                progress.update(
                    task,
                    description=(
                        f"[cyan]"
                        f"{chat_name} "
                        f"[white]"
                        f"scanned={scanned:,} "
                        f"saved={saved:,}"
                    ),
                    advance=1,
                )

                if not message.date:
                    continue

                msg_date = normalize_datetime(message.date)

                # History is newest -> oldest.
                #
                # Skip messages newer than our
                # requested end date.
                if msg_date >= end_date:
                    continue

                # Once we reach messages older
                # than start_date, stop.
                if msg_date < start_date:
                    break

                try:

                    inserted = save_message(
                        session,
                        message,
                        chat,
                    )

                    if inserted:
                        saved += 1
                    else:
                        duplicates += 1

                except Exception as exc:

                    console.print(
                        f"\n[red][!] Failed to save "
                        f"message {message.id}: "
                        f"{exc}[/red]"
                    )

                # Commit every 100 messages.
                if scanned % 100 == 0:

                    session.commit()

        except FloodWaitError as exc:

            session.commit()

            console.print()

            console.print(
                Panel(
                    f"Telegram requested a wait of "
                    f"[yellow]{exc.seconds}[/yellow] seconds.",
                    title="FloodWait",
                    border_style="yellow",
                )
            )

            await asyncio.sleep(exc.seconds)

        except Exception:

            session.rollback()

            raise

        finally:

            session.commit()

    elapsed = time.monotonic() - started

    print_summary(
        chat_name=chat_name,
        scanned=scanned,
        saved=saved,
        duplicates=duplicates,
        elapsed=elapsed,
    )

    return {
        "chat": chat_name,
        "scanned": scanned,
        "saved": saved,
        "duplicates": duplicates,
        "elapsed": elapsed,
    }


# ============================================================
# CSV export
# ============================================================


def export_csv(
    session: Session,
    output_path: str,
):

    console.print(
        Panel(
            "[cyan]Exporting SQLite data to CSV...[/cyan]",
            border_style="blue",
        )
    )

    rows = session.scalars(select(Message).order_by(Message.date.asc())).all()

    if not rows:

        console.print("[yellow][!] No messages " "found for export.[/yellow]")

        return 0

    fields = [
        "id",
        "chat_id",
        "chat_title",
        "chat_username",
        "message_id",
        "date",
        "sender_id",
        "username",
        "first_name",
        "last_name",
        "text",
        "reply_to_message_id",
        "has_media",
        "media_type",
        "message_link",
        "collected_at",
    ]

    output = Path(output_path)

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.writer(file)

        writer.writerow(fields)

        for row in rows:

            writer.writerow(
                [
                    row.id,
                    row.chat_id,
                    row.chat_title,
                    row.chat_username,
                    row.message_id,
                    row.date.isoformat() if row.date else "",
                    row.sender_id,
                    row.username,
                    row.first_name,
                    row.last_name,
                    row.text,
                    row.reply_to_message_id,
                    row.has_media,
                    row.media_type,
                    row.message_link,
                    row.collected_at.isoformat() if row.collected_at else "",
                ]
            )

    console.print(f"[bold green][+] CSV exported:[/bold green] " f"{output_path}")

    console.print(f"[dim]Rows: {len(rows):,}[/dim]")

    return len(rows)


# ============================================================
# Argument parser
# ============================================================


def parse_args():

    parser = argparse.ArgumentParser(
        description=("TDC Pro V1.1 — " "Telegram Data Collector")
    )

    parser.add_argument(
        "--chat",
        action="append",
        required=True,
        help=("Authorized Telegram group/channel. " "Can be specified multiple times."),
    )

    parser.add_argument(
        "--from",
        dest="start",
        required=True,
        type=parse_datetime,
        help=("Start datetime. Example: " "2026-09-28T00:00:00+05:00"),
    )

    parser.add_argument(
        "--to",
        dest="end",
        required=True,
        type=parse_datetime,
        help=("End datetime. Example: " "2026-10-05T00:00:00+05:00"),
    )

    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=("SQLite database path. " f"Default: {DEFAULT_DB}"),
    )

    parser.add_argument(
        "--csv",
        default=None,
        help=("Export collected messages " "to CSV."),
    )

    return parser.parse_args()


# ============================================================
# Main
# ============================================================


async def main():

    args = parse_args()

    print_banner()

    # --------------------------------------------------------
    # Validate credentials
    # --------------------------------------------------------

    if not API_ID:

        console.print(
            Panel(
                "[red]TG_API_ID is missing.[/red]\n\n" "Add it to your .env file.",
                title="Configuration Error",
                border_style="red",
            )
        )

        sys.exit(1)

    if not API_HASH:

        console.print(
            Panel(
                "[red]TG_API_HASH is missing.[/red]\n\n" "Add it to your .env file.",
                title="Configuration Error",
                border_style="red",
            )
        )

        sys.exit(1)

    try:

        api_id = int(API_ID)

    except ValueError:

        console.print("[red][!] TG_API_ID must " "be an integer.[/red]")

        sys.exit(1)

    # --------------------------------------------------------
    # Validate date range
    # --------------------------------------------------------

    if args.start >= args.end:

        console.print(
            Panel(
                "[red]--from must be earlier " "than --to.[/red]",
                title="Date Error",
                border_style="red",
            )
        )

        sys.exit(1)

    # --------------------------------------------------------
    # Print configuration
    # --------------------------------------------------------

    print_config(
        chats=args.chat,
        start_date=args.start,
        end_date=args.end,
        db_path=args.db,
        csv_path=args.csv,
    )

    # --------------------------------------------------------
    # Database
    # --------------------------------------------------------

    try:

        engine = create_database(args.db)

    except Exception as exc:

        console.print(
            Panel(
                f"[red]{exc}[/red]",
                title="Database Error",
                border_style="red",
            )
        )

        sys.exit(1)

    console.print(f"[green][+] Database ready:[/green] " f"{args.db}")

    # --------------------------------------------------------
    # Telegram client
    # --------------------------------------------------------

    client = TelegramClient(
        SESSION_NAME,
        api_id,
        API_HASH,
    )

    results = []

    try:

        async with client:

            console.print(
                "[bold green]" "[+] Telegram session connected." "[/bold green]"
            )

            with Session(engine) as session:

                for chat in args.chat:

                    try:

                        result = await collect_chat(
                            client=client,
                            session=session,
                            chat_input=chat,
                            start_date=args.start,
                            end_date=args.end,
                        )

                        results.append(result)

                    except FloodWaitError as exc:

                        console.print(
                            Panel(
                                f"Waiting " f"{exc.seconds} seconds.",
                                title="FloodWait",
                                border_style="yellow",
                            )
                        )

                        await asyncio.sleep(exc.seconds)

                    except Exception as exc:

                        console.print(
                            Panel(
                                f"[red]{exc}[/red]",
                                title=(f"Failed: {chat}"),
                                border_style="red",
                            )
                        )

                # ------------------------------------------------
                # CSV
                # ------------------------------------------------

                if args.csv:

                    export_csv(
                        session,
                        args.csv,
                    )

    except KeyboardInterrupt:

        console.print()

        console.print(
            Panel(
                "[yellow]" "Collection interrupted by user." "[/yellow]",
                title="Interrupted",
                border_style="yellow",
            )
        )

        return

    except Exception as exc:

        console.print(
            Panel(
                f"[red]{exc}[/red]",
                title="Fatal Error",
                border_style="red",
            )
        )

        sys.exit(1)

    # ========================================================
    # Final summary
    # ========================================================

    total_scanned = sum(item["scanned"] for item in results)

    total_saved = sum(item["saved"] for item in results)

    total_duplicates = sum(item["duplicates"] for item in results)

    total_time = sum(item["elapsed"] for item in results)

    console.print()

    final_table = Table(
        title="TDC Pro — Final Summary",
        border_style="cyan",
    )

    final_table.add_column(
        "Metric",
        style="cyan",
    )

    final_table.add_column(
        "Value",
        justify="right",
        style="green",
    )

    final_table.add_row(
        "Chats processed",
        str(len(results)),
    )

    final_table.add_row(
        "Messages scanned",
        f"{total_scanned:,}",
    )

    final_table.add_row(
        "Messages saved",
        f"{total_saved:,}",
    )

    final_table.add_row(
        "Duplicates",
        f"{total_duplicates:,}",
    )

    final_table.add_row(
        "Collection time",
        f"{total_time:.2f}s",
    )

    console.print(final_table)

    console.print()

    console.print(
        Panel(
            "[bold green]Collection complete.[/bold green]\n\n"
            f"SQLite: [cyan]{args.db}[/cyan]\n"
            f"CSV: "
            f"[cyan]{args.csv or 'not requested'}[/cyan]",
            title="Done",
            border_style="green",
        )
    )


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        console.print("\n[yellow][!] Interrupted.[/yellow]")

        sys.exit(130)
