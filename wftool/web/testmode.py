"""Helpers for the automated test suite. Registered only with WFTOOL_TEST_MODE=1.

Every page then shows a red TEST MODE bar. The helpers need an admin sign-in:
  POST /test/tick    run the background work now (import, timers, daily email, outbox)
  POST /test/clock   shift the app's clock by `minutes` (reminders, expiry)
  POST /test/backup  write a backup copy of the database to the data folder
  GET  /test/info    process user, folders and settings the image checks look at
"""

import os
from datetime import timedelta

from flask import current_app, jsonify, request

from .. import clock
from ..scheduler import Scheduler
from .views import admin_required


def _scheduler():
    ext = current_app.extensions["wftool"]
    return Scheduler(ext["db"], current_app.config["IMPORT_DIR"], engine=ext["engine"], cipher=ext["cipher"],
                     files=ext["files"])


@admin_required
def tick():
    _scheduler().run_once()
    return jsonify({"ok": True, "now": clock.now_iso()})


@admin_required
def shift_clock():
    try:
        minutes = int(request.form.get("minutes", "0"))
    except ValueError:
        return jsonify({"ok": False}), 400
    clock.set_offset(timedelta(minutes=minutes) if minutes else None)
    return jsonify({"ok": True, "now": clock.now_iso()})


@admin_required
def backup():
    target = current_app.config["DATA_DIR"] / "backup-test.db"
    current_app.extensions["wftool"]["db"].backup(target)
    return jsonify({"ok": True, "file": target.name})


@admin_required
def info():
    data_dir = current_app.config["DATA_DIR"]
    key = current_app.config["KEY_FILE_PATH"]
    return jsonify({
        "uid": os.getuid() if hasattr(os, "getuid") else None,
        "data_dir": str(data_dir),
        "key_file": str(key),
        "key_inside_data": data_dir in key.parents,
        "behind_proxy": os.environ.get("WFTOOL_BEHIND_PROXY", "0") == "1",
        "secure_cookie": current_app.config["SESSION_COOKIE_SECURE"],
    })


def register(app):
    app.add_url_rule("/test/tick", "test_tick", tick, methods=["POST"])
    app.add_url_rule("/test/clock", "test_clock", shift_clock, methods=["POST"])
    app.add_url_rule("/test/backup", "test_backup", backup, methods=["POST"])
    app.add_url_rule("/test/info", "test_info", info, methods=["GET"])
