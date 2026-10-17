"""Shared extension objects (kept separate to avoid circular imports)."""

import sqlite3

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all models."""


db = SQLAlchemy(model_class=Base)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    """Configure every new SQLite connection.

    - foreign_keys=ON : SQLite ignores foreign keys unless asked.
    - journal_mode=WAL: readers (web) don't block the writer (collector).
    - busy_timeout    : wait up to 30s instead of failing on a locked DB.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()
