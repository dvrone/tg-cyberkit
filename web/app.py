# app.py

import csv
import io
import os
import zlib
from collections import Counter

from flask import Flask, flash, redirect, render_template, request, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "tg-cyberkit-dev")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB upload limit

ALLOWED_EXTENSIONS = {"csv"}
RISK_LEVELS = {"HIGH", "MEDIUM", "LOW"}
STATUSES = {"UNREVIEWED", "CONFIRMED", "FALSE_POSITIVE", "NEEDS_REVIEW"}

REVIEW_ACTIONS = {
    "confirmed": "CONFIRMED",
    "false-positive": "FALSE_POSITIVE",
    "needs-review": "NEEDS_REVIEW",
}

# In-memory storage for V1 (later: SQLite/SQLAlchemy).
# `messages` keeps order; `messages_by_id` gives O(1) lookup.
# Both reference the same dict objects, so a status change shows up in both.
messages = []
messages_by_id = {}

csv.field_size_limit(10 * 1024 * 1024)  # allow very long messages


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def normalize_status(value):
    value = value.strip().upper().replace("-", "_").replace(" ", "_")
    return value if value in STATUSES else "UNREVIEWED"


def normalize_risk(value):
    value = value.strip().upper()
    return value if value in RISK_LEVELS else "UNKNOWN"


def normalize_message(row, index):
    """Convert different CSV column names into one common structure."""

    def get(*names):
        for name in names:
            value = row.get(name)
            if value not in (None, ""):
                return str(value).strip()
        return ""

    # TDC Pro exports: username, first_name, last_name.
    user = get("user", "username", "sender", "sender_username")
    if not user:
        user = " ".join(part for part in (get("first_name"), get("last_name")) if part)

    chat = get(
        "chat", "group", "group_name", "chat_name", "chat_title", "chat_username"
    )
    text = get("message", "text", "content")

    return {
        "id": index,
        "date": get("date", "datetime", "timestamp", "created_at"),
        "chat": chat,
        "user": user,
        "message": text,
        "risk": normalize_risk(get("risk", "risk_level", "severity")),
        "status": normalize_status(get("status", "review_status")),
        "reason": get("reason", "analysis", "explanation"),
        # Precomputed once, so search doesn't lowercase on every request.
        "_search": f"{text}\n{user}\n{chat}".lower(),
    }


def compute_stats():
    """Single pass over the dataset."""
    risks = Counter()
    unreviewed = 0

    for m in messages:
        risks[m["risk"]] += 1
        if m["status"] == "UNREVIEWED":
            unreviewed += 1

    return {
        "total": len(messages),
        "high": risks["HIGH"],
        "medium": risks["MEDIUM"],
        "low": risks["LOW"],
        "unreviewed": unreviewed,
    }


def compute_groups():
    """[(group_name, message_count), ...] sorted by name, for the filter."""
    counts = Counter(m["chat"] for m in messages)
    return sorted(counts.items(), key=lambda item: item[0].lower())


@app.template_filter("group_color")
def group_color(name):
    """Stable color per group name, so each group is easy to recognize."""
    hue = zlib.crc32((name or "").encode("utf-8")) % 360
    return f"hsl({hue}, 65%, 62%)"


@app.route("/")
def index():
    query = request.args.get("q", "").strip().lower()
    chat = request.args.get("chat", "")
    risk = request.args.get("risk", "").strip().upper()
    status = request.args.get("status", "").strip().upper()

    filtered = [
        m
        for m in messages
        if (not query or query in m["_search"])
        and (not chat or m["chat"] == chat)
        and (not risk or m["risk"] == risk)
        and (not status or m["status"] == status)
    ]

    return render_template(
        "index.html",
        messages=filtered,
        stats=compute_stats(),
        groups=compute_groups(),
        query=request.args.get("q", ""),
        selected_chat=chat,
        selected_risk=risk,
        selected_status=status,
    )


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")

    if not file or not file.filename:
        flash("CSV fayl tanlanmagan.", "danger")
        return redirect(url_for("index"))

    if not allowed_file(file.filename):
        flash("Faqat CSV fayllar qabul qilinadi.", "danger")
        return redirect(url_for("index"))

    try:
        content = file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))

        # Normalize header names: " Date " -> "date"
        if reader.fieldnames:
            reader.fieldnames = [(h or "").strip().lower() for h in reader.fieldnames]

        imported = [
            normalize_message(row, index) for index, row in enumerate(reader, start=1)
        ]

    except UnicodeDecodeError:
        flash("CSV UTF-8 formatida bo‘lishi kerak.", "danger")
        return redirect(url_for("index"))

    except Exception as exc:
        flash(f"CSV o‘qishda xatolik: {exc}", "danger")
        return redirect(url_for("index"))

    # Replace dataset only after the whole file parsed successfully.
    messages.clear()
    messages.extend(imported)
    messages_by_id.clear()
    messages_by_id.update({m["id"]: m for m in imported})

    flash(f"{len(messages)} ta message yuklandi.", "success")
    return redirect(url_for("index"))


@app.route("/message/<int:message_id>")
def message_detail(message_id):
    message = messages_by_id.get(message_id)

    if message is None:
        flash("Message topilmadi.", "danger")
        return redirect(url_for("index"))

    return render_template("message.html", message=message)


@app.route("/review/<int:message_id>/<action>", methods=["POST"])
def review(message_id, action):
    new_status = REVIEW_ACTIONS.get(action)

    if new_status is None:
        flash("Noto‘g‘ri review action.", "danger")
        return redirect(url_for("index"))

    message = messages_by_id.get(message_id)

    if message is None:
        flash("Message topilmadi.", "danger")
        return redirect(url_for("index"))

    message["status"] = new_status
    flash(f"Review status: {new_status}", "success")

    return redirect(url_for("message_detail", message_id=message_id))


@app.route("/clear", methods=["POST"])
def clear():
    messages.clear()
    messages_by_id.clear()

    flash("Dataset tozalandi.", "success")
    return redirect(url_for("index"))


@app.errorhandler(413)
def file_too_large(_):
    flash("Fayl juda katta (maksimum 10 MB).", "danger")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
