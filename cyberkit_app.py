#!/usr/bin/env python3
"""
Cyberkit App — Telegram collector + keyword matcher in ONE file

Tab 1 (Collect):  log in to Telegram, pick a group you are a member of,
                  pick a date range (Tashkent days), collect messages,
                  optionally save them as JSON.
Tab 2 (Analyze):  run the multilingual keyword matcher on the collected
                  messages (or on any JSON file) and save a Markdown report.

Config (.env next to this file; only needed for the collector tab):
    TG_API_ID=123456
    TG_API_HASH=abcdef...
    TG_SESSION=data/tg_cyberkit          # optional, this is the default

Install:
    pip install flashtext rapidfuzz telethon python-dotenv
    (Fedora: sudo dnf install python3-tkinter)

Run:
    python cyberkit_app.py
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import queue
import re
import sys
import threading
import tkinter as tk
import unicodedata
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

try:
    from flashtext import KeywordProcessor
except ImportError:
    sys.exit(
        "flashtext is not installed. Run: pip install flashtext rapidfuzz telethon python-dotenv"
    )

try:
    from rapidfuzz import fuzz, process
except ImportError:
    fuzz = None
    process = None

try:
    from telethon import TelegramClient
except ImportError:
    TelegramClient = None

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    from zoneinfo import ZoneInfo

    TASHKENT = ZoneInfo("Asia/Tashkent")
except Exception:  # tzdata missing
    TASHKENT = timezone(timedelta(hours=5), "Asia/Tashkent")

DEFAULT_SESSION = "data/tg_cyberkit"
EXPORT_DIR = Path("exports")
PROGRESS_EVERY = 200


# ======================================================================
# 1. KEYWORDS  (edit here as you review false positives)
# ======================================================================

FINAL_MULTILINGUAL_KEYWORDS = {
    "crypto": [
        "usdt",
        "usdc",
        "btc",
        "eth",
        "ton",
        "bnb",
        "trx",
        "sol",
        "dex",
        "cex",
        "nft",
        "defi",
        "ico",
        "ieo",
        "web3",
        "txid",
        "gas",
        "crypto",
        "kripto",
        "kriptovalyuta",
        "kripta",
        "крипта",
        "криптовалюта",
        "cryptocurrency",
        "token",
        "coin",
        "altcoin",
        "stablecoin",
        "memecoin",
        "p2p",
        "blockchain",
        "blokcheyn",
        "блокчейн",
        "wallet",
        "hamyon",
        "koshelek",
        "address",
    ],
    "banking_and_cards": [
        "bank",
        "karta",
        "card",
        "cc",
        "kartochka",
        "plastik",
        "humo",
        "uzcard",
        "visa",
        "mastercard",
        "swift",
        "iban",
        "pos",
        "atm",
        "pin",
        "cvv",
        "cvc",
        "transfer",
        "o'tkazma",
        "perevod",
        "перевод",
        "hisob",
        "schet",
        "rekvizit",
        "terminal",
        "bankomat",
    ],
    "account_and_auth": [
        "sim",
        "simkarta",
        "esim",
        "akkaunt",
        "account",
        "login",
        "parol",
        "pwd",
        "password",
        "profil",
        "profile",
        "number",
        "nomer",
        "2fa",
        "mfa",
        "otp",
        "pin",
        "code",
        "kod",
        "access",
        "dostup",
        "kirish",
    ],
    "identity_and_kyc": [
        "kyc",
        "aml",
        "pep",
        "id",
        "dl",
        "verifikatsiya",
        "верификация",
        "verification",
        "verify",
        "proverka",
        "tasdiqlash",
        "passport",
        "pasport",
        "паспорт",
        "id_card",
        "prava",
        "selfie",
        "limit",
        "tier",
        "uroven",
    ],
    "transactions_and_cashout": [
        "withdraw",
        "wdr",
        "cashout",
        "yechish",
        "vivod",
        "вывод",
        "dep",
        "depozit",
        "deposit",
        "topup",
        "popolneniye",
        "naqd",
        "cash",
        "kesh",
        "obnal",
        "обнал",
        "chek",
        "check",
        "kvitansiya",
        "cashback",
        "tx",
    ],
    "finance_and_tax": [
        "soliq",
        "tax",
        "nalog",
        "inn",
        "tin",
        "qqs",
        "vat",
        "nds",
        "faktura",
        "invoice",
        "deklaratsiya",
        "fiskal",
        "komissiya",
        "fee",
        "poshlina",
    ],
    "gambling_and_betting": [
        "casino",
        "kazino",
        "bet",
        "betting",
        "stavka",
        "gambling",
        "qimor",
        "bk",
        "bukmeker",
        "rtp",
        "rng",
        "1xbet",
        "mostbet",
        "slot",
        "slots",
        "poker",
        "roulette",
        "ruletka",
    ],
    "otc_and_offers": [
        "куплю",
        "продам",
        "покупаю",
        "продаю",
        "обмен",
        "обменник",
        "обменка",
        "buy",
        "sell",
        "exchange",
        "swap",
        "sotaman",
        "sotib olaman",
        "ayirboshlash",
        "куплю usdt",
        "продам usdt",
        "нал",
        "наличка",
        "наличные",
        "cash",
        "нал на карту",
        "карта на нал",
    ],
    "account_market": [
        "аккаунт на продажу",
        "продам аккаунт",
        "куплю аккаунт",
        "аренда аккаунта",
        "account for sale",
        "account rental",
        "арендую аккаунт",
        "сдам аккаунт",
        "аккаунт с верификацией",
        "verified account",
        "готовый аккаунт",
        "чистый аккаунт",
        "акк",
        "акки",
        "аккаунты",
        "дроп",
        "дропы",
        "дроповод",
    ],
    "payment_intermediaries": [
        "прием платежей",
        "принимаю платежи",
        "принимаю переводы",
        "прием на карту",
        "payment processing",
        "payment agent",
        "принимаю usdt",
        "вывод на карту",
        "обнал",
        "обналичка",
        "обналичить",
        "процент за перевод",
        "процент за оборот",
        "посредник",
        "гарант",
        "без гаранта",
        "без kyc",
        "без верификации",
        "без паспорта",
        "без документов",
    ],
}


# ======================================================================
# 2. MATCHER ENGINE
# ======================================================================


def normalize_text(value: Any) -> str:
    """Normalize case, Unicode variants, apostrophes and punctuation."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("ё", "е")
    text = re.sub(r"[’‘`´']", "", text)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _extend_word_boundaries(processor: KeywordProcessor) -> None:
    """FlashText treats only ASCII letters as word characters by default, so
    'нал' would match inside 'канал'. Register Latin-extended and Cyrillic
    letters so that matches stay whole-word."""
    for low, high in ((0x00C0, 0x024F), (0x0400, 0x04FF)):
        for code_point in range(low, high + 1):
            char = chr(code_point)
            if char.isalpha():
                processor.add_non_word_boundary(char)


def build_processors() -> dict[str, KeywordProcessor]:
    processors = {}
    for category, keywords in FINAL_MULTILINGUAL_KEYWORDS.items():
        processor = KeywordProcessor(case_sensitive=True)
        _extend_word_boundaries(processor)
        for keyword in keywords:
            normalized = normalize_text(keyword)
            if normalized:
                # search the normalized form, report the original spelling
                processor.add_keyword(normalized, keyword)
        processors[category] = processor
    return processors


CATEGORY_PROCESSORS = build_processors()

# Fuzzy matching: single words of 5+ characters only. Short terms such as
# "id", "tx", "cc", "pin", "bk" are exact-match only.
FUZZY_KEYWORDS: dict[str, str] = {}
FUZZY_KEYWORD_TO_CATEGORIES: dict[str, set[str]] = defaultdict(set)

for _category, _keywords in FINAL_MULTILINGUAL_KEYWORDS.items():
    for _keyword in _keywords:
        _normalized = normalize_text(_keyword)
        if " " not in _normalized and len(_normalized) >= 5:
            FUZZY_KEYWORDS[_normalized] = _keyword
            FUZZY_KEYWORD_TO_CATEGORIES[_normalized].add(_category)

FUZZY_CANDIDATES = sorted(FUZZY_KEYWORDS)


def detect_keywords(
    message: str,
    fuzzy: bool = True,
    fuzzy_threshold: int = 90,
) -> dict[str, Any]:
    """
    Return matched categories, terms and a lightweight priority score.

    Score guidance (not a verdict):
      - exact phrase (2+ words): 3 points
      - exact single keyword: 1 point
      - fuzzy single-word match: 0.5 point
    """
    normalized_message = normalize_text(message)
    matches_by_category: dict[str, dict] = defaultdict(dict)

    for category, processor in CATEGORY_PROCESSORS.items():
        for original_keyword in processor.extract_keywords(normalized_message):
            normalized_keyword = normalize_text(original_keyword)
            matches_by_category[category][normalized_keyword] = {
                "keyword": original_keyword,
                "match_type": "exact",
                "score": 3 if " " in normalized_keyword else 1,
            }

    if fuzzy and process is not None and FUZZY_CANDIDATES:
        already_exact = {
            keyword
            for category_matches in matches_by_category.values()
            for keyword in category_matches
        }
        for word in normalized_message.split():
            if len(word) < 5:
                continue
            result = process.extractOne(
                word,
                FUZZY_CANDIDATES,
                scorer=fuzz.ratio,
                score_cutoff=fuzzy_threshold,
            )
            if not result:
                continue

            matched_normalized, similarity, _ = result
            if matched_normalized == word or matched_normalized in already_exact:
                continue

            for category in FUZZY_KEYWORD_TO_CATEGORIES[matched_normalized]:
                matches_by_category[category][matched_normalized] = {
                    "keyword": FUZZY_KEYWORDS[matched_normalized],
                    "matched_text": word,
                    "match_type": "fuzzy",
                    "similarity": round(float(similarity), 1),
                    "score": 0.5,
                }

    categories = {}
    total_score = 0.0
    for category, keyword_matches in matches_by_category.items():
        if not keyword_matches:
            continue
        category_score = sum(item["score"] for item in keyword_matches.values())
        total_score += category_score
        categories[category] = {
            "score": round(category_score, 2),
            "matches": list(keyword_matches.values()),
        }

    return {
        "matched": bool(categories),
        "total_score": round(total_score, 2),
        "categories": categories,
    }


# ======================================================================
# 3. MESSAGE LOADING  (JSON file -> internal message dicts)
# ======================================================================


def flatten_text(value: Any) -> str:
    """Telegram 'text' is either a string or a list of strings/entity dicts."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(flatten_text(part) for part in value)
    if isinstance(value, dict):
        return flatten_text(value.get("text", ""))
    return ""


def messages_from_list(items: list, chat_name: str = "") -> list[dict]:
    """Normalize raw message dicts into the internal format."""
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue

        text = ""
        for key in ("text", "message"):
            candidate = flatten_text(item.get(key))
            if candidate.strip():
                text = candidate
                break
        if not text:
            continue  # service messages, media without caption, etc.

        sender_id = item.get("from_id")
        sender = (
            item.get("from")
            or item.get("sender")
            or item.get("username")
            or sender_id
            or ""
        )
        result.append(
            {
                "id": item.get("id", ""),
                "sender": str(sender),
                "sender_id": "" if sender_id is None else str(sender_id),
                "username": str(item.get("username") or ""),
                "display_name": str(item.get("from") or ""),
                "date": str(item.get("date", "")),
                "chat": chat_name,
                "text": text,
            }
        )
    return result


def load_messages(path: Path) -> list[dict]:
    """Read a JSON file (Telegram export or a plain list) into internal messages."""
    with path.open("r", encoding="utf-8-sig") as file:
        data = json.load(file)

    if isinstance(data, list):
        return messages_from_list(data)

    if isinstance(data, dict):
        messages: list[dict] = []
        found_structure = False

        if isinstance(data.get("messages"), list):
            found_structure = True
            messages += messages_from_list(data["messages"], str(data.get("name", "")))

        chats = data.get("chats")
        if isinstance(chats, dict) and isinstance(chats.get("list"), list):
            found_structure = True
            for chat in chats["list"]:
                if isinstance(chat, dict) and isinstance(chat.get("messages"), list):
                    messages += messages_from_list(
                        chat["messages"], str(chat.get("name", ""))
                    )

        if found_structure:
            return messages

    raise ValueError(
        "Unrecognized JSON structure. Expected: a list of messages or "
        "a Telegram export ('messages' / 'chats.list')."
    )


# ======================================================================
# 4. TELEGRAM COLLECTOR  (runs in its own thread with its own event loop)
# ======================================================================


def load_config() -> tuple[int, str, str]:
    if load_dotenv:
        load_dotenv()
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    if not api_id or not api_hash:
        raise ValueError(".env is missing TG_API_ID and/or TG_API_HASH.")
    try:
        api_id_int = int(api_id)
    except ValueError as error:
        raise ValueError("TG_API_ID must be an integer.") from error
    session = os.getenv("TG_SESSION", DEFAULT_SESSION)
    Path(session).parent.mkdir(parents=True, exist_ok=True)
    return api_id_int, api_hash, session


def tashkent_range(first_day: date, last_day: date) -> tuple[datetime, datetime]:
    """Return [start, end) in UTC covering first_day..last_day (Tashkent, inclusive)."""
    start = datetime.combine(first_day, time.min, tzinfo=TASHKENT)
    end = datetime.combine(last_day + timedelta(days=1), time.min, tzinfo=TASHKENT)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def describe_sender(sender: Any) -> tuple[str, str]:
    """Return (display name, username) for a user/channel sender."""
    if sender is None:
        return "", ""
    username = getattr(sender, "username", None) or ""
    title = getattr(sender, "title", None)
    if title:
        name = title
    else:
        parts = (
            getattr(sender, "first_name", None),
            getattr(sender, "last_name", None),
        )
        name = " ".join(part for part in parts if part)
    return name or username, username


def message_to_dict(message: Any) -> dict:
    name, username = describe_sender(message.sender)
    reply_to = message.reply_to.reply_to_msg_id if message.reply_to else None
    return {
        "id": message.id,
        "date": message.date.astimezone(timezone.utc).isoformat(),
        "from": name,
        "from_id": message.sender_id,
        "username": username,
        "text": message.message or "",
        "reply_to_message_id": reply_to,
        "forwarded": bool(message.fwd_from),
    }


class TelegramService:
    """Owns the Telethon client and an asyncio loop running in a background thread.

    Everything Telegram-related happens inside coroutines on that loop; the GUI
    only calls run() and receives results through the shared events queue.
    """

    def __init__(self, events: queue.Queue) -> None:
        self.events = events
        self.client: Any = None
        self.entities: dict[int, Any] = {}
        self.stop_event = threading.Event()

        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro: Any, ok_kind: str) -> None:
        """Schedule a coroutine; post (ok_kind, result) or ("error", (ok_kind, text))."""
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)

        def done(finished: concurrent.futures.Future) -> None:
            try:
                self.events.put((ok_kind, finished.result()))
            except Exception as error:  # noqa: BLE001 - surface any failure in the UI
                self.events.put(
                    ("error", (ok_kind, f"{type(error).__name__}: {error}"))
                )

        future.add_done_callback(done)

    def ask(self, prompt: str, secret: bool = False) -> str:
        """Called from the loop thread: ask the user through a GUI dialog and wait."""
        reply: queue.Queue = queue.Queue()
        self.events.put(("ask", (prompt, secret, reply)))
        answer = reply.get()
        if answer is None:
            raise RuntimeError("Login cancelled.")
        return answer

    async def connect_and_list(
        self, api_id: int, api_hash: str, session: str
    ) -> list[dict]:
        if self.client is None:
            self.client = TelegramClient(session, api_id, api_hash)
        await self.client.start(
            phone=lambda: self.ask("Phone number (+998...):"),
            code_callback=lambda: self.ask("Code sent by Telegram:"),
            password=lambda: self.ask("2FA password:", secret=True),
        )

        groups: list[dict] = []
        self.entities.clear()
        async for dialog in self.client.iter_dialogs():
            if not (dialog.is_group or dialog.is_channel):
                continue
            self.entities[dialog.id] = dialog.entity
            username = getattr(dialog.entity, "username", None) or ""
            groups.append(
                {
                    "id": dialog.id,
                    "title": dialog.name,
                    "username": username,
                }
            )
        return groups

    async def collect(
        self,
        entity_id: int,
        start_utc: datetime,
        end_utc: datetime,
        limit: int | None,
    ) -> tuple[list[dict], dict, bool]:
        """Return (messages, stats, complete). Stops early if stop_event is set."""
        entity = self.entities[entity_id]
        messages: list[dict] = []
        stats = {"seen": 0, "skipped_no_text": 0}

        async for message in self.client.iter_messages(
            entity, offset_date=start_utc, reverse=True, limit=limit
        ):
            if self.stop_event.is_set():
                return messages, stats, False
            if message.date < start_utc:
                continue
            if message.date >= end_utc:
                break

            stats["seen"] += 1
            if not (message.message or "").strip():
                stats["skipped_no_text"] += 1  # service messages, media without caption
                continue

            messages.append(message_to_dict(message))
            if len(messages) % PROGRESS_EVERY == 0:
                self.events.put(("collect_progress", len(messages)))

        return messages, stats, True

    async def _disconnect(self) -> None:
        if self.client is not None:
            await self.client.disconnect()

    def shutdown(self) -> None:
        try:
            asyncio.run_coroutine_threadsafe(self._disconnect(), self.loop).result(
                timeout=5
            )
        except Exception:  # noqa: BLE001 - closing anyway
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)


# ======================================================================
# 5. EXPORTS  (JSON for collected data, Markdown for matcher results)
# ======================================================================


def slugify(value: str) -> str:
    return re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("_") or "export"


def write_json_export(path: Path, payload: dict) -> None:
    """Write atomically so an interrupted run never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp_path.replace(path)


CATEGORY_LABELS = {
    "crypto": "Crypto",
    "banking_and_cards": "Banking / cards",
    "account_and_auth": "Accounts / authentication",
    "identity_and_kyc": "Identity / KYC",
    "transactions_and_cashout": "Cash-out / transactions",
    "finance_and_tax": "Finance / tax",
    "gambling_and_betting": "Gambling / betting",
    "otc_and_offers": "OTC offers",
    "account_market": "Account market",
    "payment_intermediaries": "Payment intermediaries",
}

NONE_LABEL = "(none)"
MAX_IDS_SHOWN = 30
MAX_TEXT_CHARS = 3000


def category_label(key: str) -> str:
    return CATEGORY_LABELS.get(key, key)


def format_match(match: dict) -> str:
    if match.get("match_type") == "fuzzy":
        return f"`{match['keyword']}` ← «{match.get('matched_text', '')}»"
    return f"`{match['keyword']}`"


def format_time(raw: str) -> str:
    """Aware ISO timestamp -> '17.09.2026, 07:05:45 (Tashkent)'.
    Timestamps without a timezone are shown as written, with a note."""
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return raw or "—"
    if moment.tzinfo is None:
        return f"{moment:%d.%m.%Y, %H:%M:%S} (timezone unknown)"
    return f"{moment.astimezone(TASHKENT):%d.%m.%Y, %H:%M:%S} (Tashkent)"


def code_block(text: str) -> list[str]:
    """Fenced block that cannot be broken by backticks inside the message."""
    text = text.replace("\r", "").strip()
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS].rstrip() + "…"
    fence = "```"
    while fence in text:
        fence += "`"
    return [f"{fence}text", text, fence]


def group_hits(hits: list[tuple[dict, dict]]) -> list[dict]:
    """Merge identical messages from the same sender (case/punctuation-insensitive).

    Different sender IDs are never merged: identical text from several accounts
    is a separate signal and should stay visible as separate entries.
    """
    groups: dict[tuple[str, str], dict] = {}
    for message, result in hits:
        key = (
            message["sender_id"] or message["sender"],
            normalize_text(message["text"]),
        )
        group = groups.get(key)
        if group is None:
            groups[key] = {"messages": [message], "result": result}
        else:
            group["messages"].append(message)

    for group in groups.values():
        group["messages"].sort(
            key=lambda m: m["date"]
        )  # ISO strings sort chronologically

    return sorted(
        groups.values(),
        key=lambda g: (-g["result"]["total_score"], -len(g["messages"])),
    )


def build_markdown(
    source: str,
    total: int,
    hits: list[tuple[dict, dict]],
    params: dict,
) -> str:
    groups = group_hits(hits)
    fuzzy_info = f"yes, threshold {params['threshold']}" if params["fuzzy"] else "no"

    lines = [
        "# Keyword report",
        "",
        f"- Source: `{source}`",
        f"- Report date: {datetime.now(TASHKENT):%d.%m.%Y, %H:%M} (Tashkent)",
        f"- Messages checked: {total}",
        f"- Matched messages: {len(hits)} (unique: {len(groups)})",
        f"- Fuzzy search: {fuzzy_info}",
        f"- Minimum score: {params['min_score']}",
        "",
        "## By category",
        "",
        "| Category | Unique messages | Total messages |",
        "|---|---:|---:|",
    ]

    stats: dict[str, list[int]] = {}
    for group in groups:
        for category in group["result"]["categories"]:
            entry = stats.setdefault(category, [0, 0])
            entry[0] += 1
            entry[1] += len(group["messages"])

    for category, (unique, messages) in sorted(
        stats.items(), key=lambda item: (-item[1][1], item[0])
    ):
        lines.append(f"| {category_label(category)} | {unique} | {messages} |")
    lines.append("")

    for number, group in enumerate(groups, 1):
        items = group["messages"]
        first, last = items[0], items[-1]
        result = group["result"]
        count = len(items)

        lines.append(
            f"## {number}. Message" + (f" — {count} times" if count > 1 else "")
        )
        lines += [
            f"- **Sender ID:** `{first['sender_id'] or NONE_LABEL}`",
            f"- **Username:** `{('@' + first['username']) if first['username'] else NONE_LABEL}`",
            f"- **Display name:** `{first['display_name'] or NONE_LABEL}`",
            "- **Indicator categories:** "
            + ", ".join(category_label(c) for c in result["categories"]),
            f"- **Score:** {result['total_score']}",
            "- **Matched keywords:** "
            + ", ".join(
                format_match(m)
                for info in result["categories"].values()
                for m in info["matches"]
            ),
        ]

        if count > 1:
            lines += [
                f"- **First sent:** `{format_time(first['date'])}`",
                f"- **Last sent:** `{format_time(last['date'])}`",
            ]
        else:
            lines.append(f"- **Sent:** `{format_time(first['date'])}`")

        ids = [f"`{m['id']}`" for m in items if m["id"] != ""]
        if ids:
            shown = ", ".join(ids[:MAX_IDS_SHOWN])
            if len(ids) > MAX_IDS_SHOWN:
                shown += f", … (+{len(ids) - MAX_IDS_SHOWN})"
            lines.append(f"- **Message IDs ({len(ids)}):** {shown}")

        lines.append("")
        lines += code_block(first["text"])
        lines.append("")

    return "\n".join(lines)


# ======================================================================
# 6. MATCH WORKER
# ======================================================================


def match_worker(
    messages: list[dict],
    params: dict,
    events: queue.Queue,
    stop_event: threading.Event,
) -> None:
    try:
        total = len(messages)
        hits = []
        for index, message in enumerate(messages, 1):
            if stop_event.is_set():
                events.put(("match_cancelled", None))
                return

            result = detect_keywords(
                message["text"],
                fuzzy=params["fuzzy"],
                fuzzy_threshold=params["threshold"],
            )
            if result["matched"] and result["total_score"] >= params["min_score"]:
                hits.append((message, result))

            if index % 50 == 0 or index == total:
                events.put(("match_progress", (index, total)))

        hits.sort(key=lambda item: item[1]["total_score"], reverse=True)
        events.put(("match_done", hits))
    except Exception as error:  # noqa: BLE001 - report any failure to the UI
        events.put(("error", ("match", f"{type(error).__name__}: {error}")))


# ======================================================================
# 7. GUI
# ======================================================================


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.service = TelegramService(self.events) if TelegramClient else None

        # shared data
        self.messages: list[dict] = []
        self.source_name = ""

        # collector state
        self.groups: dict[str, dict] = {}  # combobox label -> group info
        self.collect_ctx: dict = {}

        # matcher state
        self.hits: list[tuple[dict, dict]] = []
        self.last_params: dict = {}
        self.match_stop = threading.Event()

        self.status_var = tk.StringVar(value="Ready.")
        self._build_ui()

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._poll)

    # ---------- UI construction ----------

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)

        self.collect_tab = ttk.Frame(self.notebook, padding=10)
        self.match_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.collect_tab, text="1. Collect")
        self.notebook.add(self.match_tab, text="2. Analyze")

        self._build_collect_tab()
        self._build_match_tab()

        ttk.Label(
            outer,
            textvariable=self.status_var,
            relief="sunken",
            anchor="w",
            padding=(6, 2),
        ).pack(fill="x", pady=(8, 0))

    def _build_collect_tab(self) -> None:
        tab = self.collect_tab

        if self.service is None:
            ttk.Label(
                tab,
                text="Telethon is not installed, collecting is disabled.\n"
                "Run: pip install telethon python-dotenv\n"
                "You can still analyze a ready JSON file on the Analyze tab.",
            ).pack(anchor="w")
            return

        self.connect_btn = ttk.Button(
            tab, text="Connect to Telegram and load groups", command=self.connect
        )
        self.connect_btn.pack(anchor="w")

        group_row = ttk.Frame(tab)
        group_row.pack(fill="x", pady=8)
        ttk.Label(group_row, text="Group:").pack(side="left")
        self.group_var = tk.StringVar()
        self.group_box = ttk.Combobox(
            group_row, textvariable=self.group_var, state="readonly"
        )
        self.group_box.pack(side="left", fill="x", expand=True, padx=6)

        today = datetime.now(TASHKENT).date()
        self.first_var = tk.StringVar(value=str(today - timedelta(days=7)))
        self.last_var = tk.StringVar(value=str(today))
        self.limit_var = tk.StringVar()
        self.save_json_var = tk.BooleanVar(value=True)

        options = ttk.LabelFrame(
            tab, text="Date range (Tashkent days, both inclusive)", padding=8
        )
        options.pack(fill="x")
        ttk.Label(options, text="From (YYYY-MM-DD):").pack(side="left")
        ttk.Entry(options, textvariable=self.first_var, width=12).pack(
            side="left", padx=(4, 12)
        )
        ttk.Label(options, text="To:").pack(side="left")
        ttk.Entry(options, textvariable=self.last_var, width=12).pack(
            side="left", padx=(4, 12)
        )
        ttk.Label(options, text="Limit (optional):").pack(side="left")
        ttk.Entry(options, textvariable=self.limit_var, width=8).pack(
            side="left", padx=4
        )

        ttk.Checkbutton(
            tab,
            text=f"Also save collected messages as a JSON file (in {EXPORT_DIR}/)",
            variable=self.save_json_var,
        ).pack(anchor="w", pady=8)

        actions = ttk.Frame(tab)
        actions.pack(fill="x")
        self.collect_btn = ttk.Button(
            actions,
            text="Start collecting",
            command=self.start_collect,
            state="disabled",
        )
        self.collect_btn.pack(side="left")
        self.stop_collect_btn = ttk.Button(
            actions, text="Stop", command=self.stop_collect, state="disabled"
        )
        self.stop_collect_btn.pack(side="left", padx=6)

        self.collect_progress = ttk.Progressbar(tab, mode="indeterminate")
        self.collect_progress.pack(fill="x", pady=(10, 2))
        self.collect_info_var = tk.StringVar()
        ttk.Label(tab, textvariable=self.collect_info_var).pack(anchor="w")

    def _build_match_tab(self) -> None:
        tab = self.match_tab

        source_row = ttk.Frame(tab)
        source_row.pack(fill="x")
        ttk.Label(source_row, text="Source:").pack(side="left")
        self.source_var = tk.StringVar(
            value="Not selected yet (collect messages or choose a JSON file)"
        )
        ttk.Label(source_row, textvariable=self.source_var).pack(side="left", padx=6)
        ttk.Button(source_row, text="Choose JSON file…", command=self.open_json).pack(
            side="right"
        )

        self.fuzzy_var = tk.BooleanVar(value=process is not None)
        self.threshold_var = tk.IntVar(value=90)
        self.min_score_var = tk.DoubleVar(value=1)

        options = ttk.LabelFrame(tab, text="Settings", padding=8)
        options.pack(fill="x", pady=8)
        fuzzy_check = ttk.Checkbutton(
            options, text="Fuzzy search", variable=self.fuzzy_var
        )
        fuzzy_check.pack(side="left")
        if process is None:
            fuzzy_check.state(["disabled"])
            self.fuzzy_var.set(False)
        ttk.Label(options, text="Fuzzy threshold:").pack(side="left", padx=(16, 4))
        ttk.Spinbox(
            options, from_=0, to=100, width=5, textvariable=self.threshold_var
        ).pack(side="left")
        ttk.Label(options, text="Minimum score:").pack(side="left", padx=(16, 4))
        ttk.Spinbox(
            options,
            from_=0,
            to=100,
            increment=0.5,
            width=5,
            textvariable=self.min_score_var,
        ).pack(side="left")

        actions = ttk.Frame(tab)
        actions.pack(fill="x")
        self.run_btn = ttk.Button(actions, text="Analyze", command=self.run_match)
        self.run_btn.pack(side="left")
        self.cancel_btn = ttk.Button(
            actions, text="Cancel", command=self.cancel_match, state="disabled"
        )
        self.cancel_btn.pack(side="left", padx=6)
        self.save_btn = ttk.Button(
            actions, text="Save Markdown…", command=self.save_markdown, state="disabled"
        )
        self.save_btn.pack(side="right")

        self.match_progress = ttk.Progressbar(tab, mode="determinate")
        self.match_progress.pack(fill="x", pady=(8, 8))

        panes = ttk.PanedWindow(tab, orient="vertical")
        panes.pack(fill="both", expand=True)

        table_frame = ttk.Frame(panes)
        columns = ("score", "sender", "categories", "message")
        self.tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "score": ("Score", 60),
            "sender": ("Sender", 140),
            "categories": ("Categories", 220),
            "message": ("Message", 480),
        }
        for column, (title, width) in headings.items():
            self.tree.heading(column, text=title)
            self.tree.column(
                column, width=width, anchor="w", stretch=(column == "message")
            )
        self.tree.column("score", anchor="center")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        panes.add(table_frame, weight=3)

        detail_frame = ttk.Frame(panes)
        self.detail = tk.Text(detail_frame, height=8, wrap="word", state="disabled")
        detail_scroll = ttk.Scrollbar(detail_frame, command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        self.detail.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        panes.add(detail_frame, weight=1)

    # ---------- Event loop: background threads -> GUI ----------

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                handler = getattr(self, f"_on_{kind}", None)
                if handler:
                    handler(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _on_ask(self, payload: tuple) -> None:
        prompt, secret, reply = payload
        answer = simpledialog.askstring(
            "Telegram login", prompt, parent=self.root, show="*" if secret else None
        )
        reply.put(answer)

    def _on_error(self, payload: tuple) -> None:
        origin, text = payload
        messagebox.showerror("Error", text)
        self.status_var.set("An error occurred.")
        if origin == "connected":
            self.connect_btn.configure(state="normal")
        elif origin == "collect_done":
            self._set_collect_busy(False)
        elif origin == "match":
            self._set_match_busy(False)

    # ---------- Collector tab actions ----------

    def connect(self) -> None:
        try:
            api_id, api_hash, session = load_config()
        except ValueError as error:
            messagebox.showerror("Configuration error", str(error))
            return
        self.connect_btn.configure(state="disabled")
        self.status_var.set("Connecting to Telegram… (you may be asked to log in)")
        self.service.run(
            self.service.connect_and_list(api_id, api_hash, session), "connected"
        )

    def _on_connected(self, groups: list[dict]) -> None:
        self.groups = {}
        for group in groups:
            handle = f" (@{group['username']})" if group["username"] else ""
            label = f"{group['title']}{handle}  [{group['id']}]"
            self.groups[label] = group
        self.group_box.configure(values=list(self.groups))
        if self.groups:
            self.group_box.current(0)
            self.collect_btn.configure(state="normal")
        self.connect_btn.configure(state="normal", text="Reload groups")
        self.status_var.set(f"Connected. {len(groups)} groups/channels found.")

    def start_collect(self) -> None:
        group = self.groups.get(self.group_var.get())
        if not group:
            messagebox.showwarning("No group selected", "Choose a group first.")
            return

        try:
            first_day = date.fromisoformat(self.first_var.get().strip())
            last_day = date.fromisoformat(self.last_var.get().strip())
        except ValueError:
            messagebox.showwarning(
                "Invalid date", "Date format: YYYY-MM-DD (for example 2026-10-01)."
            )
            return
        if first_day > last_day:
            messagebox.showwarning(
                "Invalid range", "The start date must not be after the end date."
            )
            return

        limit_text = self.limit_var.get().strip()
        limit: int | None = None
        if limit_text:
            if not limit_text.isdigit() or int(limit_text) <= 0:
                messagebox.showwarning(
                    "Invalid limit", "Limit must be a positive integer."
                )
                return
            limit = int(limit_text)

        start_utc, end_utc = tashkent_range(first_day, last_day)
        self.collect_ctx = {
            "group": group,
            "first": first_day,
            "last": last_day,
            "limit": limit,
        }
        self._set_collect_busy(True)
        self.collect_info_var.set("Collecting…")
        self.status_var.set(f"Collecting: {group['title']}")
        self.service.stop_event.clear()
        self.service.run(
            self.service.collect(group["id"], start_utc, end_utc, limit), "collect_done"
        )

    def stop_collect(self) -> None:
        self.service.stop_event.set()
        self.status_var.set("Stopping…")

    def _on_collect_progress(self, count: int) -> None:
        self.collect_info_var.set(f"{count} messages collected…")

    def _on_collect_done(self, payload: tuple) -> None:
        raw_messages, stats, complete = payload
        ctx = self.collect_ctx
        title = ctx["group"]["title"]
        self._set_collect_busy(False)

        saved_note = ""
        if self.save_json_var.get() and raw_messages:
            path = EXPORT_DIR / f"{slugify(title)}_{ctx['first']}_{ctx['last']}.json"
            export = {
                "name": title,
                "type": "group",
                "id": ctx["group"]["id"],
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "range_tashkent": {"from": str(ctx["first"]), "to": str(ctx["last"])},
                "limit": ctx["limit"],
                "complete": complete,
                "messages": raw_messages,
            }
            try:
                write_json_export(path, export)
                saved_note = f" JSON: {path}"
            except OSError as error:
                messagebox.showerror("JSON not saved", str(error))

        info = (
            f"{len(raw_messages)} text messages, "
            f"{stats['skipped_no_text']} without text skipped, "
            f"complete: {'yes' if complete else 'NO (stopped)'}.{saved_note}"
        )
        self.collect_info_var.set(info)
        self.status_var.set("Collecting finished.")

        if not raw_messages:
            messagebox.showinfo("Empty result", "No text messages found in this range.")
            return

        self._set_messages(
            messages_from_list(raw_messages, title),
            f"{title} ({ctx['first']} .. {ctx['last']})",
        )
        self.notebook.select(self.match_tab)

    def _set_collect_busy(self, busy: bool) -> None:
        self.connect_btn.configure(state="disabled" if busy else "normal")
        self.collect_btn.configure(
            state="disabled" if busy or not self.groups else "normal"
        )
        self.stop_collect_btn.configure(state="normal" if busy else "disabled")
        if busy:
            self.collect_progress.start(12)
        else:
            self.collect_progress.stop()

    # ---------- Matcher tab actions ----------

    def _set_messages(self, messages: list[dict], source_name: str) -> None:
        self.messages = messages
        self.source_name = source_name
        self._clear_results()
        self.source_var.set(f"{source_name} — {len(messages)} messages")
        self.status_var.set("Source ready. Click Analyze.")

    def open_json(self) -> None:
        path_text = filedialog.askopenfilename(
            title="Choose a JSON file",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path_text:
            return
        path = Path(path_text)

        self.root.configure(cursor="watch")
        self.status_var.set("Reading JSON…")
        self.root.update_idletasks()
        try:
            messages = load_messages(path)
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not read JSON", str(error))
            self.status_var.set("An error occurred.")
            return
        finally:
            self.root.configure(cursor="")

        if not messages:
            messagebox.showinfo("Empty file", "No text messages found in the file.")
        self._set_messages(messages, path.name)

    def run_match(self) -> None:
        if not self.messages:
            messagebox.showwarning(
                "No source", "Collect messages or choose a JSON file first."
            )
            return

        try:
            threshold = int(self.threshold_var.get())
            min_score = float(self.min_score_var.get())
        except tk.TclError:
            messagebox.showwarning(
                "Invalid value", "Threshold and score must be numbers."
            )
            return
        if not 0 <= threshold <= 100:
            messagebox.showwarning(
                "Invalid value", "Fuzzy threshold must be between 0 and 100."
            )
            return

        params = {
            "fuzzy": bool(self.fuzzy_var.get()),
            "threshold": threshold,
            "min_score": min_score,
        }
        self.last_params = params
        self._clear_results()
        self._set_match_busy(True)
        self.match_progress.configure(value=0, maximum=max(len(self.messages), 1))
        self.status_var.set("Analysis started…")

        self.match_stop.clear()
        threading.Thread(
            target=match_worker,
            args=(self.messages, params, self.events, self.match_stop),
            daemon=True,
        ).start()

    def cancel_match(self) -> None:
        self.match_stop.set()
        self.status_var.set("Cancelling…")

    def _on_match_progress(self, payload: tuple) -> None:
        done, total = payload
        self.match_progress.configure(value=done)
        self.status_var.set(f"Analyzing: {done}/{total}")

    def _on_match_done(self, hits: list) -> None:
        self.hits = hits
        self._populate()
        self._set_match_busy(False)
        self.status_var.set(
            f"Done: {len(hits)} of {len(self.messages)} messages matched."
        )

    def _on_match_cancelled(self, _payload: Any) -> None:
        self._set_match_busy(False)
        self.status_var.set("Cancelled.")

    def save_markdown(self) -> None:
        if not self.hits:
            messagebox.showinfo("Empty result", "There are no results to save.")
            return

        path = filedialog.asksaveasfilename(
            title="Save Markdown report",
            defaultextension=".md",
            initialfile=f"{slugify(self.source_name)}_matches.md",
            filetypes=[("Markdown", "*.md"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            content = build_markdown(
                self.source_name, len(self.messages), self.hits, self.last_params
            )
            Path(path).write_text(content, encoding="utf-8")
        except OSError as error:
            messagebox.showerror("Could not save", str(error))
            return

        self.status_var.set(f"Saved: {path}")
        messagebox.showinfo("Done", f"Report saved:\n{path}")

    def _set_match_busy(self, busy: bool) -> None:
        self.run_btn.configure(state="disabled" if busy else "normal")
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        self.save_btn.configure(state="disabled" if busy or not self.hits else "normal")

    def _clear_results(self) -> None:
        self.hits = []
        self.tree.delete(*self.tree.get_children())
        self._set_detail("")
        self.save_btn.configure(state="disabled")

    def _populate(self) -> None:
        for index, (message, result) in enumerate(self.hits):
            preview = " ".join(message["text"].split())
            if len(preview) > 140:
                preview = preview[:140] + "…"
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    result["total_score"],
                    message["sender"],
                    ", ".join(result["categories"]),
                    preview,
                ),
            )

    def _on_select(self, _event: tk.Event) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        message, result = self.hits[int(selection[0])]

        parts = [message["text"].strip(), "", "—" * 20]
        if message["sender_id"]:
            parts.append(f"Sender ID: {message['sender_id']}")
        if message["date"]:
            parts.append(f"Date (UTC): {message['date']}")
        for category, info in result["categories"].items():
            found = ", ".join(
                (
                    f"{m['keyword']} (fuzzy: {m.get('matched_text', '')})"
                    if m.get("match_type") == "fuzzy"
                    else m["keyword"]
                )
                for m in info["matches"]
            )
            parts.append(f"{category} [{info['score']}]: {found}")
        self._set_detail("\n".join(parts))

    def _set_detail(self, text: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    # ---------- Shutdown ----------

    def _on_close(self) -> None:
        self.match_stop.set()
        if self.service is not None:
            self.service.stop_event.set()
            self.service.shutdown()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    root.title("Cyberkit — Collect and Analyze")
    root.geometry("1000x700")
    root.minsize(760, 520)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
