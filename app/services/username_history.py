"""Username history logic (used by the collector)."""

from datetime import datetime

from sqlalchemy import select

from app.extensions import db
from app.models import User, UsernameHistory, utcnow


def normalize_username(username: str | None) -> str | None:
    """Return lowercase username without '@', or None if empty."""
    if not username:
        return None
    cleaned = username.strip().lstrip("@").strip().lower()
    return cleaned or None


def record_username(
    user: User,
    username: str | None,
    observed_at: datetime | None = None,
) -> UsernameHistory | None:
    """Remember that `user` was seen with `username` at `observed_at`.

    Idempotent: calling it again for the same (user, username) only widens
    the first_observed / last_observed range. It does NOT commit; the caller
    commits so many records can be saved in one transaction.

    `user` must already have an `id` (add it to the session and flush first).
    """
    normalized = normalize_username(username)
    if normalized is None:
        return None
    if user.id is None:
        raise ValueError("User must be flushed (have an id) before recording username.")

    observed_at = observed_at or utcnow()

    row = db.session.scalar(
        select(UsernameHistory).where(
            UsernameHistory.user_id == user.id,
            UsernameHistory.username == normalized,
        )
    )
    if row is None:
        row = UsernameHistory(
            user_id=user.id,
            username=normalized,
            first_observed=observed_at,
            last_observed=observed_at,
        )
        db.session.add(row)
    else:
        # iter_messages goes newest -> oldest, so never just overwrite.
        row.first_observed = min(row.first_observed, observed_at)
        row.last_observed = max(row.last_observed, observed_at)
    return row
