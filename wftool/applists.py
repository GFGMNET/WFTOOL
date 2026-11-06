"""Dropdown lists maintained in the app (hardware models, vendors, ...).

Imported lists (software) are read-only here. Entries are deactivated, never
deleted, because old requests and PDFs still refer to them. Renaming changes
only the label; the stored value in requests stays the same.
"""

import re

from . import audit, masterdata
from .errors import NotFound, StateError, ValidationError

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def all_lists(db):
    rows = db.all(
        "SELECT l.*, (SELECT COUNT(*) FROM list_items i WHERE i.list_name = l.name AND i.active = 1) AS active_items,"
        " (SELECT COUNT(*) FROM list_items i WHERE i.list_name = l.name) AS all_items FROM lists l ORDER BY l.label"
    )
    return [dict(r) for r in rows]


def _editable(db, list_name):
    lst = masterdata.get_list(db, list_name)
    if not lst:
        raise NotFound(f"List '{list_name}' not found.")
    if lst["source"] == "import":
        raise StateError(f"List '{lst['label']}' comes from the CSV import and cannot be edited here.")
    return lst


def create_list(db, name, label, *, by):
    name = (name or "").strip().lower()
    if not _NAME.match(name):
        raise ValidationError("The list id must start with a letter and use a-z, 0-9 or _.",
                              details=["name: invalid"])
    if not (label or "").strip():
        raise ValidationError("The list needs a name.", details=["label: required"])
    if masterdata.get_list(db, name):
        raise ValidationError("A list with this id already exists.", details=["name: taken"])
    with db.tx():
        masterdata.ensure_list(db, name, label.strip(), source="app")
        audit.record(db, "list.created", actor=by, data={"list": name, "label": label.strip()})
    return masterdata.get_list(db, name)


def add_item(db, list_name, label, *, by):
    _editable(db, list_name)
    label = (label or "").strip()
    if not label:
        raise ValidationError("The entry needs a text.", details=["label: required"])
    if masterdata.list_item(db, list_name, label):
        raise ValidationError("This entry already exists (it may be deactivated).", details=["label: taken"])
    sort = db.one("SELECT COALESCE(MAX(sort), -1) + 1 AS s FROM list_items WHERE list_name = ?", (list_name,))["s"]
    with db.tx():
        masterdata.upsert_list_item(db, list_name, label, label, sort=sort)
        audit.record(db, "list.item_added", actor=by, data={"list": list_name, "value": label})
    return masterdata.list_item(db, list_name, label)


def _item(db, item_id):
    row = db.one("SELECT * FROM list_items WHERE id = ?", (item_id,))
    if not row:
        raise NotFound(f"List entry {item_id} not found.")
    return dict(row)


def rename_item(db, item_id, label, *, by):
    item = _item(db, item_id)
    _editable(db, item["list_name"])
    label = (label or "").strip()
    if not label:
        raise ValidationError("The entry needs a text.", details=["label: required"])
    if label == item["label"]:
        return item
    with db.tx() as c:
        c.execute("UPDATE list_items SET label = ? WHERE id = ?", (label, item_id))
        audit.record(db, "list.item_renamed", actor=by,
                     data={"list": item["list_name"], "value": item["value"], "from": item["label"], "to": label})
    return _item(db, item_id)


def set_item_active(db, item_id, active, *, by):
    item = _item(db, item_id)
    _editable(db, item["list_name"])
    if bool(item["active"]) == bool(active):
        return item
    with db.tx() as c:
        c.execute("UPDATE list_items SET active = ? WHERE id = ?", (int(bool(active)), item_id))
        audit.record(db, "list.item_activated" if active else "list.item_deactivated", actor=by,
                     data={"list": item["list_name"], "value": item["value"]})
    return _item(db, item_id)


def move_item(db, item_id, direction, *, by):
    """Move an entry one place up (-1) or down (+1)."""
    item = _item(db, item_id)
    _editable(db, item["list_name"])
    items = masterdata.list_items(db, item["list_name"], active_only=False)
    ids = [i["id"] for i in items]
    pos = ids.index(item_id)
    new = pos + (1 if direction > 0 else -1)
    if not 0 <= new < len(ids):
        return item
    ids[pos], ids[new] = ids[new], ids[pos]
    with db.tx() as c:
        for sort, iid in enumerate(ids):
            c.execute("UPDATE list_items SET sort = ? WHERE id = ?", (sort, iid))
        audit.record(db, "list.item_moved", actor=by,
                     data={"list": item["list_name"], "value": item["value"], "to_position": new + 1})
    return _item(db, item_id)
