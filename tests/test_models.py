"""Phase 1 model tests. Run: python -m unittest discover -s tests -v"""

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, StatementError

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import Chat, ChatMember, Message, User, utcnow


class TestConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    TESTING = True


class ModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _chat(self, telegram_id: int = -1001) -> Chat:
        chat = Chat(telegram_id=telegram_id, title=f"Chat {telegram_id}")
        db.session.add(chat)
        db.session.commit()
        return chat

    def _user(self, telegram_id: int = 111) -> User:
        user = User(telegram_id=telegram_id, username="ali")
        db.session.add(user)
        db.session.commit()
        return user

    def _count(self, model) -> int:
        return db.session.scalar(select(func.count()).select_from(model))

    def test_relationships(self) -> None:
        chat, user = self._chat(), self._user()
        now = utcnow()
        db.session.add_all(
            [
                ChatMember(
                    chat_id=chat.id, user_id=user.id, first_seen=now, last_seen=now
                ),
                Message(
                    telegram_id=1,
                    chat_id=chat.id,
                    user_id=user.id,
                    text="salom",
                    date=now,
                ),
            ]
        )
        db.session.commit()
        self.assertEqual(chat.messages[0].user, user)
        self.assertEqual(user.memberships[0].chat, chat)

    def test_same_user_in_two_chats(self) -> None:
        user = self._user()
        now = utcnow()
        for tg_id in (-1001, -1002):
            chat = self._chat(tg_id)
            db.session.add(
                ChatMember(
                    chat_id=chat.id, user_id=user.id, first_seen=now, last_seen=now
                )
            )
        db.session.commit()
        self.assertEqual(self._count(User), 1)
        self.assertEqual(len(user.memberships), 2)

    def test_duplicate_message_rejected(self) -> None:
        chat = self._chat()
        now = utcnow()
        db.session.add(Message(telegram_id=5, chat_id=chat.id, date=now))
        db.session.commit()
        db.session.add(Message(telegram_id=5, chat_id=chat.id, date=now))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_same_message_id_in_different_chats_allowed(self) -> None:
        now = utcnow()
        for tg_id in (-1001, -1002):
            chat = self._chat(tg_id)
            db.session.add(Message(telegram_id=5, chat_id=chat.id, date=now))
        db.session.commit()
        self.assertEqual(self._count(Message), 2)

    def test_foreign_keys_enforced(self) -> None:
        db.session.add(Message(telegram_id=1, chat_id=9999, date=utcnow()))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_naive_datetime_rejected(self) -> None:
        chat = self._chat()
        db.session.add(
            Message(telegram_id=1, chat_id=chat.id, date=datetime(2026, 1, 1))
        )
        with self.assertRaises((StatementError, ValueError)):
            db.session.commit()
        db.session.rollback()

    def test_datetime_roundtrip_is_utc(self) -> None:
        chat = self._chat()
        tashkent = datetime(2026, 10, 17, 12, 0, tzinfo=ZoneInfo("Asia/Tashkent"))
        db.session.add(Message(telegram_id=1, chat_id=chat.id, date=tashkent))
        db.session.commit()
        db.session.expire_all()  # force a fresh read from the database
        msg = db.session.scalar(select(Message))
        self.assertEqual(msg.date, tashkent)  # same instant
        self.assertEqual(msg.date.utcoffset(), timedelta(0))  # returned as UTC
        self.assertEqual(msg.date.hour, 7)  # 12:00 +05:00 = 07:00 UTC

    def test_chat_delete_cascades(self) -> None:
        chat, user = self._chat(), self._user()
        now = utcnow()
        db.session.add_all(
            [
                ChatMember(
                    chat_id=chat.id, user_id=user.id, first_seen=now, last_seen=now
                ),
                Message(telegram_id=1, chat_id=chat.id, user_id=user.id, date=now),
            ]
        )
        db.session.commit()
        db.session.delete(chat)
        db.session.commit()
        self.assertEqual(self._count(Message), 0)
        self.assertEqual(self._count(ChatMember), 0)
        self.assertEqual(self._count(User), 1)  # user stays


if __name__ == "__main__":
    unittest.main()
