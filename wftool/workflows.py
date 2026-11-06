"""Drafts, publishing and versions of workflows.

The admin edits a draft; Publish checks it and stores an immutable version.
Running requests keep the version they started with (design: "Publish and
versions").
"""

import json

from . import audit, clock, masterdata
from .crypto import canonical_json, fingerprint
from .definitions import publish_problems, validate
from .errors import DefinitionError, NotFound, PublishError


def save_draft(db, definition, *, by):
    """Create the workflow if new, or replace its draft. Drafts may be incomplete."""
    if not isinstance(definition, dict) or not str(definition.get("name", "")).strip():
        raise DefinitionError("A draft needs a workflow name.")
    name = definition["name"].strip()
    now = clock.now_iso()
    with db.tx() as c:
        row = c.execute("SELECT id FROM workflows WHERE name = ?", (name,)).fetchone()
        if row:
            c.execute("UPDATE workflows SET draft_json = ?, updated_at = ? WHERE id = ?",
                      (canonical_json(definition), now, row["id"]))
            wid = row["id"]
        else:
            cur = c.execute(
                "INSERT INTO workflows (name, owner, draft_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (name, by, canonical_json(definition), now, now),
            )
            wid = cur.lastrowid
        audit.record(db, "workflow.draft_saved", actor=by, data={"workflow": name, "workflow_id": wid})
    return wid


def get_workflow(db, name):
    row = db.one("SELECT * FROM workflows WHERE name = ?", (name,))
    if not row:
        raise NotFound(f"Workflow '{name}' not found.")
    w = dict(row)
    w["draft"] = json.loads(w.pop("draft_json")) if w["draft_json"] else None
    return w


def publish(db, name, *, by):
    """Check the draft and store it as the next version. Returns the version row.

    Publishing an unchanged draft returns the latest version without a new one.
    """
    w = get_workflow(db, name)
    draft = w["draft"]
    if draft is None:
        raise PublishError(f"Workflow '{name}' has no draft.")
    problems = publish_problems(db, draft)
    if problems:
        raise PublishError(f"Workflow '{name}' cannot be published.", details=problems)
    fp = fingerprint(draft)
    with db.tx() as c:
        last = c.execute(
            "SELECT * FROM workflow_versions WHERE workflow_id = ? ORDER BY version DESC LIMIT 1",
            (w["id"],),
        ).fetchone()
        if last and last["fingerprint"] == fp:
            return _version(dict(last))
        number = (last["version"] if last else 0) + 1
        cur = c.execute(
            "INSERT INTO workflow_versions (workflow_id, version, definition_json, fingerprint, published_by,"
            " published_at) VALUES (?, ?, ?, ?, ?, ?)",
            (w["id"], number, canonical_json(draft), fp, by, clock.now_iso()),
        )
        audit.record(db, "workflow.published", actor=by,
                     data={"workflow": name, "version": number, "fingerprint": fp})
        row = c.execute("SELECT * FROM workflow_versions WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _version(dict(row))


def latest_version(db, name):
    row = db.one(
        "SELECT v.* FROM workflow_versions v JOIN workflows w ON w.id = v.workflow_id"
        " WHERE w.name = ? AND w.active = 1 ORDER BY v.version DESC LIMIT 1",
        (name,),
    )
    if not row:
        raise NotFound(f"Workflow '{name}' has no published version.")
    return _version(dict(row))


def get_version(db, version_id):
    row = db.one("SELECT * FROM workflow_versions WHERE id = ?", (version_id,))
    if not row:
        raise NotFound(f"Workflow version {version_id} not found.")
    return _version(dict(row))


def _version(row):
    row["definition"] = json.loads(row.pop("definition_json"))
    return row


def export_workflow(db, name):
    """JSON text of the draft (or the latest version), for copying between systems."""
    w = get_workflow(db, name)
    definition = w["draft"] if w["draft"] is not None else latest_version(db, name)["definition"]
    return json.dumps(definition, indent=2, ensure_ascii=False, sort_keys=False)


def import_workflow(db, text, *, by, publish_now=False):
    """Load exported JSON as the draft; optionally publish it at once."""
    try:
        definition = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DefinitionError(f"Not valid JSON: {exc}") from exc
    problems = validate(definition)
    if problems:
        raise DefinitionError("The workflow file is not valid.", details=problems)
    save_draft(db, definition, by=by)
    if publish_now:
        return publish(db, definition["name"], by=by)
    return None


# --- who may start ------------------------------------------------------

def start_groups(db, name):
    row = db.one("SELECT start_groups FROM workflows WHERE name = ?", (name,))
    if not row:
        raise NotFound(f"Workflow '{name}' not found.")
    return json.loads(row["start_groups"] or "[]")


def set_start_groups(db, name, groups, *, by):
    """Set the AD groups whose members may start this workflow."""
    get_workflow(db, name)
    groups = sorted({g.strip() for g in groups if g and g.strip()}, key=str.lower)
    unknown = [g for g in groups if not masterdata.get_group(db, g)]
    if unknown:
        raise DefinitionError("Unknown group(s).", details=[f"group '{g}' does not exist" for g in unknown])
    with db.tx() as c:
        c.execute("UPDATE workflows SET start_groups = ?, updated_at = ? WHERE name = ?",
                  (json.dumps(groups), clock.now_iso(), name))
        audit.record(db, "workflow.start_groups_changed", actor=by, data={"workflow": name, "groups": groups})
    return groups


def can_start(db, account, name):
    """Admins may start every published workflow; starters if they are in one of its groups."""
    if not account or not account.get("active"):
        return False
    if account["role"] == "admin":
        return True
    if account["role"] != "starter" or not account.get("email"):
        return False
    email = account["email"].lower()
    if not masterdata.get_user(db, email):
        return False
    return any(email in masterdata.group_members(db, g) for g in start_groups(db, name))


def startable_workflows(db, account):
    rows = db.all("SELECT DISTINCT w.name FROM workflows w JOIN workflow_versions v ON v.workflow_id = w.id"
                  " WHERE w.active = 1 ORDER BY w.name")
    return [r["name"] for r in rows if can_start(db, account, r["name"])]
