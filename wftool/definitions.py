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

Sections group fields. A field belongs to a section through its "section" key:

    {"id": "laptop_issued", "type": "checkbox", "label": "Laptop"},
    {"id": "laptop", "type": "section", "label": "Laptop",
     "show_when": {"field": "laptop_issued", "op": "equals", "value": true}},
    {"id": "laptop_model", "type": "dropdown", "section": "laptop", "list": "laptop_models", "label": "Model"},
    {"id": "monitors", "type": "section", "label": "Monitor",
     "repeat": {"min": 1, "max": 6, "add_label": {"de": "Weiteren Monitor hinzufügen", "en": "Add another monitor"}}}

A hidden section's fields are neither required nor stored. A normal section's
fields are stored flat; a repeatable section is stored as a list of rows under
its own id: "monitors": [{"monitor_model": "...", "monitor_serial": "..."}].
A section belongs to the step that fills its fields; all its fields are filled
by the same step.

Problems are `Problem` objects: plain strings ("Field 'x': ...") that also
know which field or step they concern, so the builder can show them there.
"""

import re

from . import emails, masterdata

FIELD_TYPES = {
    "text", "longtext", "number", "date", "checkbox", "dropdown", "multichoice",
    "email", "person", "secret", "signature", "heading", "section",
}
LAYOUT_FIELD_TYPES = {"heading", "section"}
VALUE_FIELD_TYPES = FIELD_TYPES - LAYOUT_FIELD_TYPES
LIST_FIELD_TYPES = {"dropdown", "multichoice"}
RECIPIENT_FIELD_TYPES = {"person", "email"}
CONDITION_FIELD_TYPES = VALUE_FIELD_TYPES - {"signature"}

STEP_TYPES = {"fill", "approve", "fill_approve", "approve_per_item", "sign", "confirm", "notify"}
APPROVAL_STEP_TYPES = {"approve", "fill_approve", "approve_per_item"}
FILL_STEP_TYPES = {"fill", "fill_approve"}
GROUP_ALLOWED_STEP_TYPES = {"notify", "confirm"}

WHO_KINDS = {"person", "group", "field", "starter", "manager_of", "item_approver"}
CONDITION_OPS = {"equals", "not_equals", "contains", "not_contains", "is_set", "is_empty", "any_needs_approval"}
VALUE_OPS = {"equals", "not_equals", "contains", "not_contains"}
ON_REJECT = {"starter", "previous"}
ON_TIMEOUT = {"wait", "reject"}
TIMEOUT_REJECT_STEP_TYPES = {"approve", "fill_approve", "approve_per_item", "sign"}
REPEAT_MAX = 50

_ID = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Problem(str):
    """One problem, readable as a plain string, located at a field, a step or the workflow.

    `template` and `args` let the interface translate the message; `kind` is
    "field", "step" or "workflow", `index` the position in the fields or steps
    list and `ref` the id there (which may itself be the problem).
    """

    def __new__(cls, kind, index, ref, template, **args):
        text = template.format(**args)
        prefix = {"field": "Field", "step": "Step"}.get(kind)
        if prefix:
            label = f"'{ref}'" if isinstance(ref, str) and _ID.match(ref) else str((index or 0) + 1)
            text = f"{prefix} {label}: {text}"
        obj = super().__new__(cls, text)
        obj.kind, obj.index, obj.ref, obj.template, obj.args = kind, index, ref, template, args
        return obj

    def as_dict(self):
        return {"kind": self.kind, "index": self.index, "ref": self.ref, "template": self.template,
                "args": self.args, "text": str(self)}


def label_text(label, lang="de"):
    """A label is a string or {"de": ..., "en": ...}; return the best text for `lang`."""
    if isinstance(label, dict):
        return label.get(lang) or label.get("de") or label.get("en") or ""
    return label or ""


def _label_ok(label):
    return bool(str(label_text(label, "de")).strip() or str(label_text(label, "en")).strip())


def fields_by_id(definition):
    return {f["id"]: f for f in definition.get("fields", []) if isinstance(f, dict) and "id" in f}


def section_members(definition, section_id):
    return [f for f in definition.get("fields", []) if isinstance(f, dict) and f.get("section") == section_id]


def repeat_sections(definition):
    """Ids of the repeatable sections."""
    return {f["id"] for f in definition.get("fields", [])
            if isinstance(f, dict) and f.get("type") == "section" and f.get("repeat")}


def in_repeat_section(definition, field_id):
    f = fields_by_id(definition).get(field_id)
    return bool(f and f.get("section") in repeat_sections(definition))


def field_owner(definition):
    """Map field id -> index of the step that fills it (None = filled when starting).

    A section belongs to the step of its fields (the first one decides).
    """
    fields = [f for f in definition.get("fields", []) if isinstance(f, dict) and "id" in f]
    owner = {f["id"]: None for f in fields}
    for index, step in enumerate(definition.get("steps", []) or []):
        if not isinstance(step, dict):
            continue
        for fid in step.get("fields", []) or []:
            if fid in owner:
                owner[fid] = index
    for f in fields:
        if f.get("type") == "section":
            members = [m["id"] for m in fields if m.get("section") == f["id"] and m.get("type") != "heading"]
            owner[f["id"]] = owner[members[0]] if members else None
    return owner


def start_fields(definition):
    """Fields filled when starting a request, with their sections, in form order."""
    owner = field_owner(definition)
    return [f for f in definition.get("fields", []) if owner.get(f["id"]) is None and f["type"] != "heading"]


def step_fields(definition, index):
    """Fields a step fills, with their sections, in form order."""
    owned = set(definition["steps"][index].get("fields", []) or [])
    out = []
    for f in definition.get("fields", []):
        if f["id"] in owned:
            out.append(f)
        elif f["type"] == "section" and any(m["id"] in owned for m in section_members(definition, f["id"])):
            out.append(f)
    return out


def who_list(step):
    who = step.get("who")
    if who is None:
        return []
    return who if isinstance(who, list) else [who]


def validate(definition):
    """Return a list of problems (Problem strings). An empty list means the structure is valid."""
    if not isinstance(definition, dict):
        return [Problem("workflow", None, None, "The definition must be an object.")]
    p = []
    if not str(definition.get("name", "")).strip():
        p.append(Problem("workflow", None, None, "The workflow needs a name."))

    fields = definition.get("fields")
    steps = definition.get("steps")
    if not isinstance(fields, list):
        p.append(Problem("workflow", None, None, "'fields' must be a list."))
        fields = []
    if not isinstance(steps, list) or not steps:
        p.append(Problem("workflow", None, None, "The workflow needs at least one step."))
        steps = steps if isinstance(steps, list) else []

    by_id = {}
    index_of = {}
    for i, f in enumerate(fields):
        if isinstance(f, dict) and isinstance(f.get("id"), str) and f["id"] not in by_id:
            by_id[f["id"]] = f
            index_of[f["id"]] = i
    repeats = {fid for fid, f in by_id.items() if f.get("type") == "section" and f.get("repeat")}

    def fp(fid_or_index, template, **args):
        i = fid_or_index if isinstance(fid_or_index, int) else index_of.get(fid_or_index)
        ref = fields[i].get("id") if i is not None and isinstance(fields[i], dict) else fid_or_index
        return Problem("field", i, ref, template, **args)

    # Field ownership (which step fills which field)
    owned = {}
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        for fid in step.get("fields", []) or []:
            if fid not in by_id:
                p.append(Problem("step", index, step.get("id"), "Field '{field}' does not exist.", field=fid))
            elif fid in owned:
                p.append(fp(fid, "This field is filled by two steps."))
            elif by_id[fid].get("type") in LAYOUT_FIELD_TYPES:
                p.append(Problem("step", index, step.get("id"),
                                 "'{field}' is a heading or section; a step fills the fields inside it.", field=fid))
            else:
                owned[fid] = index
    owner = {fid: owned.get(fid) for fid in by_id}
    for fid, f in by_id.items():
        if f.get("type") == "section":
            members = [m for m in by_id.values() if m.get("section") == fid and m.get("type") != "heading"]
            owner[fid] = owned.get(members[0]["id"]) if members else None

    # Fields
    seen = set()
    for i, f in enumerate(fields):
        if not isinstance(f, dict):
            p.append(Problem("field", i, None, "must be an object."))
            continue
        fid = f.get("id", "")
        if not isinstance(fid, str) or not _ID.match(fid):
            p.append(Problem("field", i, fid, "The id '{id}' must start with a letter and use a-z, 0-9 or _ "
                                              "(max 40).", id=fid))
        elif fid in seen:
            p.append(Problem("field", i, fid, "The id '{id}' is used twice.", id=fid))
        seen.add(fid)
        ftype = f.get("type")
        if ftype not in FIELD_TYPES:
            p.append(Problem("field", i, fid, "unknown type '{type}'.", type=ftype))
            continue
        if not _label_ok(f.get("label")):
            p.append(Problem("field", i, fid, "The field needs a label."))
        if ftype in LIST_FIELD_TYPES:
            has_list = bool(f.get("list"))
            options = f.get("options")
            if has_list == bool(options):
                p.append(Problem("field", i, fid, "Choose a list or enter fixed options (one of the two)."))
            if options is not None and not (isinstance(options, list) and all(
                    isinstance(o, dict) and str(o.get("value", "")).strip() for o in options)):
                p.append(Problem("field", i, fid, "Every option needs a value."))
            elif isinstance(options, list):
                values = [str(o.get("value")).strip() for o in options]
                if len(values) != len(set(values)):
                    p.append(Problem("field", i, fid, "Two options have the same value."))
        elif f.get("list") or f.get("options"):
            p.append(Problem("field", i, fid, "Only dropdown and multiple choice fields can have a list."))
        if ftype in LAYOUT_FIELD_TYPES and f.get("required"):
            p.append(Problem("field", i, fid, "A heading or section cannot be required."))
        if ftype == "number":
            p.extend(_check_min_max(i, fid, f))
        if f.get("max_length") not in (None, ""):
            if ftype not in ("text", "longtext", "secret"):
                p.append(Problem("field", i, fid, "Only text fields have a maximum length."))
            elif not _positive_int(f["max_length"]):
                p.append(Problem("field", i, fid, "The maximum length must be a whole number above 0."))

        sid = f.get("section")
        if sid not in (None, ""):
            sec = by_id.get(sid)
            if not sec or sec.get("type") != "section":
                p.append(Problem("field", i, fid, "The section '{section}' does not exist.", section=sid))
            elif ftype == "section":
                p.append(Problem("field", i, fid, "A section cannot be inside another section."))
            elif ftype == "signature":
                p.append(Problem("field", i, fid, "A signature field cannot be inside a section."))
            elif ftype != "heading" and owner.get(fid) != owner.get(sid):
                p.append(Problem("field", i, fid, "All fields of section '{section}' must be filled by the "
                                                  "same step.", section=sid))

        if ftype == "section":
            members = [m for m in by_id.values() if m.get("section") == fid and m.get("type") != "heading"]
            if not members:
                p.append(Problem("field", i, fid, "The section has no fields yet."))
            if f.get("show_when") not in (None, {}):
                p.extend(_check_condition(fp, i, f["show_when"], by_id, owner, repeats, owner.get(fid),
                                          for_section=True))
            if f.get("repeat") not in (None, False, {}):
                p.extend(_check_repeat(i, fid, f["repeat"]))
        elif f.get("show_when") or f.get("repeat"):
            p.append(Problem("field", i, fid, "Only a section can be shown on a condition or repeated."))

    for fid, f in by_id.items():
        if f.get("type") == "signature":
            idx = owned.get(fid)
            if idx is None or not isinstance(steps[idx], dict) or steps[idx].get("type") != "sign":
                p.append(fp(fid, "A signature field must belong to a sign step."))

    # Steps
    step_ids = set()
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            p.append(Problem("step", index, None, "must be an object."))
            continue
        sid = step.get("id", "")

        def sp(template, **args):
            return Problem("step", index, sid, template, **args)

        if not isinstance(sid, str) or not _ID.match(sid):
            p.append(sp("The id '{id}' must start with a letter and use a-z, 0-9 or _ (max 40).", id=sid))
        elif sid in step_ids:
            p.append(sp("The id '{id}' is used twice.", id=sid))
        step_ids.add(sid)
        stype = step.get("type")
        if stype not in STEP_TYPES:
            p.append(sp("unknown type '{type}'.", type=stype))
            continue

        available = {fid for fid, o in owner.items() if o is None or o < index}
        own_fields = [fid for fid in step.get("fields", []) or [] if fid in by_id]

        if stype in FILL_STEP_TYPES and not own_fields:
            p.append(sp("This step has no fields to fill. In the form, set 'Filled by' of a field to this step."))
        if stype not in FILL_STEP_TYPES and stype != "sign" and own_fields:
            p.append(sp("A {type} step cannot fill fields.", type=stype))
        if stype == "sign" and any(by_id[fid].get("type") != "signature" for fid in own_fields):
            p.append(sp("A sign step can only hold signature fields."))

        whos = who_list(step)
        if not whos:
            p.append(sp("Choose who receives this step."))
        if isinstance(step.get("who"), list) and stype != "notify" and len(whos) != 1:
            p.append(sp("Only a notify step can have several recipients."))
        for who in whos:
            p.extend(_check_who(sp, stype, who, by_id, available, repeats))

        if stype == "approve_per_item":
            items = step.get("items")
            f = by_id.get(items)
            if not f:
                p.append(sp("Choose the field whose items are approved."))
            elif f.get("type") not in LIST_FIELD_TYPES or not f.get("list"):
                p.append(sp("'{field}' must be a dropdown or multiple choice field with a list.", field=items))
            elif f.get("section") in repeats:
                p.append(sp("'{field}' is inside a repeatable section and cannot be used here.", field=items))
            elif items not in available:
                p.append(sp("The field '{field}' is filled only at or after this step.", field=items))
            if not any(isinstance(w, dict) and w.get("kind") == "item_approver" for w in whos):
                p.append(sp("Approve per item goes to the approver of each item."))

        if step.get("on_reject", "starter") not in ON_REJECT:
            p.append(sp("on_reject must be 'starter' or 'previous'."))
        timeout = step.get("on_timeout", "wait")
        if timeout not in ON_TIMEOUT:
            p.append(sp("on_timeout must be 'wait' or 'reject'."))
        elif timeout == "reject" and stype not in TIMEOUT_REJECT_STEP_TYPES:
            p.append(sp("Only approval and sign steps can be rejected automatically."))
        if step.get("on_reject") == "previous" and index == 0:
            p.append(sp("The first step cannot send a rejection back one step."))
        if step.get("on_reject") == "previous" and stype not in TIMEOUT_REJECT_STEP_TYPES:
            p.append(sp("Only approval and sign steps can be rejected."))

        cond = step.get("when")
        if cond is not None:
            p.extend(_check_condition(lambda _i, t, **a: sp(t, **a), index, cond, by_id, owner, repeats, index))

        if not _label_ok(step.get("label", sid)):
            p.append(sp("The step needs a label."))
        p.extend(_check_email(sp, step, stype, by_id, repeats))

    return p


def _positive_int(value):
    try:
        return int(value) >= 1 and float(value) == int(value)
    except (TypeError, ValueError):
        return False


def _number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return "bad"


def _check_min_max(i, fid, f):
    low, high = _number(f.get("min")), _number(f.get("max"))
    if "bad" in (low, high):
        return [Problem("field", i, fid, "Minimum and maximum must be numbers.")]
    if low is not None and high is not None and low > high:
        return [Problem("field", i, fid, "The minimum is larger than the maximum.")]
    return []


def _check_repeat(i, fid, repeat):
    if not isinstance(repeat, dict):
        return [Problem("field", i, fid, "The repeat setting must be an object with min and max.")]
    p = []
    low, high = repeat.get("min", 0), repeat.get("max", REPEAT_MAX)
    try:
        low, high = int(low if low not in (None, "") else 0), int(high if high not in (None, "") else REPEAT_MAX)
    except (TypeError, ValueError):
        return [Problem("field", i, fid, "How often a section repeats must be a whole number.")]
    if low < 0 or high < 1 or high > REPEAT_MAX:
        p.append(Problem("field", i, fid, "A section repeats at least 0 and at most {max} times.", max=REPEAT_MAX))
    elif low > high:
        p.append(Problem("field", i, fid, "The minimum count is larger than the maximum count."))
    return p


def _check_who(sp, stype, who, by_id, available, repeats):
    if not isinstance(who, dict) or who.get("kind") not in WHO_KINDS:
        return [sp("Recipient kind must be one of {kinds}.", kinds=", ".join(sorted(WHO_KINDS)))]
    p = []
    kind = who["kind"]
    if kind == "person":
        if not _EMAIL.match(str(who.get("email", ""))):
            p.append(sp("The recipient needs a valid email address."))
    elif kind == "group":
        if stype not in GROUP_ALLOWED_STEP_TYPES:
            p.append(sp("Approvals and signatures go to one person, never a group."))
        if not str(who.get("group", "")).strip():
            p.append(sp("Choose a group."))
    elif kind in ("field", "manager_of"):
        fid = who.get("field")
        if kind == "manager_of" and fid in (None, ""):
            return p  # manager of the starter
        f = by_id.get(fid)
        if not f:
            p.append(sp("The recipient field '{field}' does not exist.", field=fid))
        elif kind == "field" and f.get("type") not in RECIPIENT_FIELD_TYPES:
            p.append(sp("The recipient field '{field}' must be a person or email field.", field=fid))
        elif kind == "manager_of" and f.get("type") != "person":
            p.append(sp("'Manager of' needs a person field."))
        elif f.get("section") in repeats:
            p.append(sp("'{field}' is inside a repeatable section and cannot be used here.", field=fid))
        elif fid not in available:
            p.append(sp("The recipient field '{field}' is filled only at or after this step.", field=fid))
    elif kind == "item_approver" and stype != "approve_per_item":
        p.append(sp("'Approver of the item' only works in an approve per item step."))
    return p


def _check_condition(make, index, cond, by_id, owner, repeats, at, *, for_section=False):
    """Check a step's `when` or a section's `show_when`. `at` is the step index it runs at."""
    if not isinstance(cond, dict):
        return [make(index, "The condition must be an object.")]
    p = []
    op = cond.get("op")
    fid = cond.get("field")
    if op not in CONDITION_OPS:
        p.append(make(index, "Unknown condition '{op}'.", op=op))
    f = by_id.get(fid)
    filled_by = owner.get(fid, None)
    in_time = filled_by is None or (at is not None and (filled_by < at if not for_section else filled_by <= at))
    if not f:
        p.append(make(index, "The condition field '{field}' does not exist.", field=fid))
    elif f.get("type") not in CONDITION_FIELD_TYPES:
        p.append(make(index, "A condition cannot use the heading, section or signature '{field}'.", field=fid))
    elif for_section and f.get("section"):
        p.append(make(index, "A section can only depend on a field outside sections."))
    elif f.get("section") in repeats:
        p.append(make(index, "'{field}' is inside a repeatable section and cannot be used here.", field=fid))
    elif not in_time:
        p.append(make(index, "The condition field '{field}' is filled only at or after this step.", field=fid))
    elif op == "any_needs_approval" and not (f.get("type") in LIST_FIELD_TYPES and f.get("list")):
        p.append(make(index, "'Needs approval' only works on a field with a list."))
    if op in VALUE_OPS and "value" not in cond:
        p.append(make(index, "The condition '{op}' needs a value.", op=op))
    return p


def _check_email(sp, step, stype, by_id, repeats):
    email = step.get("email")
    if email in (None, {}):
        return []
    if not isinstance(email, dict):
        return [sp("The email text must be an object with subject and body.")]
    p = []
    used = []
    for part in ("subject", "body"):
        value = email.get(part)
        texts = value.values() if isinstance(value, dict) else [value]
        for text in texts:
            if text is not None and not isinstance(text, str):
                p.append(sp("The email {part} must be text.", part=part))
                continue
            used.extend(emails.placeholders_in(text))
    for name in dict.fromkeys(used):
        f = by_id.get(name)
        if name in emails.PLACEHOLDERS:
            if name == "link" and stype == "notify":
                p.append(sp("A notify email has no link; remove {{link}}."))
        elif not f or f.get("type") in LAYOUT_FIELD_TYPES or f.get("type") == "signature":
            p.append(sp("Unknown placeholder {{{name}}} in the email text.", name=name))
        elif f.get("type") == "secret":
            p.append(sp("The secret field {{{name}}} must not appear in an email.", name=name))
        elif f.get("section") in repeats:
            p.append(sp("{{{name}}} is inside a repeatable section and cannot appear in an email.", name=name))
    return p


def publish_problems(db, definition):
    """Structure problems plus master-data problems; empty list = ready to publish.

    The master-data checks run even when the structure has problems, wherever
    the part they look at is complete enough, so the builder can show every
    problem at once.
    """
    p = validate(definition)
    if not isinstance(definition, dict):
        return p
    fields = definition.get("fields") if isinstance(definition.get("fields"), list) else []
    steps = definition.get("steps") if isinstance(definition.get("steps"), list) else []
    by_id = {f["id"]: f for f in fields if isinstance(f, dict) and isinstance(f.get("id"), str)}

    for index, f in enumerate(fields):
        if isinstance(f, dict) and f.get("list") and isinstance(f["list"], str) \
                and not masterdata.get_list(db, f["list"]):
            p.append(Problem("field", index, f.get("id"), "The list '{list}' does not exist.", list=f["list"]))

    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue

        def sp(template, **args):
            return Problem("step", index, step.get("id"), template, **args)

        for who in who_list(step):
            if not isinstance(who, dict):
                continue
            if who.get("kind") == "person" and _EMAIL.match(str(who.get("email", ""))):
                if not masterdata.get_user(db, who["email"]):
                    p.append(sp("{email} is not an active AD user.", email=who["email"]))
            elif who.get("kind") == "group" and str(who.get("group", "")).strip():
                if not masterdata.get_group(db, who["group"]):
                    p.append(sp("The group '{group}' does not exist.", group=who["group"]))
                elif not masterdata.group_members(db, who["group"]):
                    p.append(sp("The group '{group}' has no active members.", group=who["group"]))
        items_field = by_id.get(step.get("items")) if isinstance(step.get("items"), str) else None
        if step.get("type") == "approve_per_item" and items_field and isinstance(items_field.get("list"), str):
            for item in masterdata.list_items(db, items_field["list"]):
                if not item["needs_approval"]:
                    continue
                if not item["approver_email"]:
                    p.append(sp("'{item}' needs approval but has no approver.", item=item["label"]))
                elif not masterdata.get_user(db, item["approver_email"]):
                    p.append(sp("The approver {email} of '{item}' is not an active AD user.",
                                email=item["approver_email"], item=item["label"]))
    return p
