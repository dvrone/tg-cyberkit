#!/usr/bin/env python3

"""
TDC Pro Analyzer V1.1
=====================

Telegram OTC message analyzer.

Input:
    --db  SQLite database
    --csv CSV file

LLM:
    Ollama
    Qwen3.5 4B Q4_K_M

Output:
    --db  -> analysis_results table + optional CSV
    --csv -> output CSV

Example:

    python analyzer/analyzer.py \
        --db data/otc.db \
        --model qwen3.5:4b-q4_K_M \
        --limit 20

    python analyzer/analyzer.py \
        --csv exports/otc.csv \
        --output exports/analysis.csv \
        --limit 20
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from rich.console import Console
from rich.panel import Panel
from rich.progress import (BarColumn, Progress, SpinnerColumn,
                           TaskProgressColumn, TextColumn, TimeElapsedColumn)
from rich.table import Table
from sqlalchemy import (BigInteger, Boolean, DateTime, ForeignKey, Integer,
                        String, Text, create_engine, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

# ============================================================
# CONFIG
# ============================================================

OLLAMA_URL = "http://127.0.0.1:11434"

DEFAULT_MODEL = "qwen3.5:4b-q4_K_M"

DEFAULT_OUTPUT = "exports/analysis_results.csv"

OLLAMA_TIMEOUT = 300

console = Console()


# ============================================================
# DATABASE
# ============================================================


class Base(DeclarativeBase):
    pass


class Message(Base):

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
    )

    chat_title: Mapped[str | None] = mapped_column(String(255))

    chat_username: Mapped[str | None] = mapped_column(String(255))

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
    )

    username: Mapped[str | None] = mapped_column(
        String(255),
        index=True,
    )

    first_name: Mapped[str | None] = mapped_column(String(255))

    last_name: Mapped[str | None] = mapped_column(String(255))

    text: Mapped[str] = mapped_column(
        Text,
        default="",
    )

    reply_to_message_id: Mapped[int | None] = mapped_column(Integer)

    has_media: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    media_type: Mapped[str | None] = mapped_column(String(100))

    message_link: Mapped[str | None] = mapped_column(String(500))

    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AnalysisResult(Base):

    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    message_id: Mapped[int] = mapped_column(
        Integer,
        index=True,
    )

    classification: Mapped[str] = mapped_column(
        String(30),
    )

    risk_score: Mapped[int] = mapped_column(
        Integer,
    )

    indicators: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )

    reason: Mapped[str] = mapped_column(
        Text,
        default="",
    )

    model: Mapped[str] = mapped_column(
        String(100),
    )

    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )


# ============================================================
# CSV MESSAGE OBJECT
# ============================================================


@dataclass
class InputMessage:

    id: int

    chat_title: str

    chat_username: str

    username: str

    date: str

    text: str

    message_link: str = ""


# ============================================================
# DATABASE ENGINE
# ============================================================


def create_database(db_path: str):

    path = Path(db_path)

    if not path.exists():
        console.print(f"[red][!] Database not found:[/red] {path}")
        sys.exit(1)

    engine = create_engine(
        f"sqlite:///{path}",
        echo=False,
    )

    Base.metadata.create_all(engine)

    return engine


# ============================================================
# CSV LOADER
# ============================================================


def find_value(
    row: dict,
    names: list[str],
    default: str = "",
) -> str:

    normalized = {str(k).strip().lower(): v for k, v in row.items()}

    for name in names:

        value = normalized.get(name.lower())

        if value is not None:
            return str(value)

    return default


def load_csv(
    csv_path: str,
) -> list[InputMessage]:

    path = Path(csv_path)

    if not path.exists():

        console.print(f"[red][!] CSV not found:[/red] {path}")

        sys.exit(1)

    messages = []

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        reader = csv.DictReader(file)

        if not reader.fieldnames:

            console.print("[red][!] CSV has no header.[/red]")

            sys.exit(1)

        for index, row in enumerate(
            reader,
            start=1,
        ):

            text = find_value(
                row,
                [
                    "text",
                    "message",
                    "message_text",
                    "content",
                ],
            )

            chat_title = find_value(
                row,
                [
                    "chat_title",
                    "chat",
                    "group",
                    "group_name",
                ],
                "unknown",
            )

            chat_username = find_value(
                row,
                [
                    "chat_username",
                    "group_username",
                ],
                "",
            )

            username = find_value(
                row,
                [
                    "username",
                    "sender_username",
                    "user",
                ],
                "unknown",
            )

            date = find_value(
                row,
                [
                    "date",
                    "message_date",
                    "timestamp",
                ],
                "unknown",
            )

            message_link = find_value(
                row,
                [
                    "message_link",
                    "link",
                    "url",
                ],
                "",
            )

            raw_id = find_value(
                row,
                [
                    "id",
                    "message_id",
                ],
                str(index),
            )

            try:
                message_id = int(raw_id)
            except ValueError:
                message_id = index

            messages.append(
                InputMessage(
                    id=message_id,
                    chat_title=chat_title,
                    chat_username=chat_username,
                    username=username,
                    date=date,
                    text=text,
                    message_link=message_link,
                )
            )

    return messages


# ============================================================
# PREFILTER
# ============================================================

SUSPICIOUS_PATTERNS = [
    (
        "url",
        re.compile(
            r"https?://\S+|" r"www\.\S+|" r"t\.me/\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "crypto_address",
        re.compile(
            r"\b("
            r"0x[a-fA-F0-9]{40}|"
            r"T[a-zA-Z0-9]{33}|"
            r"[13][a-km-zA-HJ-NP-Z1-9]{25,34}"
            r")\b"
        ),
    ),
    (
        "telegram_contact",
        re.compile(
            r"@\w{4,}",
            re.IGNORECASE,
        ),
    ),
    (
        "contact_request",
        re.compile(
            r"\b("
            r"dm|"
            r"direct|"
            r"lichka|"
            r"lichkaga|"
            r"lichkada|"
            r"yoz|"
            r"yozing|"
            r"contact|"
            r"telegram|"
            r"whatsapp|"
            r"signal"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "urgency",
        re.compile(
            r"\b("
            r"tez|"
            r"tezda|"
            r"shoshilinch|"
            r"urgent|"
            r"asap|"
            r"quick|"
            r"hozir|"
            r"darhol|"
            r"immediately|"
            r"zudlik"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "payment",
        re.compile(
            r"\b("
            r"usdt|"
            r"usdc|"
            r"btc|"
            r"bitcoin|"
            r"eth|"
            r"ethereum|"
            r"bnb|"
            r"ton|"
            r"trx|"
            r"crypto|"
            r"wallet|"
            r"hamyon|"
            r"karta|"
            r"card|"
            r"bank|"
            r"transfer|"
            r"payment|"
            r"to['’]lov|"
            r"pul"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "amount",
        re.compile(r"\b\d{4,}\b"),
    ),
    (
        "secretive_language",
        re.compile(
            r"\b("
            r"hech kimga aytma|"
            r"aytma|"
            r"yashirin|"
            r"secret|"
            r"don't tell|"
            r"do not tell|"
            r"nobody knows|"
            r"gapirma"
            r")\b",
            re.IGNORECASE,
        ),
    ),
]


def prefilter_message(
    text: str,
) -> tuple[bool, list[str]]:

    if not text:
        return False, []

    indicators = []

    for name, pattern in SUSPICIOUS_PATTERNS:

        if pattern.search(text):

            indicators.append(name)

    return (
        bool(indicators),
        indicators,
    )


# ============================================================
# OLLAMA
# ============================================================


def check_ollama() -> bool:

    try:

        response = requests.get(
            f"{OLLAMA_URL}/api/tags",
            timeout=5,
        )

        response.raise_for_status()

        return True

    except requests.RequestException:

        return False


def check_model(
    model: str,
) -> bool:

    try:

        response = requests.get(
            f"{OLLAMA_URL}/api/tags",
            timeout=5,
        )

        response.raise_for_status()

        data = response.json()

        models = [
            item.get("name", "")
            for item in data.get(
                "models",
                [],
            )
        ]

        return model in models

    except (
        requests.RequestException,
        ValueError,
    ):

        return False


# ============================================================
# PROMPT
# ============================================================


def build_prompt(
    message,
    indicators: list[str],
) -> str:

    text = message.text if hasattr(message, "text") else ""

    chat_title = getattr(
        message,
        "chat_title",
        "unknown",
    )

    username = getattr(
        message,
        "username",
        "unknown",
    )

    date = getattr(
        message,
        "date",
        "unknown",
    )

    return f"""
You are a cybersecurity message triage assistant
for authorized Telegram OTC monitoring.

Analyze the message below.

IMPORTANT:
- Do not identify or accuse a person as a criminal.
- Identify observable risk indicators only.
- Do not invent facts.
- If evidence is insufficient, use NEEDS_REVIEW.
- Return ONLY valid JSON.
- Do not use Markdown.
- Do not include a code fence.

Allowed classifications:

BENIGN
SUSPICIOUS
HIGH_RISK
NEEDS_REVIEW

Risk score:
0 = no obvious risk
100 = extremely concerning

Required JSON:

{{
  "classification": "BENIGN",
  "risk_score": 0,
  "indicators": [],
  "reason": "short evidence-based explanation"
}}

Message metadata:

Chat:
{chat_title}

Username:
{username}

Date:
{date}

Prefilter indicators:
{json.dumps(indicators, ensure_ascii=False)}

Message:

{text}
""".strip()


# ============================================================
# LLM ANALYSIS
# ============================================================


def analyze_with_ollama(
    prompt: str,
    model: str,
):

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_ctx": 4096,
        },
    }

    response = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json=payload,
        timeout=OLLAMA_TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    raw = data.get(
        "response",
        "",
    )

    if not raw:
        raise RuntimeError("Ollama returned empty response.")

    try:

        result = json.loads(raw)

    except json.JSONDecodeError as exc:

        raise RuntimeError(f"Invalid JSON returned by model: " f"{raw[:500]}") from exc

    return result, data


# ============================================================
# VALIDATION
# ============================================================

ALLOWED_CLASSES = {
    "BENIGN",
    "SUSPICIOUS",
    "HIGH_RISK",
    "NEEDS_REVIEW",
}


def validate_result(
    result: dict,
):

    classification = (
        str(
            result.get(
                "classification",
                "NEEDS_REVIEW",
            )
        )
        .upper()
        .strip()
    )

    if classification not in ALLOWED_CLASSES:

        classification = "NEEDS_REVIEW"

    try:

        risk_score = int(
            result.get(
                "risk_score",
                50,
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        risk_score = 50

    risk_score = max(
        0,
        min(
            100,
            risk_score,
        ),
    )

    indicators = result.get(
        "indicators",
        [],
    )

    if not isinstance(
        indicators,
        list,
    ):

        indicators = [str(indicators)]

    indicators = [str(x).strip() for x in indicators if str(x).strip()]

    reason = str(
        result.get(
            "reason",
            "",
        )
    ).strip()

    return (
        classification,
        risk_score,
        indicators,
        reason,
    )


# ============================================================
# OUTPUT CSV
# ============================================================

CSV_FIELDS = [
    "message_id",
    "chat_title",
    "chat_username",
    "username",
    "date",
    "message_link",
    "text",
    "prefilter_indicators",
    "classification",
    "risk_score",
    "llm_indicators",
    "reason",
    "model",
    "analyzed_at",
]


def ensure_parent_directory(
    path: str,
):

    parent = Path(path).parent

    if parent != Path("."):

        parent.mkdir(
            parents=True,
            exist_ok=True,
        )


def write_csv_results(
    path: str,
    results: list[dict],
):

    ensure_parent_directory(path)

    file_exists = Path(path).exists()

    with open(
        path,
        "a",
        encoding="utf-8",
        newline="",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=CSV_FIELDS,
        )

        if not file_exists:

            writer.writeheader()

        writer.writerows(results)


# ============================================================
# DB RESULT CHECK
# ============================================================


def already_analyzed(
    session: Session,
    message_id: int,
    model: str,
) -> bool:

    result = session.scalar(
        select(AnalysisResult.id).where(
            AnalysisResult.message_id == message_id,
            AnalysisResult.model == model,
        )
    )

    return result is not None


# ============================================================
# DB MODE
# ============================================================


def run_database_mode(
    db_path: str,
    model: str,
    limit: int | None,
    output: str | None,
):

    engine = create_database(db_path)

    with Session(engine) as session:

        query = select(Message).order_by(Message.date.asc())

        if limit:
            query = query.limit(limit)

        messages = session.scalars(query).all()

        if not messages:

            console.print("[yellow]No messages found.[/yellow]")

            return

        candidates = []

        for message in messages:

            matched, indicators = prefilter_message(message.text)

            if matched:

                candidates.append(
                    (
                        message,
                        indicators,
                    )
                )

        print_prefilter_summary(
            len(messages),
            len(candidates),
        )

        if not candidates:
            return

        output_rows = []

        analyzed = 0
        skipped = 0
        errors = 0

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
                "[cyan]Analyzing...",
                total=len(candidates),
            )

            for message, prefilter in candidates:

                if already_analyzed(
                    session,
                    message.id,
                    model,
                ):

                    skipped += 1

                    progress.advance(task)

                    continue

                try:

                    prompt = build_prompt(
                        message,
                        prefilter,
                    )

                    result, raw_data = analyze_with_ollama(
                        prompt,
                        model,
                    )

                    (
                        classification,
                        risk_score,
                        llm_indicators,
                        reason,
                    ) = validate_result(result)

                    combined = list(dict.fromkeys(prefilter + llm_indicators))

                    analysis = AnalysisResult(
                        message_id=message.id,
                        classification=classification,
                        risk_score=risk_score,
                        indicators=json.dumps(
                            combined,
                            ensure_ascii=False,
                        ),
                        reason=reason,
                        model=model,
                        analyzed_at=datetime.now(timezone.utc),
                    )

                    session.add(analysis)
                    session.commit()

                    output_rows.append(
                        make_output_row(
                            message,
                            prefilter,
                            classification,
                            risk_score,
                            llm_indicators,
                            reason,
                            model,
                        )
                    )

                    analyzed += 1

                    progress.update(
                        task,
                        description=(
                            f"[cyan]Analyzing " f"{analyzed:,}/" f"{len(candidates):,}"
                        ),
                    )

                except Exception as exc:

                    session.rollback()

                    errors += 1

                    console.print(
                        f"\n[red]" f"[!] Message " f"{message.id}: " f"{exc}" f"[/red]"
                    )

                progress.advance(task)

        if output and output_rows:

            write_csv_results(
                output,
                output_rows,
            )

        elapsed = time.monotonic() - started

        print_summary(
            analyzed=analyzed,
            skipped=skipped,
            errors=errors,
            elapsed=elapsed,
            results=output_rows,
        )


# ============================================================
# CSV MODE
# ============================================================


def run_csv_mode(
    csv_path: str,
    model: str,
    limit: int | None,
    output: str,
):

    messages = load_csv(csv_path)

    if limit:

        messages = messages[:limit]

    if not messages:

        console.print("[yellow]No messages found.[/yellow]")

        return

    candidates = []

    for message in messages:

        matched, indicators = prefilter_message(message.text)

        if matched:

            candidates.append(
                (
                    message,
                    indicators,
                )
            )

    print_prefilter_summary(
        len(messages),
        len(candidates),
    )

    if not candidates:
        return

    output_rows = []

    analyzed = 0
    errors = 0

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
            "[cyan]Analyzing...",
            total=len(candidates),
        )

        for message, prefilter in candidates:

            try:

                prompt = build_prompt(
                    message,
                    prefilter,
                )

                result, raw_data = analyze_with_ollama(
                    prompt,
                    model,
                )

                (
                    classification,
                    risk_score,
                    llm_indicators,
                    reason,
                ) = validate_result(result)

                output_rows.append(
                    make_output_row(
                        message,
                        prefilter,
                        classification,
                        risk_score,
                        llm_indicators,
                        reason,
                        model,
                    )
                )

                analyzed += 1

                progress.update(
                    task,
                    description=(
                        f"[cyan]Analyzing " f"{analyzed:,}/" f"{len(candidates):,}"
                    ),
                )

            except Exception as exc:

                errors += 1

                console.print(
                    f"\n[red]" f"[!] Message " f"{message.id}: " f"{exc}" f"[/red]"
                )

            progress.advance(task)

    if output_rows:

        write_csv_results(
            output,
            output_rows,
        )

    elapsed = time.monotonic() - started

    print_summary(
        analyzed=analyzed,
        skipped=0,
        errors=errors,
        elapsed=elapsed,
        results=output_rows,
    )


# ============================================================
# OUTPUT ROW
# ============================================================


def make_output_row(
    message,
    prefilter: list[str],
    classification: str,
    risk_score: int,
    llm_indicators: list[str],
    reason: str,
    model: str,
):

    return {
        "message_id": message.id,
        "chat_title": getattr(
            message,
            "chat_title",
            "",
        ),
        "chat_username": getattr(
            message,
            "chat_username",
            "",
        ),
        "username": getattr(
            message,
            "username",
            "",
        ),
        "date": str(
            getattr(
                message,
                "date",
                "",
            )
        ),
        "message_link": getattr(
            message,
            "message_link",
            "",
        ),
        "text": getattr(
            message,
            "text",
            "",
        ),
        "prefilter_indicators": json.dumps(
            prefilter,
            ensure_ascii=False,
        ),
        "classification": classification,
        "risk_score": risk_score,
        "llm_indicators": json.dumps(
            llm_indicators,
            ensure_ascii=False,
        ),
        "reason": reason,
        "model": model,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# DISPLAY
# ============================================================


def print_prefilter_summary(
    total: int,
    candidates: int,
):

    filtered = total - candidates

    console.print()

    console.print(
        Panel(
            f"Messages: [cyan]{total:,}[/cyan]\n"
            f"Candidates: [yellow]{candidates:,}[/yellow]\n"
            f"Filtered: [green]{filtered:,}[/green]",
            title="Prefilter",
            border_style="blue",
        )
    )


def print_summary(
    analyzed: int,
    skipped: int,
    errors: int,
    elapsed: float,
    results: list[dict],
):

    counts = {}

    for result in results:

        classification = result["classification"]

        counts[classification] = (
            counts.get(
                classification,
                0,
            )
            + 1
        )

    table = Table(
        title="Analysis Summary",
        border_style="green",
    )

    table.add_column("Classification")

    table.add_column(
        "Count",
        justify="right",
    )

    for classification in [
        "HIGH_RISK",
        "SUSPICIOUS",
        "NEEDS_REVIEW",
        "BENIGN",
    ]:

        table.add_row(
            classification,
            str(
                counts.get(
                    classification,
                    0,
                )
            ),
        )

    table.add_row(
        "Analyzed",
        str(analyzed),
    )

    table.add_row(
        "Skipped",
        str(skipped),
    )

    table.add_row(
        "Errors",
        str(errors),
    )

    table.add_row(
        "Time",
        f"{elapsed:.2f}s",
    )

    console.print()

    console.print(table)


# ============================================================
# CLI
# ============================================================


def parse_args():

    parser = argparse.ArgumentParser(description=("TDC Pro Analyzer V1.1"))

    source = parser.add_mutually_exclusive_group(required=True)

    source.add_argument(
        "--db",
        type=str,
        help="SQLite database input.",
    )

    source.add_argument(
        "--csv",
        type=str,
        help="CSV input file.",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=DEFAULT_OUTPUT,
        help=("CSV output path. " "Default: " f"{DEFAULT_OUTPUT}"),
    )

    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=("Ollama model name."),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=("Maximum number of input messages."),
    )

    return parser.parse_args()


# ============================================================
# MAIN
# ============================================================


def main():

    args = parse_args()

    console.print(
        Panel.fit(
            "[bold cyan]TDC PRO ANALYZER V1.1[/bold cyan]\n"
            "[dim]Prefilter → Ollama → Structured Analysis[/dim]",
            border_style="cyan",
            padding=(1, 3),
        )
    )

    # --------------------------------------------------------
    # Validate paths
    # --------------------------------------------------------

    if args.db:

        input_path = Path(args.db)

        console.print(f"[green][+] Input:[/green] " f"SQLite → {input_path}")

    else:

        input_path = Path(args.csv)

        console.print(f"[green][+] Input:[/green] " f"CSV → {input_path}")

    console.print(f"[green][+] Model:[/green] " f"{args.model}")

    console.print(f"[green][+] Output:[/green] " f"{args.output}")

    # --------------------------------------------------------
    # Ollama
    # --------------------------------------------------------

    if not check_ollama():

        console.print(
            Panel(
                "[red]"
                "Ollama API is unavailable."
                "[/red]\n\n"
                "Try:\n"
                "[cyan]systemctl status ollama[/cyan]\n"
                "[cyan]ollama serve[/cyan]",
                title="Ollama Error",
                border_style="red",
            )
        )

        sys.exit(1)

    console.print("[green][+] Ollama API: OK[/green]")

    if not check_model(args.model):

        console.print(
            Panel(
                f"[red]"
                f"Model not found: "
                f"{args.model}"
                f"[/red]\n\n"
                "Available models:\n"
                "[cyan]ollama list[/cyan]",
                title="Model Error",
                border_style="red",
            )
        )

        sys.exit(1)

    console.print("[green][+] Model: OK[/green]")

    console.print()

    # --------------------------------------------------------
    # Run
    # --------------------------------------------------------

    if args.db:

        run_database_mode(
            db_path=args.db,
            model=args.model,
            limit=args.limit,
            output=args.output,
        )

    else:

        run_csv_mode(
            csv_path=args.csv,
            model=args.model,
            limit=args.limit,
            output=args.output,
        )

    console.print()

    console.print(
        Panel(
            "[bold green]Analysis complete.[/bold green]",
            border_style="green",
        )
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        console.print("\n[yellow]" "[!] Interrupted by user." "[/yellow]")

        sys.exit(130)

    except Exception as exc:

        console.print(
            Panel(
                f"[red]{exc}[/red]",
                title="Fatal Error",
                border_style="red",
            )
        )

        sys.exit(1)
