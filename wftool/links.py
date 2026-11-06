"""Single-use email links (design: "Approval and signature by email").

A link carries a random 256-bit code. Only its SHA-256 hash is stored, so a
copy of the database cannot be used to decide. A link belongs to one task and
one email address and expires after the link validity (default 14 days).
Opening a link only shows the page; the decision is made with the button, and
then the link is used up. Reminders and resends replace the old link.
"""

import hashlib
import secrets
from datetime import timedelta

from . import audit, clock, masterdata

TOKEN_BYTES = 32  # 256 bits

# Why a link no longer works; the page explains it to the person.
USED, EXPIRED, REPLACED, CLOSED = "used", "expired", "replaced", "closed"


def _hash(token):
    return hashlib.sha256(token.encode("ascii", "replace")).hexdigest()


def validity_days(db):
    try:
        return int(masterdata.get_setting(db, "link_validity_days", "14"))
    except ValueError:
        return 14


def create(db, task, *, revoke_reason=REPLACED):
    """A new link for an open task; earlier links of the task stop working. Returns the code."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    now = clock.now()
    expires = now + timedelta(days=validity_days(db))
    with db.tx() as c:
        c.execute("UPDATE links SET revoked_at = ?, revoke_reason = ? WHERE task_id = ? AND used_at IS NULL"
                  " AND revoked_at IS NULL", (clock.now_iso(), revoke_reason, task["id"]))
        c.execute("INSERT INTO links (task_id, email, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                  (task["id"], task["assignee_email"].lower(), _hash(token),
                   expires.replace(microsecond=0).isoformat(), clock.now_iso()))
    return token


def revoke_for_task(db, task_id, reason):
    with db.tx() as c:
        c.execute("UPDATE links SET revoked_at = ?, revoke_reason = ? WHERE task_id = ? AND used_at IS NULL"
                  " AND revoked_at IS NULL", (clock.now_iso(), reason, task_id))


def find(db, token):
    """The link row for a code, with `state`: 'valid', 'used', 'expired', 'replaced', 'closed' or None."""
    if not token or len(token) > 100:
        return None
    row = db.one("SELECT * FROM links WHERE token_hash = ?", (_hash(token),))
    if not row:
        return None
    link = dict(row)
    if link["used_at"]:
        link["state"] = USED
    elif link["revoked_at"]:
        link["state"] = link["revoke_reason"] if link["revoke_reason"] in (EXPIRED, REPLACED) else CLOSED
    elif link["expires_at"] <= clock.now_iso():
        link["state"] = EXPIRED
    else:
        link["state"] = "valid"
    return link


def mark_used(db, link_id):
    with db.tx() as c:
        c.execute("UPDATE links SET used_at = ? WHERE id = ? AND used_at IS NULL", (clock.now_iso(), link_id))


def record_fetch(db, link, request_id, ip, user_agent):
    """The page was loaded: kept apart from decisions, because mail scanners load links too."""
    audit.record(db, "link.fetched", request_id=request_id, actor=link["email"], ip=ip, user_agent=user_agent,
                 data={"link": link["id"], "task": link["task_id"], "state": link["state"]})


def expired_open_links(db):
    """Links past their expiry that were neither used nor revoked, for open tasks."""
    return [dict(r) for r in db.all(
        "SELECT l.* FROM links l JOIN tasks t ON t.id = l.task_id WHERE l.used_at IS NULL AND l.revoked_at IS NULL"
        " AND l.expires_at <= ? AND t.status = 'open'", (clock.now_iso(),))]


def mark_expired(db, link_id):
    with db.tx() as c:
        c.execute("UPDATE links SET revoked_at = ?, revoke_reason = ? WHERE id = ? AND revoked_at IS NULL",
                  (clock.now_iso(), EXPIRED, link_id))
