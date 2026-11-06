"""Append-only event log with a fingerprint chain (design: "Audit and reports").

Each entry's hash covers its own content plus the hash of the entry before it,
so changing or removing an entry breaks every hash after it. Entries are only
ever inserted; the one exception is retention, which may empty the IP address
and browser of old entries (see `redact`).

Version 2 entries (all entries written since phase 5) put the IP address and
browser into the chain as salted fingerprints, sha256(salt + value), with a
random salt per entry. The check then also proves the stored values match.
Retention deletes the values and the salt; the fingerprints stay, so the chain
still verifies, and without the salt the deleted values cannot be guessed back.
Deleting the newest entries is not visible in the chain itself: that is what
the daily anchor email is for (it holds the newest hash of each day).
"""

import hashlib
import json
import secrets

from . import clock
from .crypto import canonical_json, fingerprint

GENESIS = "0" * 64
VERSION = 2


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


def _field_hash(salt, value):
    if value is None:
        return None
    return hashlib.sha256((salt + "\x00" + value).encode("utf-8")).hexdigest()


def record(db, type_, *, request_id=None, actor=None, ip=None, user_agent=None, data=None):
    """Append one event. Must run inside `db.tx()` so the chain stays ordered."""
    data = data or {}
    ts = clock.now_iso()
    salt = secrets.token_hex(16)
    ip_hash, ua_hash = _field_hash(salt, ip), _field_hash(salt, user_agent)
    with db.tx() as c:
        row = c.execute("SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        prev_hash = row["hash"] if row else GENESIS
        payload = dict(_entry_payload(request_id, ts, type_, actor, ip_hash, ua_hash, data, prev_hash), v=VERSION)
        h = fingerprint(payload)
        cur = c.execute(
            "INSERT INTO events (request_id, ts, type, actor, ip, user_agent, data_json, prev_hash, hash,"
            " hash_version, field_salt, ip_hash, ua_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, ts, type_, actor, ip, user_agent, canonical_json(data), prev_hash, h, VERSION, salt,
             ip_hash, ua_hash),
        )
        return cur.lastrowid


def latest_hash(db):
    row = db.one("SELECT id, hash FROM events ORDER BY id DESC LIMIT 1")
    return (row["id"], row["hash"]) if row else (0, GENESIS)


def _row_ok(row):
    """None if the entry is intact, else what is wrong."""
    data = json.loads(row["data_json"])
    if row["hash_version"] == 1:
        payload = _entry_payload(row["request_id"], row["ts"], row["type"], row["actor"], row["ip"],
                                 row["user_agent"], data, row["prev_hash"])
    else:
        if row["redacted_at"] is None:
            if (_field_hash(row["field_salt"] or "", row["ip"]) != row["ip_hash"]
                    or _field_hash(row["field_salt"] or "", row["user_agent"]) != row["ua_hash"]):
                return "IP address or browser changed"
        elif row["ip"] is not None or row["user_agent"] is not None:
            return "IP address or browser changed"
        payload = dict(_entry_payload(row["request_id"], row["ts"], row["type"], row["actor"], row["ip_hash"],
                                      row["ua_hash"], data, row["prev_hash"]), v=row["hash_version"])
    if fingerprint(payload) != row["hash"]:
        return "entry content changed"
    return None


def verify(db):
    """Walk the whole chain. Returns {"ok", "checked", "first_bad_id", "problem", "last_id", "last_hash"}."""
    prev = GENESIS
    checked = 0
    last_id = 0
    for row in db.all("SELECT * FROM events ORDER BY id"):
        if row["id"] != last_id + 1:
            return _bad(checked, row["id"], f"entries missing before id {row['id']}")
        if row["prev_hash"] != prev:
            return _bad(checked, row["id"], "link to previous entry broken")
        problem = _row_ok(row)
        if problem:
            return _bad(checked, row["id"], problem)
        prev = row["hash"]
        last_id = row["id"]
        checked += 1
    return {"ok": True, "checked": checked, "first_bad_id": None, "problem": None, "last_id": last_id,
            "last_hash": prev}


def _bad(checked, event_id, problem):
    return {"ok": False, "checked": checked, "first_bad_id": event_id, "problem": problem, "last_id": None,
            "last_hash": None}


def events_for(db, request_id):
    rows = db.all("SELECT * FROM events WHERE request_id = ? ORDER BY id", (request_id,))
    return [dict(r, data=json.loads(r["data_json"])) for r in rows]


def redact(db, event_ids):
    """Retention: empty IP address and browser of version-2 entries. Returns how many were changed.

    Version-1 entries (written before phase 5) keep their values: changing them
    would break the chain.
    """
    if not event_ids:
        return 0
    now = clock.now_iso()
    changed = 0
    with db.tx() as c:
        for chunk in range(0, len(event_ids), 500):
            ids = event_ids[chunk:chunk + 500]
            marks = ",".join("?" * len(ids))
            cur = c.execute(
                f"UPDATE events SET ip = NULL, user_agent = NULL, field_salt = NULL, redacted_at = ?"
                f" WHERE id IN ({marks}) AND hash_version >= 2 AND redacted_at IS NULL"
                f" AND (ip IS NOT NULL OR user_agent IS NOT NULL)", [now] + ids)
            changed += cur.rowcount
    return changed
