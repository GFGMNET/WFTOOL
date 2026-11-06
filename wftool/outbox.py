"""The outbox: emails are queued in the same transaction as the state change
and sent by the background thread (design: "outbox ... retries").

Each email is HTML with a plain-text copy (multipart/alternative), optionally
with a PDF. Attachments are stored encrypted until the email is sent. The event
log records who got which email and when; it never stores the text.
"""

import logging
import smtplib
from datetime import timedelta
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid

from . import audit, clock, settings

log = logging.getLogger("wftool.outbox")

# Minutes to wait before attempt 2, 3, ...; after the last one the email is marked failed.
BACKOFF_MINUTES = [1, 5, 15, 60, 180, 360, 720]
MAX_ATTEMPTS = len(BACKOFF_MINUTES) + 1


def queue(db, *, to, subject, text, html=None, request_id=None, kind=None, attachment=None,
          attachment_name=None, files=None):
    """Queue one email. Runs inside the caller's transaction."""
    path = files.save("mail", attachment, ".pdf") if attachment is not None and files is not None else None
    now = clock.now_iso()
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO outbox (request_id, to_addr, subject, body_text, body_html, attachment_path,"
            " attachment_name, kind, status, attempts, next_attempt_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)",
            (request_id, to.lower(), subject, text, html, path, attachment_name if path else None, kind, now, now))
        audit.record(db, "email.queued", request_id=request_id, actor="system",
                     data={"outbox": cur.lastrowid, "to": to.lower(), "kind": kind, "subject": subject,
                           "attachment": attachment_name if path else None})
    return cur.lastrowid


def build_message(row, sender, attachment=None):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = row["to_addr"]
    msg["Subject"] = row["subject"]
    msg["Date"] = format_datetime(clock.now())
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1] if "@" in sender else None)
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(row["body_text"])
    if row["body_html"]:
        msg.add_alternative(row["body_html"], subtype="html")
    if attachment is not None:
        msg.add_attachment(attachment, maintype="application", subtype="pdf",
                           filename=row["attachment_name"] or "request.pdf")
    return msg


def due(db, limit=50):
    return [dict(r) for r in db.all(
        "SELECT * FROM outbox WHERE status = 'pending' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)"
        " ORDER BY id LIMIT ?", (clock.now_iso(), limit))]


def send_due(db, cipher, files, limit=50):
    """Send what is due. Returns {"sent": n, "retry": n, "failed": n, "skipped": reason or None}."""
    result = {"sent": 0, "retry": 0, "failed": 0, "skipped": None}
    rows = due(db, limit)
    if not rows:
        return result
    cfg = settings.smtp_config(db, cipher)
    if not cfg["host"] or not cfg["sender"]:
        result["skipped"] = "SMTP is not set up"
        return result
    try:
        server = settings.open_smtp(cfg)
    except (OSError, smtplib.SMTPException) as exc:
        for row in rows:
            _failed_attempt(db, row, f"connect: {exc.__class__.__name__}: {exc}", result)
        return result
    try:
        for row in rows:
            try:
                attachment = files.load(row["attachment_path"]) if row["attachment_path"] else None
                server.send_message(build_message(row, cfg["sender"], attachment))
            except (OSError, smtplib.SMTPException, ValueError) as exc:
                _failed_attempt(db, row, f"{exc.__class__.__name__}: {exc}", result)
                if isinstance(exc, (smtplib.SMTPServerDisconnected, OSError)):
                    break
                continue
            with db.tx() as c:
                c.execute("UPDATE outbox SET status = 'sent', sent_at = ?, attempts = attempts + 1, last_error = NULL"
                          " WHERE id = ?", (clock.now_iso(), row["id"]))
                audit.record(db, "email.sent", request_id=row["request_id"], actor="system",
                             data={"outbox": row["id"], "to": row["to_addr"], "kind": row["kind"]})
            if row["attachment_path"]:
                files.delete(row["attachment_path"])
            result["sent"] += 1
    finally:
        try:
            server.quit()
        except (OSError, smtplib.SMTPException):
            pass
    return result


def _failed_attempt(db, row, error, result):
    attempts = row["attempts"] + 1
    with db.tx() as c:
        if attempts >= MAX_ATTEMPTS:
            c.execute("UPDATE outbox SET status = 'failed', attempts = ?, last_error = ? WHERE id = ?",
                      (attempts, error[:500], row["id"]))
            audit.record(db, "email.failed", request_id=row["request_id"], actor="system",
                         data={"outbox": row["id"], "to": row["to_addr"], "error": error[:300]})
            result["failed"] += 1
        else:
            wait = timedelta(minutes=BACKOFF_MINUTES[attempts - 1])
            c.execute("UPDATE outbox SET attempts = ?, last_error = ?, next_attempt_at = ? WHERE id = ?",
                      (attempts, error[:500], (clock.now() + wait).replace(microsecond=0).isoformat(), row["id"]))
            result["retry"] += 1
    log.warning("email %s to %s: %s", row["id"], row["to_addr"], error)


def retry(db, outbox_id, *, by):
    """Send a failed or waiting email again at the next run."""
    with db.tx() as c:
        c.execute("UPDATE outbox SET status = 'pending', attempts = 0, next_attempt_at = ? WHERE id = ?"
                  " AND status != 'sent'", (clock.now_iso(), outbox_id))
        audit.record(db, "email.retry", actor=by, data={"outbox": outbox_id})


def recent(db, limit=200, status=None):
    sql = "SELECT id, request_id, to_addr, subject, kind, status, attempts, last_error, created_at, sent_at," \
          " next_attempt_at FROM outbox"
    params = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    return [dict(r) for r in db.all(sql + " ORDER BY id DESC LIMIT ?", params + [limit])]


def counts(db):
    return {r["status"]: r["n"] for r in db.all("SELECT status, COUNT(*) AS n FROM outbox GROUP BY status")}
