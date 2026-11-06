"""Search, CSV export, the daily anchor email and retention (phase 5).

Design: "show me yesterday's request and who approved it" is one search and one click.
"""

import csv
import io
import json
import logging
from datetime import date, datetime, time, timedelta

from . import audit, clock, emails, masterdata, outbox, present, settings
from .definitions import label_text

log = logging.getLogger("wftool.reports")

PAGE_SIZE = 50
STATUSES = ("open", "returned", "completed", "cancelled")


def _day_start_utc(day):
    """Local midnight of `day` as UTC ISO text (dates in the search are local dates)."""
    return datetime.combine(day, time.min, tzinfo=settings.LOCAL_TZ).astimezone(
        clock.now().tzinfo).replace(microsecond=0).isoformat()


def search(db, filters, *, starter_only=None, page=1, page_size=PAGE_SIZE, lang="de"):
    """Requests matching `filters`: date_from, date_to (local dates, by start date), workflow (name),
    person (starter or anyone who acted, part of email or name), status, number.
    `starter_only` limits to one starter's requests. Returns {"rows", "total", "page", "pages"}."""
    where, params = ["1 = 1"], []
    if starter_only is not None:
        where.append("r.starter_email = ?")
        params.append(starter_only)
    if filters.get("date_from"):
        where.append("r.created_at >= ?")
        params.append(_day_start_utc(filters["date_from"]))
    if filters.get("date_to"):
        where.append("r.created_at < ?")
        params.append(_day_start_utc(filters["date_to"] + timedelta(days=1)))
    if filters.get("workflow"):
        where.append("w.name = ?")
        params.append(filters["workflow"])
    if filters.get("status") in STATUSES:
        where.append("r.status = ?")
        params.append(filters["status"])
    if filters.get("number"):
        where.append("r.number LIKE ?")
        params.append(f"%{filters['number'].strip()}%")
    if filters.get("person"):
        p = f"%{filters['person'].strip().lower()}%"
        where.append("(LOWER(r.starter_email) LIKE ? OR r.id IN (SELECT t.request_id FROM tasks t"
                     " LEFT JOIN ad_users u ON u.email = t.decided_by OR u.email = t.assignee_email"
                     " WHERE LOWER(t.decided_by) LIKE ? OR LOWER(t.assignee_email) LIKE ?"
                     " OR LOWER(u.display_name) LIKE ?) OR r.starter_email IN"
                     " (SELECT email FROM ad_users WHERE LOWER(display_name) LIKE ?))")
        params += [p, p, p, p, p]
    base = (" FROM requests r JOIN workflow_versions v ON v.id = r.workflow_version_id"
            " JOIN workflows w ON w.id = v.workflow_id WHERE " + " AND ".join(where))
    total = db.one("SELECT COUNT(*) AS n" + base, params)["n"]
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, page), pages)
    rows = []
    for r in db.all("SELECT r.*, v.definition_json, v.version AS wf_version" + base +
                    " ORDER BY r.id DESC LIMIT ? OFFSET ?", params + [page_size, (page - 1) * page_size]):
        rows.append(_summary(db, dict(r), lang))
    return {"rows": rows, "total": total, "page": page, "pages": pages}


def _summary(db, r, lang):
    definition = json.loads(r.pop("definition_json"))
    r["workflow_name"] = definition["name"]
    r["workflow"] = label_text(definition.get("label"), lang) or definition["name"]
    r["step"] = None
    r["waiting_for"] = []
    if r["status"] == "open" and r["current_step"] is not None:
        step = definition["steps"][r["current_step"]]
        r["step"] = label_text(step.get("label"), lang) or step["id"]
        r["waiting_for"] = [x["assignee_email"] for x in db.all(
            "SELECT assignee_email FROM tasks WHERE request_id = ? AND round = ? AND step_index = ?"
            " AND status IN ('open', 'paper_pending')", (r["id"], r["round"], r["current_step"]))]
    decisions = []
    for t in db.all("SELECT * FROM tasks WHERE request_id = ? AND round = ? AND status = 'done' ORDER BY id",
                    (r["id"], r["round"])):
        step = definition["steps"][t["step_index"]]
        name = label_text(step.get("label"), lang) or step["id"]
        if t["item_value"]:
            name += f" ({t['item_value']})"
        decisions.append({"step": name, "decision": present.name_of(present.DECISION_NAMES, t["decision"], lang),
                          "by": t["decided_by"], "at": t["decided_at"], "reason": t["reason"]})
    r["decisions"] = decisions
    return r


CSV_COLUMNS = {
    "de": ["Antragsnummer", "Workflow", "Version", "Status", "Gestartet von", "Gestartet am", "Letzte Änderung",
           "Abgeschlossen am", "Aktueller Schritt", "Wartet auf", "Entscheidungen"],
    "en": ["Request number", "Workflow", "Version", "Status", "Started by", "Started", "Last change",
           "Closed", "Current step", "Waiting for", "Decisions"],
}


def to_csv(rows, lang="de"):
    """Semicolon-separated, UTF-8 with BOM so Excel opens it correctly. Never contains form values,
    so secret values cannot end up in it."""
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(CSV_COLUMNS[lang])
    for r in rows:
        decisions = " | ".join(
            f"{d['step']}: {d['decision']} – {d['by']} – {present.local_time(d['at'], lang)}"
            + (f" – {d['reason']}" if d["reason"] else "") for d in r["decisions"])
        w.writerow([r["number"], r["workflow"], r["wf_version"],
                    present.name_of(present.STATUS_NAMES, r["status"], lang), r["starter_email"],
                    present.local_time(r["created_at"], lang), present.local_time(r["updated_at"], lang),
                    present.local_time(r["closed_at"], lang), r["step"] or "", ", ".join(r["waiting_for"]),
                    decisions])
    return buf.getvalue()


def all_rows(db, filters, *, starter_only=None, lang="de", limit=100000):
    return search(db, filters, starter_only=starter_only, page=1, page_size=limit, lang=lang)["rows"]


# --- daily anchor -----------------------------------------------------------

ANCHOR_TEXT = {
    "subject": "WFTOOL – Prüfwert des Ereignisprotokolls {day}",
    "body": ("Guten Tag,\n\nder aktuelle Prüfwert (Fingerabdruck) des WFTOOL-Ereignisprotokolls vom {day}:\n\n"
             "Eintrag Nr. {last_id}\n{hash}\n\nPrüfung der Kette: {result}\n\n"
             "Bitte bewahren Sie diese E-Mail auf. Stimmt der Wert später nicht mehr mit dem Protokoll überein, "
             "wurden Einträge geändert oder gelöscht.\n\n"
             "This is the daily fingerprint of the WFTOOL event log; keep this email."),
}


def anchor_due(db, now=None):
    """True once a day from the anchor time (local, default 06:00) until the anchor was sent."""
    now = (now or clock.now()).astimezone(settings.LOCAL_TZ)
    at = (masterdata.get_setting(db, "anchor_time", "06:00") or "06:00").strip()
    try:
        hh, mm = (int(x) for x in at.split(":"))
    except ValueError:
        hh, mm = 6, 0
    if (now.hour, now.minute) < (hh, mm):
        return False
    return masterdata.get_setting(db, "last_anchor_day", "") != now.date().isoformat()


def send_anchor(db, *, by="system"):
    """Queue the anchor email to the admin and record it. Returns the event id, or None without admin email."""
    to = (masterdata.get_setting(db, "admin_email", "") or "").strip()
    day = clock.now().astimezone(settings.LOCAL_TZ).date()
    check = audit.verify(db)
    last_id, last_hash = audit.latest_hash(db)
    masterdata.set_setting(db, "last_anchor_day", day.isoformat())
    if not to:
        log.warning("daily anchor not sent: no admin email in Settings")
        return None
    result = "in Ordnung / ok" if check["ok"] else \
        f"FEHLER bei Eintrag {check['first_bad_id']}: {check['problem']}"
    ctx = {"day": day.strftime("%d.%m.%Y"), "last_id": last_id, "hash": last_hash, "result": result}
    subject, body = emails.render(ANCHOR_TEXT["subject"], ctx), emails.render(ANCHOR_TEXT["body"], ctx)
    with db.tx():
        outbox.queue(db, to=to, subject=subject, text=body, html=emails.html_email("de", body, title=subject),
                     kind="anchor")
        return audit.record(db, "audit.anchor_sent", actor=by,
                            data={"to": to, "last_id": last_id, "hash": last_hash, "chain_ok": check["ok"],
                                  "day": day.isoformat()})


def anchors(db, limit=60):
    rows = db.all("SELECT id, ts, data_json FROM events WHERE type = 'audit.anchor_sent' ORDER BY id DESC LIMIT ?",
                  (limit,))
    return [dict(r, data=json.loads(r["data_json"])) for r in rows]


def anchor_matches(db, anchor):
    """Does the anchored entry still carry the anchored hash?"""
    row = db.one("SELECT hash FROM events WHERE id = ?", (anchor["data"]["last_id"],))
    return bool(row) and row["hash"] == anchor["data"]["hash"]


# --- retention ----------------------------------------------------------------

def retention_months(db):
    raw = (masterdata.get_setting(db, "retention_months", "") or "").strip()
    try:
        months = int(raw)
    except ValueError:
        return None
    return months if months > 0 else None


def retention_cutoff(months, today=None):
    today = today or settings.today()
    year, month = today.year, today.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(today.day, 28)
    return _day_start_utc(date(year, month, day))


def apply_retention(db, *, dry_run=False, by="system"):
    """Owner decision 6 Nov 2026: remove IP address and browser from requests closed more than N
    months ago; decisions and PDFs stay. Off while `retention_months` is empty."""
    months = retention_months(db)
    if months is None:
        return {"on": False}
    cutoff = retention_cutoff(months)
    ids = [r["id"] for r in db.all("SELECT id FROM requests WHERE status IN ('completed', 'cancelled')"
                                   " AND closed_at IS NOT NULL AND closed_at < ?", (cutoff,))]
    result = {"on": True, "months": months, "cutoff": cutoff, "requests": len(ids), "tasks": 0, "events": 0,
              "dry_run": dry_run}
    if not ids:
        return result
    marks = ",".join("?" * len(ids))
    task_ids = [r["id"] for r in db.all(f"SELECT id FROM tasks WHERE request_id IN ({marks}) AND redacted_at IS NULL"
                                        " AND (ip IS NOT NULL OR user_agent IS NOT NULL)", ids)]
    event_ids = [r["id"] for r in db.all(f"SELECT id FROM events WHERE request_id IN ({marks})"
                                         " AND redacted_at IS NULL AND (ip IS NOT NULL OR user_agent IS NOT NULL)",
                                         ids)]
    result["tasks"], result["events"] = len(task_ids), len(event_ids)
    if dry_run or (not task_ids and not event_ids):
        return result
    with db.tx() as c:
        for chunk in range(0, len(task_ids), 500):
            part = task_ids[chunk:chunk + 500]
            c.execute(f"UPDATE tasks SET ip = NULL, user_agent = NULL, redacted_at = ? WHERE id IN"
                      f" ({','.join('?' * len(part))})", [clock.now_iso()] + part)
        result["events"] = audit.redact(db, event_ids)
        audit.record(db, "retention.applied", actor=by,
                     data={"months": months, "cutoff": cutoff, "requests": len(ids), "tasks": len(task_ids),
                           "events": result["events"]})
    return result
