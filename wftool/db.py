"""SQLite access: one database file, WAL mode, numbered migrations.

All writes go through `Database.tx()`, which opens an IMMEDIATE transaction so
that the audit chain and request state are always written in one ordered step.
"""

import sqlite3
import threading
from contextlib import contextmanager

MIGRATIONS = [
    # 1: full schema of version 1 (design document v2, "Data model").
    """
    CREATE TABLE accounts (
        id            INTEGER PRIMARY KEY,
        username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
        email         TEXT COLLATE NOCASE,
        password_hash TEXT,
        role          TEXT NOT NULL CHECK (role IN ('admin', 'starter', 'auditor')),
        language      TEXT NOT NULL DEFAULT 'de' CHECK (language IN ('de', 'en')),
        active        INTEGER NOT NULL DEFAULT 1,
        created_at    TEXT NOT NULL
    );

    CREATE TABLE ad_users (
        email          TEXT PRIMARY KEY COLLATE NOCASE,
        samaccountname TEXT,
        display_name   TEXT NOT NULL,
        department     TEXT,
        manager_email  TEXT COLLATE NOCASE,
        active         INTEGER NOT NULL DEFAULT 1,
        updated_at     TEXT NOT NULL
    );

    CREATE TABLE groups (
        name       TEXT PRIMARY KEY COLLATE NOCASE,
        active     INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE group_members (
        group_name   TEXT NOT NULL COLLATE NOCASE REFERENCES groups(name),
        member_email TEXT NOT NULL COLLATE NOCASE,
        active       INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (group_name, member_email)
    );

    CREATE TABLE lists (
        name   TEXT PRIMARY KEY COLLATE NOCASE,
        label  TEXT NOT NULL,
        source TEXT NOT NULL CHECK (source IN ('app', 'import'))
    );

    CREATE TABLE list_items (
        id             INTEGER PRIMARY KEY,
        list_name      TEXT NOT NULL COLLATE NOCASE REFERENCES lists(name),
        value          TEXT NOT NULL,
        label          TEXT NOT NULL,
        needs_approval INTEGER NOT NULL DEFAULT 0,
        approver_email TEXT COLLATE NOCASE,
        sort           INTEGER NOT NULL DEFAULT 0,
        active         INTEGER NOT NULL DEFAULT 1,
        UNIQUE (list_name, value)
    );

    CREATE TABLE workflows (
        id         INTEGER PRIMARY KEY,
        name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
        owner      TEXT,
        draft_json TEXT,
        active     INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE workflow_versions (
        id              INTEGER PRIMARY KEY,
        workflow_id     INTEGER NOT NULL REFERENCES workflows(id),
        version         INTEGER NOT NULL,
        definition_json TEXT NOT NULL,
        fingerprint     TEXT NOT NULL,
        published_by    TEXT NOT NULL,
        published_at    TEXT NOT NULL,
        UNIQUE (workflow_id, version)
    );

    CREATE TABLE requests (
        id                  INTEGER PRIMARY KEY,
        number              TEXT NOT NULL UNIQUE,
        workflow_version_id INTEGER NOT NULL REFERENCES workflow_versions(id),
        status              TEXT NOT NULL CHECK (status IN ('open', 'returned', 'completed', 'cancelled')),
        starter_email       TEXT NOT NULL COLLATE NOCASE,
        current_step        INTEGER,
        round               INTEGER NOT NULL DEFAULT 1,
        created_at          TEXT NOT NULL,
        updated_at          TEXT NOT NULL,
        closed_at           TEXT
    );

    CREATE TABLE form_versions (
        id          INTEGER PRIMARY KEY,
        request_id  INTEGER NOT NULL REFERENCES requests(id),
        version     INTEGER NOT NULL,
        data_json   TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        created_by  TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        UNIQUE (request_id, version)
    );

    CREATE TABLE tasks (
        id                    INTEGER PRIMARY KEY,
        request_id            INTEGER NOT NULL REFERENCES requests(id),
        round                 INTEGER NOT NULL,
        step_index            INTEGER NOT NULL,
        step_id               TEXT NOT NULL,
        type                  TEXT NOT NULL,
        assignee_email        TEXT NOT NULL COLLATE NOCASE,
        item_value            TEXT,
        group_name            TEXT,
        status                TEXT NOT NULL CHECK (status IN ('open', 'paper_pending', 'done', 'cancelled')),
        decision              TEXT,
        reason                TEXT,
        form_version          INTEGER,
        form_fingerprint      TEXT,
        decided_by            TEXT,
        decided_at            TEXT,
        ip                    TEXT,
        user_agent            TEXT,
        signature_fingerprint TEXT,
        cancel_reason         TEXT,
        created_at            TEXT NOT NULL
    );
    CREATE INDEX tasks_request ON tasks(request_id, round, step_index);
    CREATE INDEX tasks_open ON tasks(status, assignee_email);

    CREATE TABLE links (
        id            INTEGER PRIMARY KEY,
        task_id       INTEGER NOT NULL REFERENCES tasks(id),
        email         TEXT NOT NULL COLLATE NOCASE,
        token_hash    TEXT NOT NULL UNIQUE,
        expires_at    TEXT NOT NULL,
        used_at       TEXT,
        revoked_at    TEXT,
        revoke_reason TEXT,
        created_at    TEXT NOT NULL
    );

    CREATE TABLE events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER,
        ts         TEXT NOT NULL,
        type       TEXT NOT NULL,
        actor      TEXT,
        ip         TEXT,
        user_agent TEXT,
        data_json  TEXT NOT NULL,
        prev_hash  TEXT NOT NULL,
        hash       TEXT NOT NULL
    );
    CREATE INDEX events_request ON events(request_id);
    CREATE INDEX events_ts ON events(ts);

    CREATE TABLE outbox (
        id              INTEGER PRIMARY KEY,
        request_id      INTEGER,
        to_addr         TEXT NOT NULL,
        subject         TEXT NOT NULL,
        body_text       TEXT NOT NULL,
        body_html       TEXT,
        attachment_path TEXT,
        status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
        attempts        INTEGER NOT NULL DEFAULT 0,
        last_error      TEXT,
        next_attempt_at TEXT,
        created_at      TEXT NOT NULL,
        sent_at         TEXT
    );

    CREATE TABLE settings (
        key       TEXT PRIMARY KEY,
        value     TEXT,
        is_secret INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE imports (
        id            INTEGER PRIMARY KEY,
        kind          TEXT NOT NULL,
        file_name     TEXT,
        started_at    TEXT NOT NULL,
        finished_at   TEXT,
        status        TEXT NOT NULL,
        added         INTEGER NOT NULL DEFAULT 0,
        changed       INTEGER NOT NULL DEFAULT 0,
        deactivated   INTEGER NOT NULL DEFAULT 0,
        rejected_rows INTEGER NOT NULL DEFAULT 0,
        message       TEXT
    );
    """,
    # 2: login rules, start permissions per workflow, import fingerprints.
    """
    ALTER TABLE accounts ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE accounts ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE accounts ADD COLUMN locked_until TEXT;
    ALTER TABLE accounts ADD COLUMN last_login_at TEXT;
    ALTER TABLE accounts ADD COLUMN password_changed_at TEXT;
    ALTER TABLE workflows ADD COLUMN start_groups TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE imports ADD COLUMN fingerprint TEXT;
    """,
    # 3: phase 4 – reminders, files of signatures and paper scans, lookups for links and the outbox.
    """
    ALTER TABLE tasks ADD COLUMN reminded_at TEXT;
    ALTER TABLE tasks ADD COLUMN starter_noticed_at TEXT;
    ALTER TABLE tasks ADD COLUMN signature_file TEXT;
    ALTER TABLE tasks ADD COLUMN scan_file TEXT;
    ALTER TABLE outbox ADD COLUMN attachment_name TEXT;
    ALTER TABLE outbox ADD COLUMN kind TEXT;
    CREATE INDEX links_task ON links(task_id);
    CREATE INDEX outbox_due ON outbox(status, next_attempt_at);
    """,
    # 4: phase 5 – IP address and browser enter the chain as salted fingerprints, so retention can
    # remove the values later without breaking the tamper check; lookups for search and the audit page.
    """
    ALTER TABLE events ADD COLUMN hash_version INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE events ADD COLUMN field_salt TEXT;
    ALTER TABLE events ADD COLUMN ip_hash TEXT;
    ALTER TABLE events ADD COLUMN ua_hash TEXT;
    ALTER TABLE events ADD COLUMN redacted_at TEXT;
    ALTER TABLE tasks ADD COLUMN redacted_at TEXT;
    CREATE INDEX events_type ON events(type);
    CREATE INDEX requests_created ON requests(created_at);
    CREATE INDEX tasks_decided_by ON tasks(decided_by);
    """,
]


class Database:
    """A thread-safe wrapper around one SQLite connection per thread."""

    def __init__(self, path):
        self.path = str(path)
        self._local = threading.local()
        self.migrate()

    def conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, isolation_level=None, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            c.execute("PRAGMA busy_timeout = 30000")
            if self.path != ":memory:":
                c.execute("PRAGMA journal_mode = WAL")
                c.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = c
            self._local.depth = 0
        return c

    @contextmanager
    def tx(self):
        """IMMEDIATE transaction; nested calls join the outer one."""
        c = self.conn()
        if self._local.depth:
            self._local.depth += 1
            try:
                yield c
            finally:
                self._local.depth -= 1
            return
        c.execute("BEGIN IMMEDIATE")
        self._local.depth = 1
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")
        finally:
            self._local.depth = 0

    def one(self, sql, params=()):
        return self.conn().execute(sql, params).fetchone()

    def all(self, sql, params=()):
        return self.conn().execute(sql, params).fetchall()

    def execute(self, sql, params=()):
        return self.conn().execute(sql, params)

    def migrate(self):
        c = self.conn()
        current = c.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(MIGRATIONS, start=1):
            if number <= current:
                continue
            c.execute("BEGIN IMMEDIATE")
            try:
                for statement in _split_sql(script):
                    c.execute(statement)
                c.execute(f"PRAGMA user_version = {number}")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")

    def schema_version(self):
        return self.conn().execute("PRAGMA user_version").fetchone()[0]

    def backup(self, target):
        """A consistent copy of the database while the app keeps running (SQLite online backup)."""
        import sqlite3
        dest = sqlite3.connect(str(target))
        try:
            self.conn().backup(dest)
        finally:
            dest.close()

    def close(self):
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None


def _split_sql(script):
    return [s.strip() for s in script.split(";") if s.strip()]
