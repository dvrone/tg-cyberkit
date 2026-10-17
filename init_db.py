"""Create all database tables: python init_db.py"""

from sqlalchemy import inspect

from app import create_app
from app.extensions import db


def main() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()
        tables = inspect(db.engine).get_table_names()
        print("Tables:", ", ".join(sorted(tables)))


if __name__ == "__main__":
    main()
