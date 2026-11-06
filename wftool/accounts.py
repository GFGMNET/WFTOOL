"""Local login accounts for HR, IT and admins (approvers and signers never log in).

Rules (design v2, decided 6 Nov 2026): passwords at least 12 characters; 5 wrong
attempts lock the account for 15 minutes; sessions end after 60 minutes of
inactivity (enforced by the web layer). A fresh installation gets the account
admin / admin, which must be changed at the first login.
"""

from datetime import timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from . import audit, clock
from .errors import NotFound, StateError, ValidationError

ROLES = ("admin", "starter", "auditor")
LANGUAGES = ("de", "en")
MIN_PASSWORD_LENGTH = 12
MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15
SESSION_MINUTES = 60
DEFAULT_ADMIN = ("admin", "admin")


class LoginError(StateError):
    code = "login_failed"

    def __init__(self, message, reason):
        super().__init__(message)
        self.reason = reason  # "invalid", "locked" or "inactive"


def ensure_default_admin(db):
    """Create admin/admin (must change at first login) when no account exists yet."""
    if db.one("SELECT 1 FROM accounts LIMIT 1"):
        return False
    username, password = DEFAULT_ADMIN
    with db.tx() as c:
        c.execute(
            "INSERT INTO accounts (username, email, password_hash, role, language, active, created_at,"
            " must_change_password) VALUES (?, NULL, ?, 'admin', 'de', 1, ?, 1)",
            (username, generate_password_hash(password), clock.now_iso()),
        )
        audit.record(db, "account.default_admin_created", actor="system", data={"username": username})
    return True


def check_password_rules(password):
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"The password needs at least {MIN_PASSWORD_LENGTH} characters.",
                              details=["password: too short"])


def get_account(db, account_id):
    row = db.one("SELECT * FROM accounts WHERE id = ?", (account_id,))
    if not row:
        raise NotFound(f"Account {account_id} not found.")
    return dict(row)


def list_accounts(db):
    return [dict(r) for r in db.all("SELECT * FROM accounts ORDER BY username")]


def create_account(db, username, password, role, *, email=None, language="de", by):
    username = (username or "").strip()
    email = (email or "").strip().lower() or None
    problems = []
    if not username:
        problems.append("username: required")
    if role not in ROLES:
        problems.append("role: unknown")
    if language not in LANGUAGES:
        problems.append("language: unknown")
    if role == "starter" and not email:
        problems.append("email: a starter needs the email address of their AD user")
    if problems:
        raise ValidationError("Please correct the account.", details=problems)
    check_password_rules(password)
    if db.one("SELECT 1 FROM accounts WHERE username = ?", (username,)):
        raise ValidationError("This username already exists.", details=["username: taken"])
    now = clock.now_iso()
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO accounts (username, email, password_hash, role, language, active, created_at,"
            " must_change_password, password_changed_at) VALUES (?, ?, ?, ?, ?, 1, ?, 1, ?)",
            (username, email, generate_password_hash(password), role, language, now, now),
        )
        audit.record(db, "account.created", actor=by,
                     data={"account": cur.lastrowid, "username": username, "role": role, "email": email})
    return get_account(db, cur.lastrowid)


def update_account(db, account_id, *, by, role=None, email=None, language=None, active=None):
    acc = get_account(db, account_id)
    changes = {}
    if role is not None and role != acc["role"]:
        if role not in ROLES:
            raise ValidationError("Unknown role.", details=["role: unknown"])
        changes["role"] = role
    if email is not None and (email.strip().lower() or None) != acc["email"]:
        changes["email"] = email.strip().lower() or None
    if language is not None and language != acc["language"]:
        if language not in LANGUAGES:
            raise ValidationError("Unknown language.", details=["language: unknown"])
        changes["language"] = language
    if active is not None and int(active) != acc["active"]:
        changes["active"] = int(active)
    if not changes:
        return acc
    new_role = changes.get("role", acc["role"])
    new_active = changes.get("active", acc["active"])
    if acc["role"] == "admin" and (new_role != "admin" or not new_active) and _active_admins(db) <= 1:
        raise StateError("The last active admin cannot be removed or deactivated.")
    if new_role == "starter" and not changes.get("email", acc["email"]):
        raise ValidationError("A starter needs an email address.", details=["email: required"])
    with db.tx() as c:
        sets = ", ".join(f"{k} = ?" for k in changes)
        c.execute(f"UPDATE accounts SET {sets} WHERE id = ?", (*changes.values(), account_id))
        audit.record(db, "account.updated", actor=by, data={"account": account_id, "changes": changes})
    return get_account(db, account_id)


def _active_admins(db):
    return db.one("SELECT COUNT(*) AS n FROM accounts WHERE role = 'admin' AND active = 1")["n"]


def authenticate(db, username, password, *, ip=None, user_agent=None):
    """Return the account on success; raise LoginError otherwise. Counts and locks failures."""
    row = db.one("SELECT * FROM accounts WHERE username = ?", ((username or "").strip(),))
    now = clock.now()
    if not row:
        audit.record(db, "login.failed", actor=(username or "")[:64], ip=ip, user_agent=user_agent,
                     data={"reason": "unknown user"})
        raise LoginError("Username or password is wrong.", "invalid")
    acc = dict(row)
    if acc["locked_until"] and acc["locked_until"] > now.replace(microsecond=0).isoformat():
        audit.record(db, "login.failed", actor=acc["username"], ip=ip, user_agent=user_agent,
                     data={"reason": "locked"})
        raise LoginError("Too many wrong attempts. Try again later.", "locked")
    if not check_password_hash(acc["password_hash"] or "", password or ""):
        attempts = acc["failed_attempts"] + 1
        locked_until = None
        if attempts >= MAX_FAILED_ATTEMPTS:
            locked_until = (now + timedelta(minutes=LOCK_MINUTES)).replace(microsecond=0).isoformat()
            attempts = 0
        with db.tx() as c:
            c.execute("UPDATE accounts SET failed_attempts = ?, locked_until = ? WHERE id = ?",
                      (attempts, locked_until, acc["id"]))
            audit.record(db, "login.failed", actor=acc["username"], ip=ip, user_agent=user_agent,
                         data={"reason": "wrong password", "locked_until": locked_until})
        if locked_until:
            raise LoginError("Too many wrong attempts. Try again later.", "locked")
        raise LoginError("Username or password is wrong.", "invalid")
    if not acc["active"]:
        audit.record(db, "login.failed", actor=acc["username"], ip=ip, user_agent=user_agent,
                     data={"reason": "inactive"})
        raise LoginError("This account is deactivated.", "inactive")
    with db.tx() as c:
        c.execute("UPDATE accounts SET failed_attempts = 0, locked_until = NULL, last_login_at = ? WHERE id = ?",
                  (clock.now_iso(), acc["id"]))
        audit.record(db, "login.success", actor=acc["username"], ip=ip, user_agent=user_agent, data={})
    return get_account(db, acc["id"])


def change_password(db, account_id, old_password, new_password):
    acc = get_account(db, account_id)
    if not check_password_hash(acc["password_hash"] or "", old_password or ""):
        raise ValidationError("The current password is wrong.", details=["old_password: wrong"])
    check_password_rules(new_password)
    if new_password == old_password:
        raise ValidationError("Choose a password different from the current one.", details=["password: same"])
    _set_password(db, acc, new_password, must_change=False, by=acc["username"])


def reset_password(db, account_id, new_password, *, by):
    """Admin sets a temporary password; the user must change it at the next login."""
    acc = get_account(db, account_id)
    check_password_rules(new_password)
    _set_password(db, acc, new_password, must_change=True, by=by)


def _set_password(db, acc, password, *, must_change, by):
    with db.tx() as c:
        c.execute(
            "UPDATE accounts SET password_hash = ?, must_change_password = ?, password_changed_at = ?,"
            " failed_attempts = 0, locked_until = NULL WHERE id = ?",
            (generate_password_hash(password), int(must_change), clock.now_iso(), acc["id"]),
        )
        audit.record(db, "account.password_changed", actor=by,
                     data={"account": acc["id"], "username": acc["username"], "temporary": must_change})
