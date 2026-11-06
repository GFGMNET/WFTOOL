"""Automatic CSV import of users, groups and the software list.

Your export scripts drop users.csv, groups.csv and software.csv into the import
folder. Files are UTF-8 (a BOM is accepted), semicolon-separated, with a header
row. Rules from the design (v2, "Import rules"):

- AD is the source: these rows cannot be edited in the app.
- Never delete: rows missing from a file, or disabled, become inactive.
- Protection: an empty file, missing columns, or a file that would deactivate
  more than 10 % of the active rows at once is rejected; the old data stays.
- Every run is logged in `imports` and in the audit log.
- A file identical to the last imported one is skipped.
"""

import csv
import hashlib
import io
import re
from pathlib import Path

from . import audit, clock, masterdata

KINDS = ("users", "groups", "software")
COLUMNS = {
    "users": ["samaccountname", "email", "displayname", "department", "manager_email", "enabled"],
    "groups": ["group", "member_email"],
    "software": ["software", "needs_approval", "approver_email"],
}
SOFTWARE_LIST = "software"
_EMAIL = re.compile(r"^[^@\s;]+@[^@\s;]+\.[^@\s;]+$")
_TRUE = {"1", "true", "yes", "ja", "y", "j", "x", "wahr"}
_FALSE = {"0", "false", "no", "nein", "n", "", "falsch"}


class ImportResult(dict):
    """status: imported | rejected | unchanged; counts and messages for the admin."""

    @property
    def rejected(self):
        return self["status"] == "rejected"


def run_folder(db, folder):
    """Import every known file present in `folder`, in dependency order."""
    results = []
    folder = Path(folder)
    for kind in KINDS:
        path = folder / f"{kind}.csv"
        if path.exists():
            results.append(import_file(db, kind, path))
    return results


def import_file(db, kind, path):
    path = Path(path)
    return import_bytes(db, kind, path.read_bytes(), file_name=path.name)


def import_bytes(db, kind, data, *, file_name):
    if kind not in KINDS:
        raise ValueError(f"Unknown import kind {kind}")
    fp = hashlib.sha256(data).hexdigest()
    last = db.one("SELECT fingerprint FROM imports WHERE kind = ? AND status = 'imported' ORDER BY id DESC LIMIT 1",
                  (kind,))
    if last and last["fingerprint"] == fp:
        return ImportResult(kind=kind, file=file_name, status="unchanged", added=0, changed=0, deactivated=0,
                            rejected_rows=[], message="File unchanged since the last import.")

    started = clock.now_iso()
    rows, problem = _parse(kind, data)
    if problem:
        return _finish(db, kind, file_name, fp, started, "rejected", problem, rejected_rows=[])
    handler = {"users": _import_users, "groups": _import_groups, "software": _import_software}[kind]
    with db.tx():
        result = handler(db, rows)
        if result.get("problem"):
            # nothing written yet: handlers check everything before changing data
            return _finish(db, kind, file_name, fp, started, "rejected", result["problem"],
                           rejected_rows=result["rejected_rows"])
        return _finish(db, kind, file_name, fp, started, "imported", None, **result)


def _finish(db, kind, file_name, fp, started, status, message, *, added=0, changed=0, deactivated=0,
            rejected_rows=(), problem=None):
    rejected_rows = list(rejected_rows)
    if status == "imported" and rejected_rows:
        message = f"{len(rejected_rows)} row(s) skipped."
    with db.tx() as c:
        c.execute(
            "INSERT INTO imports (kind, file_name, started_at, finished_at, status, added, changed, deactivated,"
            " rejected_rows, message, fingerprint) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, file_name, started, clock.now_iso(), status, added, changed, deactivated, len(rejected_rows),
             message, fp),
        )
        audit.record(db, f"import.{kind}", actor="import", data={
            "file": file_name, "fingerprint": fp, "status": status, "added": added, "changed": changed,
            "deactivated": deactivated, "rejected_rows": rejected_rows[:50], "message": message,
        })
    return ImportResult(kind=kind, file=file_name, status=status, added=added, changed=changed,
                        deactivated=deactivated, rejected_rows=rejected_rows, message=message)


def _parse(kind, data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "The file is not UTF-8. Export it with -Encoding UTF8."
    if not text.strip():
        return None, "The file is empty."
    reader = csv.reader(io.StringIO(text), delimiter=";")
    try:
        header = [h.strip().lower() for h in next(reader)]
    except StopIteration:
        return None, "The file is empty."
    missing = [c for c in COLUMNS[kind] if c not in header]
    if missing:
        hint = " The separator must be a semicolon." if len(header) == 1 and "," in header[0] else ""
        return None, f"Missing columns: {', '.join(missing)}.{hint}"
    rows = []
    for line_no, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        row = {name: (values[i].strip() if i < len(values) else "") for i, name in enumerate(header)}
        row["_line"] = line_no
        rows.append(row)
    if not rows:
        return None, "The file has a header but no rows."
    return rows, None


def _bool(value):
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(f"'{value}' is not yes/no")


def _limit(db, active_count):
    pct = float(masterdata.get_setting(db, "import_max_deactivate_percent", "10"))
    return max(1, int(active_count * pct / 100))


def _too_many(db, deactivate, active, what):
    if active and deactivate > _limit(db, active):
        return (f"The file would deactivate {deactivate} of {active} active {what}"
                f" (more than {masterdata.get_setting(db, 'import_max_deactivate_percent', '10')} %)."
                " Nothing was changed. Check the export, or raise the limit in Settings.")
    return None


# --- users ------------------------------------------------------------

def _import_users(db, rows):
    rejected, users, seen = [], {}, set()
    for r in rows:
        email = r["email"].lower()
        try:
            if not _EMAIL.match(email):
                raise ValueError("email missing or invalid")
            if email in seen:
                raise ValueError("email appears twice")
            if not r["displayname"]:
                raise ValueError("displayname missing")
            manager = r["manager_email"].lower()
            if manager and not _EMAIL.match(manager):
                raise ValueError("manager_email invalid")
            enabled = _bool(r["enabled"]) if r["enabled"] else True
        except ValueError as exc:
            rejected.append({"line": r["_line"], "reason": str(exc)})
            continue
        seen.add(email)
        users[email] = {"display_name": r["displayname"], "department": r["department"] or None,
                        "manager_email": manager or None, "samaccountname": r["samaccountname"] or None,
                        "active": enabled}
    if not users:
        return {"problem": "No valid rows in the file.", "rejected_rows": rejected}

    existing = {r["email"].lower(): dict(r) for r in db.all("SELECT * FROM ad_users")}
    active_now = {e for e, u in existing.items() if u["active"]}
    will_be_active = {e for e, u in users.items() if u["active"]}
    to_deactivate = active_now - will_be_active
    problem = _too_many(db, len(to_deactivate), len(active_now), "users")
    if problem:
        return {"problem": problem, "rejected_rows": rejected}

    added = changed = 0
    for email, u in users.items():
        old = existing.get(email)
        if old is None:
            added += 1
        elif (old["display_name"], old["department"], old["manager_email"], old["samaccountname"],
              bool(old["active"])) != (u["display_name"], u["department"], u["manager_email"],
                                       u["samaccountname"], u["active"]):
            changed += 1
        else:
            continue
        masterdata.upsert_user(db, email, u["display_name"], manager_email=u["manager_email"],
                               department=u["department"], samaccountname=u["samaccountname"], active=u["active"])
    for email in (set(existing) - set(users)):
        if existing[email]["active"]:
            db.execute("UPDATE ad_users SET active = 0, updated_at = ? WHERE email = ?", (clock.now_iso(), email))
    return {"added": added, "changed": changed, "deactivated": len(to_deactivate), "rejected_rows": rejected}


# --- groups -----------------------------------------------------------

def _import_groups(db, rows):
    rejected, groups = [], {}
    for r in rows:
        name, email = r["group"], r["member_email"].lower()
        if not name:
            rejected.append({"line": r["_line"], "reason": "group missing"})
            continue
        if not _EMAIL.match(email):
            rejected.append({"line": r["_line"], "reason": "member_email missing or invalid"})
            continue
        if not masterdata.get_user(db, email):
            rejected.append({"line": r["_line"], "reason": f"{email} is not an active user"})
            continue
        groups.setdefault(name, set()).add(email)
    if not groups:
        return {"problem": "No valid rows in the file.", "rejected_rows": rejected}

    current = {}
    for r in db.all("SELECT g.name, m.member_email FROM groups g LEFT JOIN group_members m"
                    " ON m.group_name = g.name AND m.active = 1 WHERE g.active = 1"):
        current.setdefault(r["name"].lower(), {"name": r["name"], "members": set()})
        if r["member_email"]:
            current[r["name"].lower()]["members"].add(r["member_email"].lower())
    wanted = {name.lower(): (name, members) for name, members in groups.items()}

    active_memberships = sum(len(g["members"]) for g in current.values())
    removed = sum(len(g["members"] - wanted.get(key, (None, set()))[1]) for key, g in current.items())
    problem = _too_many(db, removed, active_memberships, "group memberships")
    if problem:
        return {"problem": problem, "rejected_rows": rejected}

    added = changed = 0
    for key, (name, members) in wanted.items():
        old = current.get(key)
        if old is None:
            added += 1
        elif old["members"] != members:
            changed += 1
        else:
            continue
        masterdata.upsert_group(db, name, members)
    for key, g in current.items():
        if key not in wanted:
            db.execute("UPDATE groups SET active = 0, updated_at = ? WHERE name = ?", (clock.now_iso(), g["name"]))
            db.execute("UPDATE group_members SET active = 0 WHERE group_name = ?", (g["name"],))
    return {"added": added, "changed": changed, "deactivated": removed, "rejected_rows": rejected}


# --- software ---------------------------------------------------------

def _import_software(db, rows):
    rejected, items = [], {}
    for r in rows:
        name = r["software"]
        try:
            if not name:
                raise ValueError("software missing")
            if name.lower() in {k.lower() for k in items}:
                raise ValueError("software appears twice")
            needs = _bool(r["needs_approval"])
        except ValueError as exc:
            rejected.append({"line": r["_line"], "reason": str(exc)})
            continue
        approver = r["approver_email"].lower() or None
        usable = True
        if needs:
            if not approver:
                usable = False
                rejected.append({"line": r["_line"], "reason": f"'{name}' needs approval but has no approver;"
                                                               " not usable until fixed"})
            elif not masterdata.get_user(db, approver):
                usable = False
                rejected.append({"line": r["_line"], "reason": f"approver {approver} of '{name}' is not an"
                                                               " active user; not usable until fixed"})
        items[name] = {"needs": needs, "approver": approver, "usable": usable}
    if not items:
        return {"problem": "No valid rows in the file.", "rejected_rows": rejected}

    masterdata.ensure_list(db, SOFTWARE_LIST, "Software", source="import")
    existing = {i["value"]: i for i in masterdata.list_items(db, SOFTWARE_LIST, active_only=False)}
    active_now = {v for v, i in existing.items() if i["active"]}
    to_deactivate = active_now - {n for n, i in items.items() if i["usable"]}
    problem = _too_many(db, len(to_deactivate), len(active_now), "software entries")
    if problem:
        return {"problem": problem, "rejected_rows": rejected}

    added = changed = 0
    for sort, (name, i) in enumerate(items.items()):
        old = existing.get(name)
        if old is None:
            added += 1
        elif (bool(old["needs_approval"]), old["approver_email"], bool(old["active"]), old["sort"]) != (
                i["needs"], i["approver"], i["usable"], sort):
            changed += 1
        else:
            continue
        masterdata.upsert_list_item(db, SOFTWARE_LIST, name, name, needs_approval=i["needs"],
                                    approver_email=i["approver"], sort=sort, active=i["usable"])
    for name in set(existing) - set(items):
        if existing[name]["active"]:
            db.execute("UPDATE list_items SET active = 0 WHERE list_name = ? AND value = ?", (SOFTWARE_LIST, name))
    return {"added": added, "changed": changed, "deactivated": len(to_deactivate), "rejected_rows": rejected}


def history(db, limit=50):
    return [dict(r) for r in db.all("SELECT * FROM imports ORDER BY id DESC LIMIT ?", (limit,))]
