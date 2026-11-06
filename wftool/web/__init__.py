"""Flask web app. Phase 2: login, password rules and the admin pages.

Configuration (environment variables):
  WFTOOL_DATA         data folder (database, files, import folder)   default ./data
  WFTOOL_KEY_FILE     encryption key, keep OUTSIDE the data folder    default ./keys/wftool.key
  WFTOOL_SECURE_COOKIE  "1" when the app is reached over HTTPS       default 0
"""

import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .. import accounts
from ..crypto import Cipher
from ..db import Database


def create_app(config=None):
    config = dict(config or {})
    data_dir = Path(config.get("DATA_DIR") or os.environ.get("WFTOOL_DATA", "data")).resolve()
    key_file = Path(config.get("KEY_FILE") or os.environ.get("WFTOOL_KEY_FILE", "keys/wftool.key")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "import").mkdir(exist_ok=True)

    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.update(
        SECRET_KEY=_session_key(data_dir),
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=accounts.SESSION_MINUTES),
        SESSION_REFRESH_EACH_REQUEST=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("WFTOOL_SECURE_COOKIE", "0") == "1",
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        DATA_DIR=data_dir,
        IMPORT_DIR=data_dir / "import",
    )
    app.config.update(config)

    db = Database(data_dir / "wftool.db")
    cipher = Cipher.from_key_file(key_file)
    accounts.ensure_default_admin(db)
    app.extensions["wftool"] = {"db": db, "cipher": cipher}

    from . import views
    views.register(app)
    return app


def _session_key(data_dir):
    path = data_dir / "session.key"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    return path.read_text().strip()
