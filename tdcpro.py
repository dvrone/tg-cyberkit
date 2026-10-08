#!/usr/bin/env python3

"""
TDC Pro V1.2
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import (BarColumn, Progress, SpinnerColumn,
                           TaskProgressColumn, TextColumn, TimeElapsedColumn)
from rich.table import Table
from rich.traceback import install
from sqlalchemy import (BigInteger, Boolean, DateTime, Index, Integer, String,
                        Text, create_engine, event, func, select)
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import Channel

# ============================================================
# Setup
# ============================================================

install()
console = Console()
load_dotenv()

API_ID = os.getenv("TG_API_ID")
API_HASH = os.getenv("TG_API_HASH")
SESSION_NAME = os.getenv("TG_SESSION", "tdc_pro")

DEFAULT_DB = "tdcpro.db"
DEFAULT_BATCH_SIZE = 200
MAX_BATCH_SIZE = 500  # stays below SQLite's IN (...) variable limit
MAX_FLOOD_RETRIES = 5
PROGRESS_EVERY = 100
CSV_CHUNK = 1000

CSV_FIELDS = [
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


# ============================================================
# Database models
# ============================================================


class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"

    # One row per (chat, message). Enforced by the database itself.
    __table_args__ = (
        Index(
            "uq_messages_chat_message",
            "chat_id",
            "message_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chat_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_id: Mapped[int] = mapped_column(Integer, index=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sender_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    reply_to_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_media: Mapped[bool] = mapped_column(Boolean, default=False)
    media_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    message_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


# ============================================================
# Small data holders
# ============================================================


@dataclass
class Stats:
    scanned: int = 0
    saved: int = 0
    duplicates: int = 0
    failed: int = 0


@dataclass(frozen=True)
class ChatMeta:
    """Chat fields computed once per chat, not once per message."""

    chat_id: int | None
    title: str | None
    username: str | None
    is_channel: bool

    @classmethod
    def from_entity(cls, chat) -> "ChatMeta":
        return cls(
            chat_id=getattr(chat, "id", None),
            title=getattr(chat, "title", None),
            username=getattr(chat, "username", None),
            is_channel=isinstance(chat, Channel),
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

    return normalize_datetime(dt)


def normalize_datetime(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def batch_size_type(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--batch-size must be an integer")

    if not 1 <= number <= MAX_BATCH_SIZE:
        raise argparse.ArgumentTypeError(
            f"--batch-size must be between 1 and {MAX_BATCH_SIZE}"
        )
    return number


def build_message_link(meta: ChatMeta, message_id: int) -> str | None:
    if meta.username:
        return f"https://t.me/{meta.username}/{message_id}"

    if meta.chat_id is None:
        return None

    chat_id_string = str(meta.chat_id)

    # Old-style marked id: -100XXXXXXXXXX
    if chat_id_string.startswith("-100"):
        return f"https://t.me/c/{chat_id_string[4:]}/{message_id}"

    # Telethon entities carry the bare channel id (no -100 prefix).
    if meta.is_channel:
        return f"https://t.me/c/{chat_id_string}/{message_id}"

    # Basic groups have no message links.
    return None


def build_row(message, meta: ChatMeta) -> Message:
    sender = message.sender

    reply_to_message_id = None
    if message.reply_to:
        reply_to_message_id = message.reply_to.reply_to_msg_id

    return Message(
        chat_id=meta.chat_id,
        chat_title=meta.title,
        chat_username=meta.username,
        message_id=message.id,
        date=normalize_datetime(message.date),
        sender_id=getattr(sender, "id", None),
        username=getattr(sender, "username", None),
        first_name=getattr(sender, "first_name", None),
        last_name=getattr(sender, "last_name", None),
        text=message.message or "",
        reply_to_message_id=reply_to_message_id,
        has_media=bool(message.media),
        media_type=type(message.media).__name__ if message.media else None,
        message_link=build_message_link(meta, message.id),
        collected_at=datetime.now(timezone.utc),
    )


# ============================================================
# Rich UI
# ============================================================


def print_banner():
    console.print(
        Panel.fit(
            "[bold cyan]TDC PRO[/bold cyan]\n"
            "[white]Telegram Data Collector V1.2[/white]\n\n"
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
    batch_size: int,
):
    table = Table(
        title="Collection Configuration",
        show_header=False,
        border_style="blue",
    )
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Chats", str(len(chats)))
    table.add_row("From", start_date.isoformat())
    table.add_row("To", end_date.isoformat())
    table.add_row("Database", db_path)
    table.add_row("CSV", csv_path or "Disabled")
    table.add_row("Batch size", str(batch_size))

    console.print(table)
    console.print()


def print_summary(chat_name: str, stats: Stats, elapsed: float):
    table = Table(
        title=f"Collection Summary — {chat_name}",
        border_style="green",
    )
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="green")

    table.add_row("Scanned", f"{stats.scanned:,}")
    table.add_row("Saved", f"{stats.saved:,}")
    table.add_row("Duplicates", f"{stats.duplicates:,}")

    if stats.failed:
        table.add_row("Failed", f"{stats.failed:,}")

    table.add_row("Time", f"{elapsed:.2f}s")

    if elapsed > 0:
        table.add_row("Rate", f"{stats.scanned / elapsed:,.1f} msg/s")

    console.print(table)
    console.print()


# ============================================================
# Database
# ============================================================


def create_database(db_path: str):
    engine = create_engine(f"sqlite:///{db_path}", echo=False)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    Base.metadata.create_all(engine)

    # create_all() does not add indexes to tables that already exist,
    # so make sure older databases get the unique index too.
    try:
        with engine.begin() as conn:
            conn.execute(
                sql_text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_messages_chat_message "
                    "ON messages (chat_id, message_id)"
                )
            )
    except Exception as exc:
        console.print(
            Panel(
                "Could not create the unique index "
                "(the database probably already contains duplicate "
                "rows).\nCollection still works, but duplicates are "
                "only prevented by the pre-insert check.\n\n"
                f"[dim]{exc}[/dim]",
                title="Warning",
                border_style="yellow",
            )
        )

    return engine


# ============================================================
# Save a batch of messages
# ============================================================


def flush_batch(
    session: Session,
    meta: ChatMeta,
    pending: list,
    stats: Stats,
) -> None:
    """
    Save all pending Telegram messages with ONE duplicate lookup
    and ONE commit, then clear the list.
    """

    if not pending:
        return

    ids = [m.id for m in pending]

    existing = set(
        session.scalars(
            select(Message.message_id).where(
                Message.chat_id == meta.chat_id,
                Message.message_id.in_(ids),
            )
        )
    )

    rows = []

    for message in pending:
        if message.id in existing:
            stats.duplicates += 1
            continue

        try:
            rows.append(build_row(message, meta))
        except Exception as exc:
            stats.failed += 1
            console.print(
                f"\n[red][!] Failed to process message " f"{message.id}: {exc}[/red]"
            )

    pending.clear()

    if not rows:
        return

    try:
        session.add_all(rows)
        session.commit()
        stats.saved += len(rows)

    except IntegrityError:
        # Rare: a row slipped past the pre-check (e.g. another
        # process wrote at the same time). Insert one by one.
        session.rollback()

        for row in rows:
            try:
                with session.begin_nested():
                    session.add(row)
                stats.saved += 1
            except IntegrityError:
                stats.duplicates += 1

        session.commit()


# ============================================================
# Collect one chat
# ============================================================


async def collect_chat(
    client: TelegramClient,
    session: Session,
    chat_input: str,
    start_date: datetime,
    end_date: datetime,
    batch_size: int,
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
    meta = ChatMeta.from_entity(chat)

    chat_name = meta.title or meta.username or str(meta.chat_id)

    stats = Stats()
    pending: list = []

    resume_id = 0  # oldest message id seen so far
    flood_retries = 0
    finished = False

    started = time.monotonic()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
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
            while not finished:

                # First pass: jump straight to --to using offset_date,
                # so newer messages are never downloaded.
                # After a FloodWait: continue below the last seen id.
                if resume_id:
                    iter_kwargs = {"offset_id": resume_id}
                else:
                    iter_kwargs = {"offset_date": end_date}

                try:
                    async for message in client.iter_messages(chat, **iter_kwargs):
                        stats.scanned += 1
                        resume_id = message.id

                        if stats.scanned % PROGRESS_EVERY == 0:
                            progress.update(
                                task,
                                description=(
                                    f"[cyan]{chat_name} [white]"
                                    f"scanned={stats.scanned:,} "
                                    f"saved={stats.saved:,}"
                                ),
                            )

                        if not message.date:
                            continue

                        msg_date = normalize_datetime(message.date)

                        # Safety net; offset_date already skips these.
                        if msg_date >= end_date:
                            continue

                        # History is newest -> oldest, so we are done.
                        if msg_date < start_date:
                            break

                        pending.append(message)

                        if len(pending) >= batch_size:
                            flush_batch(session, meta, pending, stats)

                    finished = True

                except FloodWaitError as exc:
                    flush_batch(session, meta, pending, stats)

                    flood_retries += 1

                    if flood_retries > MAX_FLOOD_RETRIES:
                        console.print(
                            Panel(
                                "Too many FloodWait errors. "
                                "This chat was only partially "
                                "collected; run the same command "
                                "again later to continue "
                                "(duplicates are skipped).",
                                title="Giving up",
                                border_style="red",
                            )
                        )
                        break

                    console.print(
                        Panel(
                            f"Telegram requested a wait of "
                            f"[yellow]{exc.seconds}[/yellow] seconds.\n"
                            f"Retry {flood_retries}/{MAX_FLOOD_RETRIES}, "
                            f"resuming below message {resume_id}.",
                            title="FloodWait",
                            border_style="yellow",
                        )
                    )

                    await asyncio.sleep(exc.seconds + 1)

        finally:
            # Also runs on Ctrl+C / errors, so at most the current
            # batch is at risk and normally nothing is lost.
            try:
                flush_batch(session, meta, pending, stats)
            except Exception:
                session.rollback()

    elapsed = time.monotonic() - started

    print_summary(chat_name, stats, elapsed)

    return {
        "chat": chat_name,
        "scanned": stats.scanned,
        "saved": stats.saved,
        "duplicates": stats.duplicates,
        "failed": stats.failed,
        "elapsed": elapsed,
    }


# ============================================================
# CSV export
# ============================================================


def export_csv(session: Session, output_path: str) -> int:
    console.print(
        Panel(
            "[cyan]Exporting SQLite data to CSV...[/cyan]",
            border_style="blue",
        )
    )

    total = session.scalar(select(func.count()).select_from(Message))

    if not total:
        console.print("[yellow][!] No messages found for export.[/yellow]")
        return 0

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    # Streamed in chunks: memory stays flat even for millions of rows.
    stmt = (
        select(Message)
        .order_by(Message.date.asc(), Message.id.asc())
        .execution_options(yield_per=CSV_CHUNK)
    )

    written = 0

    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(CSV_FIELDS)

        for row in session.scalars(stmt):
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
            written += 1

    console.print(f"[bold green][+] CSV exported:[/bold green] {output_path}")
    console.print(f"[dim]Rows: {written:,}[/dim]")

    return written


# ============================================================
# Argument parser
# ============================================================


def parse_args():
    parser = argparse.ArgumentParser(
        description="TDC Pro V1.2 — Telegram Data Collector"
    )

    parser.add_argument(
        "--chat",
        action="append",
        required=True,
        help="Authorized Telegram group/channel. " "Can be specified multiple times.",
    )

    parser.add_argument(
        "--from",
        dest="start",
        required=True,
        type=parse_datetime,
        help="Start datetime. Example: 2026-09-28T00:00:00+05:00",
    )

    parser.add_argument(
        "--to",
        dest="end",
        required=True,
        type=parse_datetime,
        help="End datetime. Example: 2026-10-05T00:00:00+05:00",
    )

    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"SQLite database path. Default: {DEFAULT_DB}",
    )

    parser.add_argument(
        "--csv",
        default=None,
        help="Export collected messages to CSV.",
    )

    parser.add_argument(
        "--batch-size",
        type=batch_size_type,
        default=DEFAULT_BATCH_SIZE,
        help=f"Messages saved per commit (1-{MAX_BATCH_SIZE}). "
        f"Default: {DEFAULT_BATCH_SIZE}",
    )

    return parser.parse_args()


# ============================================================
# Main
# ============================================================


def config_error(message: str, title: str = "Configuration Error"):
    console.print(
        Panel(
            f"[red]{message}[/red]",
            title=title,
            border_style="red",
        )
    )
    sys.exit(1)


async def main():
    args = parse_args()

    print_banner()

    # --------------------------------------------------------
    # Validate credentials and dates
    # --------------------------------------------------------

    if not API_ID:
        config_error("TG_API_ID is missing.\n\nAdd it to your .env file.")

    if not API_HASH:
        config_error("TG_API_HASH is missing.\n\nAdd it to your .env file.")

    try:
        api_id = int(API_ID)
    except ValueError:
        config_error("TG_API_ID must be an integer.")

    if args.start >= args.end:
        config_error("--from must be earlier than --to.", "Date Error")

    print_config(
        chats=args.chat,
        start_date=args.start,
        end_date=args.end,
        db_path=args.db,
        csv_path=args.csv,
        batch_size=args.batch_size,
    )

    # --------------------------------------------------------
    # Database
    # --------------------------------------------------------

    try:
        engine = create_database(args.db)
    except Exception as exc:
        config_error(str(exc), "Database Error")

    console.print(f"[green][+] Database ready:[/green] {args.db}")

    # --------------------------------------------------------
    # Telegram client
    # --------------------------------------------------------

    client = TelegramClient(SESSION_NAME, api_id, API_HASH)

    results = []

    try:
        async with client:
            console.print("[bold green][+] Telegram session connected.[/bold green]")

            with Session(engine) as session:

                for chat in args.chat:
                    try:
                        result = await collect_chat(
                            client=client,
                            session=session,
                            chat_input=chat,
                            start_date=args.start,
                            end_date=args.end,
                            batch_size=args.batch_size,
                        )
                        results.append(result)

                    except FloodWaitError as exc:
                        # Only reachable if get_entity() itself is limited.
                        console.print(
                            Panel(
                                f"Waiting {exc.seconds} seconds, "
                                f"then moving to the next chat.",
                                title=f"FloodWait: {chat}",
                                border_style="yellow",
                            )
                        )
                        await asyncio.sleep(exc.seconds + 1)

                    except Exception as exc:
                        session.rollback()
                        console.print(
                            Panel(
                                f"[red]{exc}[/red]",
                                title=f"Failed: {chat}",
                                border_style="red",
                            )
                        )

                if args.csv:
                    export_csv(session, args.csv)

    except KeyboardInterrupt:
        console.print()
        console.print(
            Panel(
                "[yellow]Collection interrupted by user.[/yellow]",
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

    console.print()

    final_table = Table(
        title="TDC Pro — Final Summary",
        border_style="cyan",
    )
    final_table.add_column("Metric", style="cyan")
    final_table.add_column("Value", justify="right", style="green")

    final_table.add_row("Chats processed", f"{len(results)}/{len(args.chat)}")
    final_table.add_row("Messages scanned", f"{sum(r['scanned'] for r in results):,}")
    final_table.add_row("Messages saved", f"{sum(r['saved'] for r in results):,}")
    final_table.add_row("Duplicates", f"{sum(r['duplicates'] for r in results):,}")

    total_failed = sum(r["failed"] for r in results)
    if total_failed:
        final_table.add_row("Failed", f"{total_failed:,}")

    final_table.add_row("Collection time", f"{sum(r['elapsed'] for r in results):.2f}s")

    console.print(final_table)
    console.print()

    console.print(
        Panel(
            "[bold green]Collection complete.[/bold green]\n\n"
            f"SQLite: [cyan]{args.db}[/cyan]\n"
            f"CSV: [cyan]{args.csv or 'not requested'}[/cyan]",
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
