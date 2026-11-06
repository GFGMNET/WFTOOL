"""Workflow definitions: the form and the ordered steps the builder produces.

A definition is plain JSON. `validate()` checks its structure without the
database; `publish_problems()` adds the checks that need master data
(design: "Publishing is checked first").

Example (shortened):

    {
      "name": "HR Onboard",
      "fields": [
        {"id": "employee", "type": "text", "label": {"de": "Mitarbeiter", "en": "Employee"}, "required": true},
        {"id": "manager", "type": "person", "label": "Manager", "required": true},
        {"id": "software", "type": "multichoice", "label": "Software", "list": "software"}
      ],
      "steps": [
        {"id": "mgr", "type": "fill_approve", "who": {"kind": "field", "field": "manager"}, "fields": ["software"]},
        {"id": "sw", "type": "approve_per_item", "items": "software", "who": {"kind": "item_approver"},
         "when": {"field": "software", "op": "any_needs_approval"}},
        {"id": "to_hr", "type": "notify", "who": [{"kind": "group", "group": "HR"}], "attach_pdf": true}
      ]
    }
"""

import re

from . import masterdata

FIELD_TYPES = {
    "text", "longtext", "number", "date", "checkbox", "dropdown", "multichoice",
    "email", "person", "secret", "signature", "heading",
}
VALUE_FIELD_TYPES = FIELD_TYPES - {"heading"}
LIST_FIELD_TYPES = {"dropdown", "multichoice"}
RECIPIENT_FIELD_TYPES = {"person", "email"}

STEP_TYPES = {"fill", "approve", "fill_approve", "approve_per_item", "sign", "confirm", "notify"}
APPROVAL_STEP_TYPES = {"approve", "fill_approve", "approve_per_item"}
FILL_STEP_TYPES = {"fill", "fill_approve"}
GROUP_ALLOWED_STEP_TYPES = {"notify", "confirm"}

WHO_KINDS = {"person", "group", "field", "starter", "manager_of", "item_approver"}
CONDITION_OPS = {"equals", "not_equals", "contains", "not_contains", "is_set", "is_empty", "any_needs_approval"}
ON_REJECT = {"starter", "previous"}
ON_TIMEOUT = {"wait", "reject"}
TIMEOUT_REJECT_STEP_TYPES = {"approve", "fill_approve", "approve_per_item", "sign"}

_ID = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def label_text(label, lang="de"):
    """A label is a string or {"de": ..., "en": ...}; return the best text for `lang`."""
    if isinstance(label, dict):
        return label.get(lang) or label.get("de") or label.get("en") or ""
    return label or ""


def _label_ok(label):
    return bool(label_text(label, "de").strip() or label_text(label, "en").strip())


def fields_by_id(definition):
    return {f["id"]: f for f in definition.get("fields", []) if isinstance(f, dict) and "id" in f}


def field_owner(definition):
    """Map field id -> index of the step that fills it (None = filled when starting)."""
    owner = {f["id"]: None for f in definition.get("fields", []) if isinstance(f, dict) and "id" in f}
    for index, step in enumerate(definition.get("steps", [])):
        for fid in step.get("fields", []) or []:
            if fid in owner:
                owner[fid] = index
    return owner


def start_fields(definition):
    owner = field_owner(definition)
    return [f for f in definition.get("fields", []) if owner.get(f["id"]) is None and f["type"] != "heading"]


def step_fields(definition, index):
    fields = fields_by_id(definition)
    return [fields[fid] for fid in definition["steps"][index].get("fields", []) or [] if fid in fields]


def who_list(step):
    who = step.get("who")
    if who is None:
        return []
    return who if isinstance(who, list) else [who]


def validate(definition):
    """Return a list of problems (strings). An empty list means the structure is valid."""
    p = []
    if not isinstance(definition, dict):
        return ["The definition must be an object."]
    if not str(definition.get("name", "")).strip():
        p.append("The workflow needs a name.")

    fields = definition.get("fields")
    steps = definition.get("steps")
    if not isinstance(fields, list):
        p.append("'fields' must be a list.")
        fields = []
    if not isinstance(steps, list) or not steps:
        p.append("The workflow needs at least one step.")
        steps = steps if isinstance(steps, list) else []

    # Fields
    seen = set()
    for i, f in enumerate(fields):
        where = f"Field {i + 1}"
        if not isinstance(f, dict):
            p.append(f"{where}: must be an object.")
            continue
        fid = f.get("id", "")
        if not isinstance(fid, str) or not _ID.match(fid):
            p.append(f"{where}: id '{fid}' must start with a letter and use a-z, 0-9 or _ (max 40).")
        elif fid in seen:
            p.append(f"{where}: id '{fid}' is used twice.")
        seen.add(fid)
        where = f"Field '{fid}'"
        ftype = f.get("type")
        if ftype not in FIELD_TYPES:
            p.append(f"{where}: unknown type '{ftype}'.")
            continue
        if not _label_ok(f.get("label")):
            p.append(f"{where}: needs a label.")
        if ftype in LIST_FIELD_TYPES:
            has_list = bool(f.get("list"))
            options = f.get("options")
            if has_list == bool(options):
                p.append(f"{where}: needs either a list or fixed options, not both.")
            if options is not None and not (isinstance(options, list) and all(
                    isinstance(o, dict) and str(o.get("value", "")).strip() for o in options)):
                p.append(f"{where}: options must be a list of {{value, label}}.")
        elif f.get("list") or f.get("options"):
            p.append(f"{where}: only dropdown and multiple choice fields can have a list.")
        if ftype == "heading" and f.get("required"):
            p.append(f"{where}: a section heading cannot be required.")

    by_id = fields_by_id(definition)

    # Field ownership
    owned = {}
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        for fid in step.get("fields", []) or []:
            if fid not in by_id:
                p.append(f"Step '{step.get('id')}': field '{fid}' does not exist.")
            elif fid in owned:
                p.append(f"Field '{fid}' is filled by two steps.")
            owned[fid] = index
    for fid, f in by_id.items():
        if f.get("type") == "signature":
            idx = owned.get(fid)
            if idx is None or steps[idx].get("type") != "sign":
                p.append(f"Field '{fid}': a signature field must belong to a sign step.")

    # Steps
    step_ids = set()
    for index, step in enumerate(steps):
        where = f"Step {index + 1}"
        if not isinstance(step, dict):
            p.append(f"{where}: must be an object.")
            continue
        sid = step.get("id", "")
        if not isinstance(sid, str) or not _ID.match(sid):
            p.append(f"{where}: id '{sid}' must start with a letter and use a-z, 0-9 or _ (max 40).")
        elif sid in step_ids:
            p.append(f"{where}: id '{sid}' is used twice.")
        step_ids.add(sid)
        where = f"Step '{sid}'"
        stype = step.get("type")
        if stype not in STEP_TYPES:
            p.append(f"{where}: unknown type '{stype}'.")
            continue

        available = {fid for fid, owner in _owner_map(by_id, owned).items()
                     if owner is None or owner < index}
        own_fields = step.get("fields", []) or []

        if stype in FILL_STEP_TYPES and not own_fields:
            p.append(f"{where}: a {stype} step needs at least one field to fill.")
        if stype not in FILL_STEP_TYPES and stype != "sign" and own_fields:
            p.append(f"{where}: a {stype} step cannot fill fields.")
        if stype == "sign" and any(by_id.get(fid, {}).get("type") != "signature" for fid in own_fields):
            p.append(f"{where}: a sign step can only hold signature fields.")

        whos = who_list(step)
        if not whos:
            p.append(f"{where}: needs a recipient ('who').")
        if isinstance(step.get("who"), list) and stype != "notify" and len(whos) != 1:
            p.append(f"{where}: only a notify step can have several recipients.")
        for who in whos:
            p.extend(_check_who(where, stype, who, by_id, available))

        if stype == "approve_per_item":
            items = step.get("items")
            f = by_id.get(items)
            if not f:
                p.append(f"{where}: 'items' must name a field.")
            elif f["type"] not in LIST_FIELD_TYPES or not f.get("list"):
                p.append(f"{where}: 'items' must be a dropdown or multiple choice field with a list.")
            elif items not in available:
                p.append(f"{where}: field '{items}' is filled only at or after this step.")
            if not any(isinstance(w, dict) and w.get("kind") == "item_approver" for w in whos):
                p.append(f"{where}: approve per item goes to the approver of each item.")

        if step.get("on_reject", "starter") not in ON_REJECT:
            p.append(f"{where}: on_reject must be 'starter' or 'previous'.")
        timeout = step.get("on_timeout", "wait")
        if timeout not in ON_TIMEOUT:
            p.append(f"{where}: on_timeout must be 'wait' or 'reject'.")
        elif timeout == "reject" and stype not in TIMEOUT_REJECT_STEP_TYPES:
            p.append(f"{where}: only approval and sign steps can be rejected automatically.")
        if step.get("on_reject") == "previous" and index == 0:
            p.append(f"{where}: the first step cannot send a rejection back one step.")

        cond = step.get("when")
        if cond is not None:
            p.extend(_check_condition(where, cond, by_id, available))

        if not _label_ok(step.get("label", sid)):
            p.append(f"{where}: needs a label.")

    return p


def _owner_map(by_id, owned):
    return {fid: owned.get(fid) for fid in by_id}


def _check_who(where, stype, who, by_id, available):
    p = []
    if not isinstance(who, dict) or who.get("kind") not in WHO_KINDS:
        return [f"{where}: recipient kind must be one of {', '.join(sorted(WHO_KINDS))}."]
    kind = who["kind"]
    if kind == "person":
        if not _EMAIL.match(str(who.get("email", ""))):
            p.append(f"{where}: recipient needs a valid email address.")
    elif kind == "group":
        if stype not in GROUP_ALLOWED_STEP_TYPES:
            p.append(f"{where}: approvals and signatures go to one person, never a group.")
        if not str(who.get("group", "")).strip():
            p.append(f"{where}: recipient needs a group name.")
    elif kind in ("field", "manager_of"):
        fid = who.get("field")
        if kind == "manager_of" and fid is None:
            return p  # manager of the starter
        f = by_id.get(fid)
        if not f:
            p.append(f"{where}: recipient field '{fid}' does not exist.")
        elif kind == "field" and f["type"] not in RECIPIENT_FIELD_TYPES:
            p.append(f"{where}: recipient field '{fid}' must be a person or email field.")
        elif kind == "manager_of" and f["type"] != "person":
            p.append(f"{where}: 'manager of' needs a person field.")
        elif fid not in available:
            p.append(f"{where}: recipient field '{fid}' is filled only at or after this step.")
    elif kind == "item_approver" and stype != "approve_per_item":
        p.append(f"{where}: 'approver of the item' only works in an approve per item step.")
    return p


def _check_condition(where, cond, by_id, available):
    if not isinstance(cond, dict):
        return [f"{where}: condition must be an object."]
    p = []
    op = cond.get("op")
    fid = cond.get("field")
    if op not in CONDITION_OPS:
        p.append(f"{where}: unknown condition '{op}'.")
    f = by_id.get(fid)
    if not f:
        p.append(f"{where}: condition field '{fid}' does not exist.")
    elif fid not in available:
        p.append(f"{where}: condition field '{fid}' is filled only at or after this step.")
    elif op == "any_needs_approval" and not (f["type"] in LIST_FIELD_TYPES and f.get("list")):
        p.append(f"{where}: 'needs approval' only works on a field with a list.")
    if op in ("equals", "not_equals", "contains", "not_contains") and "value" not in cond:
        p.append(f"{where}: condition '{op}' needs a value.")
    return p


def publish_problems(db, definition):
    """Structure problems plus master-data problems; empty list = ready to publish."""
    p = validate(definition)
    if p:
        return p
    by_id = fields_by_id(definition)

    for f in by_id.values():
        if f.get("list") and not masterdata.get_list(db, f["list"]):
            p.append(f"Field '{f['id']}': list '{f['list']}' does not exist.")

    for step in definition["steps"]:
        where = f"Step '{step['id']}'"
        for who in who_list(step):
            if who["kind"] == "person":
                if not masterdata.get_user(db, who["email"]):
                    p.append(f"{where}: {who['email']} is not an active AD user.")
            elif who["kind"] == "group":
                if not masterdata.get_group(db, who["group"]):
                    p.append(f"{where}: group '{who['group']}' does not exist.")
                elif not masterdata.group_members(db, who["group"]):
                    p.append(f"{where}: group '{who['group']}' has no active members.")
        if step["type"] == "approve_per_item":
            list_name = by_id[step["items"]]["list"]
            for item in masterdata.list_items(db, list_name):
                if not item["needs_approval"]:
                    continue
                if not item["approver_email"]:
                    p.append(f"{where}: '{item['label']}' needs approval but has no approver.")
                elif not masterdata.get_user(db, item["approver_email"]):
                    p.append(f"{where}: approver {item['approver_email']} of '{item['label']}'"
                             " is not an active AD user.")
    return p
