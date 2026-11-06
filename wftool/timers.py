"""Time-driven work, run by the scheduler every few minutes.

- Expiry: a link past its validity (default 14 days) runs the step's "On no
  response": wait (tell the starter and the admin) or reject automatically.
- Reminder: once per task, after the reminder delay (default 5 working days),
  with a new link that replaces the old one (decided 6 Nov 2026: one reminder).
  A paper signature waiting for its scan reminds the starter instead.
- Notice to the starter: once per task, after the starter notice delay
  (default 10 working days).
Working days follow the holiday calendar in Settings (Saxony plus extra days).
"""

import logging
from datetime import datetime

from . import links, masterdata, settings
from .engine import Actor

log = logging.getLogger("wftool.timers")
SYSTEM = Actor("system", via="timer")


def _int_setting(db, key, default):
    try:
        return int(masterdata.get_setting(db, key, str(default)))
    except ValueError:
        return default


def _local_date(iso):
    return datetime.fromisoformat(iso).astimezone(settings.LOCAL_TZ).date()


def run(db, engine):
    """One pass. Returns counts, for logging and tests."""
    out = {"expired": 0, "reminded": 0, "noticed": 0}
    days = links.validity_days(db)
    for link in links.expired_open_links(db):
        try:
            with db.tx():
                links.mark_expired(db, link["id"])
                engine.expire_task(link["task_id"], days)
            out["expired"] += 1
        except Exception:  # one broken task must not stop the others
            log.exception("expiry of link %s failed", link["id"])

    today = settings.today()
    remind_after = _int_setting(db, "reminder_working_days", 5)
    notice_after = _int_setting(db, "starter_notice_working_days", 10)
    rows = db.all("SELECT t.* FROM tasks t JOIN requests r ON r.id = t.request_id WHERE r.status = 'open'"
                  " AND t.round = r.round AND t.status IN ('open', 'paper_pending')")
    for row in rows:
        task = dict(row)
        start = _local_date(task["created_at"])
        try:
            if not task["reminded_at"] and settings.add_working_days(db, start, remind_after) <= today:
                engine.resend(task["id"], SYSTEM, reason="reminder")
                out["reminded"] += 1
            if task["status"] == "open" and not task["starter_noticed_at"] \
                    and settings.add_working_days(db, start, notice_after) <= today:
                engine.notify_starter_still_open(task["id"], notice_after)
                out["noticed"] += 1
        except Exception:
            log.exception("timer for task %s failed", task["id"])
    return out
