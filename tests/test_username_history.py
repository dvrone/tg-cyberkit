"""Username history tests. Run: python -m unittest discover -s tests -v"""

import unittest
from datetime import timedelta

from sqlalchemy import func, select

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import User, UsernameHistory, utcnow
from app.services.username_history import normalize_username, record_username


class TestConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    TESTING = True


class UsernameHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.user = User(telegram_id=111)
        db.session.add(self.user)
        db.session.commit()

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _count(self) -> int:
        return db.session.scalar(select(func.count()).select_from(UsernameHistory))

    def test_normalize(self) -> None:
        self.assertEqual(normalize_username("@Ali_01 "), "ali_01")
        self.assertIsNone(normalize_username(""))
        self.assertIsNone(normalize_username(None))
        self.assertIsNone(normalize_username("@"))

    def test_first_record_creates_row(self) -> None:
        record_username(self.user, "Ali")
        db.session.commit()
        self.assertEqual(self._count(), 1)

    def test_same_username_is_not_duplicated(self) -> None:
        record_username(self.user, "ali")
        record_username(self.user, "@ALI")  # same after normalising
        db.session.commit()
        self.assertEqual(self._count(), 1)

    def test_changed_username_adds_second_row(self) -> None:
        record_username(self.user, "ali")
        record_username(self.user, "ali_new")
        db.session.commit()
        names = db.session.scalars(
            select(UsernameHistory.username).order_by(UsernameHistory.username)
        ).all()
        self.assertEqual(names, ["ali", "ali_new"])

    def test_none_username_is_ignored(self) -> None:
        self.assertIsNone(record_username(self.user, None))
        db.session.commit()
        self.assertEqual(self._count(), 0)

    def test_range_uses_min_max(self) -> None:
        now = utcnow()
        older = now - timedelta(days=5)
        newer = now + timedelta(days=5)
        record_username(self.user, "ali", now)
        record_username(self.user, "ali", newer)
        record_username(self.user, "ali", older)  # newest-to-oldest order
        db.session.commit()
        db.session.expire_all()
        row = db.session.scalar(select(UsernameHistory))
        self.assertEqual(row.first_observed, older)
        self.assertEqual(row.last_observed, newer)

    def test_unflushed_user_rejected(self) -> None:
        with self.assertRaises(ValueError):
            record_username(User(telegram_id=222), "bob")

    def test_user_delete_cascades(self) -> None:
        record_username(self.user, "ali")
        db.session.commit()
        db.session.delete(self.user)
        db.session.commit()
        self.assertEqual(self._count(), 0)


if __name__ == "__main__":
    unittest.main()
