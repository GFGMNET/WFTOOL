"""Read and write access to master data: AD users, groups, lists and settings.

Phase 2 adds the CSV import on top of these functions; the engine only reads.
Nothing here deletes rows: entries are deactivated so history keeps pointing
at them (design: "Never delete").
"""

from . import clock


# --- users --------------------------------------------------------------

def upsert_user(db, email, display_name, *, manager_email=None, department=None,
                samaccountname=None, active=True):
    with db.tx() as c:
        c.execute(
            "INSERT INTO ad_users (email, samaccountname, display_name, department, manager_email, active, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(email) DO UPDATE SET samaccountname = excluded.samaccountname,"
            " display_name = excluded.display_name, department = excluded.department,"
            " manager_email = excluded.manager_email, active = excluded.active, updated_at = excluded.updated_at",
            (email.strip(), samaccountname, display_name, department,
             manager_email.strip() if manager_email else None, int(active), clock.now_iso()),
        )


def get_user(db, email, *, active_only=True):
    if not email:
        return None
    sql = "SELECT * FROM ad_users WHERE email = ?" + (" AND active = 1" if active_only else "")
    row = db.one(sql, (email.strip(),))
    return dict(row) if row else None


# --- groups -------------------------------------------------------------

def upsert_group(db, name, member_emails, *, active=True):
    """Set a group and its exact member list; members not listed become inactive."""
    wanted = {e.strip().lower() for e in member_emails}
    with db.tx() as c:
        c.execute(
            "INSERT INTO groups (name, active, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET active = excluded.active, updated_at = excluded.updated_at",
            (name, int(active), clock.now_iso()),
        )
        existing = {r["member_email"].lower() for r in
                    c.execute("SELECT member_email FROM group_members WHERE group_name = ?", (name,))}
        for email in wanted:
            if email in existing:
                c.execute("UPDATE group_members SET active = 1 WHERE group_name = ? AND member_email = ?",
                          (name, email))
            else:
                c.execute("INSERT INTO group_members (group_name, member_email, active) VALUES (?, ?, 1)",
                          (name, email))
        for email in existing - wanted:
            c.execute("UPDATE group_members SET active = 0 WHERE group_name = ? AND member_email = ?",
                      (name, email))


def get_group(db, name):
    row = db.one("SELECT * FROM groups WHERE name = ? AND active = 1", (name,))
    return dict(row) if row else None


def group_members(db, name):
    """Active members of an active group who are also active AD users."""
    rows = db.all(
        "SELECT u.email FROM group_members m"
        " JOIN groups g ON g.name = m.group_name AND g.active = 1"
        " JOIN ad_users u ON u.email = m.member_email AND u.active = 1"
        " WHERE m.group_name = ? AND m.active = 1 ORDER BY u.email",
        (name,),
    )
    return [r["email"] for r in rows]


# --- lists --------------------------------------------------------------

def ensure_list(db, name, label=None, *, source="app"):
    with db.tx() as c:
        c.execute(
            "INSERT INTO lists (name, label, source) VALUES (?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET label = excluded.label, source = excluded.source",
            (name, label or name, source),
        )


def get_list(db, name):
    row = db.one("SELECT * FROM lists WHERE name = ?", (name,))
    return dict(row) if row else None


def upsert_list_item(db, list_name, value, label=None, *, needs_approval=False,
                     approver_email=None, sort=0, active=True):
    with db.tx() as c:
        c.execute(
            "INSERT INTO list_items (list_name, value, label, needs_approval, approver_email, sort, active)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(list_name, value) DO UPDATE SET label = excluded.label,"
            " needs_approval = excluded.needs_approval, approver_email = excluded.approver_email,"
            " sort = excluded.sort, active = excluded.active",
            (list_name, value, label or value, int(needs_approval),
             approver_email.strip() if approver_email else None, sort, int(active)),
        )


def list_items(db, list_name, *, active_only=True):
    sql = "SELECT * FROM list_items WHERE list_name = ?" + (" AND active = 1" if active_only else "")
    return [dict(r) for r in db.all(sql + " ORDER BY sort, label", (list_name,))]


def list_item(db, list_name, value):
    row = db.one("SELECT * FROM list_items WHERE list_name = ? AND value = ?", (list_name, value))
    return dict(row) if row else None


# --- settings -----------------------------------------------------------

DEFAULTS = {
    "link_validity_days": "14",
    "reminder_working_days": "5",
    "starter_notice_working_days": "10",
    "default_email_language": "de",
    "admin_email": "",
    "company_domains": "",
}


def get_setting(db, key, default=None):
    row = db.one("SELECT value FROM settings WHERE key = ?", (key,))
    if row is not None and row["value"] is not None:
        return row["value"]
    return DEFAULTS.get(key, default) if default is None else default


def set_setting(db, key, value, *, is_secret=False):
    with db.tx() as c:
        c.execute(
            "INSERT INTO settings (key, value, is_secret) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, is_secret = excluded.is_secret",
            (key, value, int(is_secret)),
        )


def company_domains(db):
    raw = get_setting(db, "company_domains") or ""
    return [d.strip().lower().lstrip("@") for d in raw.replace(";", ",").split(",") if d.strip()]


def email_allowed(db, email):
    """True if `email` belongs to a company domain (or no domain is configured yet)."""
    domains = company_domains(db)
    if not domains:
        return True
    return "@" in email and email.rsplit("@", 1)[1].lower() in domains
