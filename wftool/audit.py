"""Append-only event log with a fingerprint chain (design: "Audit and reports").

Each entry's hash covers its own content plus the hash of the entry before it,
so changing or removing an entry breaks every hash after it. Entries are only
ever inserted; nothing in the app updates or deletes them.
"""

import json

from . import clock
from .crypto import canonical_json, fingerprint

GENESIS = "0" * 64


def _entry_payload(request_id, ts, type_, actor, ip, user_agent, data, prev_hash):
    return {
        "request_id": request_id,
        "ts": ts,
        "type": type_,
        "actor": actor,
        "ip": ip,
        "user_agent": user_agent,
        "data": data,
        "prev": prev_hash,
    }


def record(db, type_, *, request_id=None, actor=None, ip=None, user_agent=None, data=None):
    """Append one event. Must run inside `db.tx()` so the chain stays ordered."""
    data = data or {}
    ts = clock.now_iso()
    with db.tx() as c:
        row = c.execute("SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        prev_hash = row["hash"] if row else GENESIS
        payload = _entry_payload(request_id, ts, type_, actor, ip, user_agent, data, prev_hash)
        h = fingerprint(payload)
        cur = c.execute(
            "INSERT INTO events (request_id, ts, type, actor, ip, user_agent, data_json, prev_hash, hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, ts, type_, actor, ip, user_agent, canonical_json(data), prev_hash, h),
        )
        return cur.lastrowid


def latest_hash(db):
    row = db.one("SELECT id, hash FROM events ORDER BY id DESC LIMIT 1")
    return (row["id"], row["hash"]) if row else (0, GENESIS)


def verify(db):
    """Walk the whole chain. Returns {"ok", "checked", "first_bad_id", "problem"}."""
    prev = GENESIS
    checked = 0
    last_id = 0
    for row in db.all("SELECT * FROM events ORDER BY id"):
        if row["id"] != last_id + 1:
            return _bad(checked, row["id"], f"entries missing before id {row['id']}")
        if row["prev_hash"] != prev:
            return _bad(checked, row["id"], "link to previous entry broken")
        payload = _entry_payload(
            row["request_id"], row["ts"], row["type"], row["actor"], row["ip"],
            row["user_agent"], json.loads(row["data_json"]), row["prev_hash"],
        )
        if fingerprint(payload) != row["hash"]:
            return _bad(checked, row["id"], "entry content changed")
        prev = row["hash"]
        last_id = row["id"]
        checked += 1
    return {"ok": True, "checked": checked, "first_bad_id": None, "problem": None}


def _bad(checked, event_id, problem):
    return {"ok": False, "checked": checked, "first_bad_id": event_id, "problem": problem}


def events_for(db, request_id):
    rows = db.all("SELECT * FROM events WHERE request_id = ? ORDER BY id", (request_id,))
    return [dict(r, data=json.loads(r["data_json"])) for r in rows]
