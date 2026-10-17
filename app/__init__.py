"""Application factory."""

from flask import Flask

from app.config import DATA_DIR, Config
from app.extensions import db


def create_app(config_object: type = Config) -> Flask:
    """Create and configure the Flask app."""
    app = Flask(__name__)
    app.config.from_object(config_object)

    DATA_DIR.mkdir(parents=True, exist_ok=True)  # SQLite won't create folders
    db.init_app(app)

    # Import inside the function so models register on `db` without
    # creating an import cycle. create_all() only knows imported models.
    from app import models  # noqa: F401

    return app
