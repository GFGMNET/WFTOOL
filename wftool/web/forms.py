"""Request forms in the browser: what the templates need, and reading the post back.

Input names: `f.<field id>` for normal fields (also inside normal sections) and
`r.<section id>.<row>.<field id>` for rows of repeatable sections. The engine's
error keys ("laptop_model", "monitors[2].monitor_serial", "monitors") are used
as they are to show each message next to its input.
"""

import re

from .. import masterdata
from ..definitions import label_text

_ROW = re.compile(r"^r\.([a-z][a-z0-9_]*)\.(\d{1,6})\.([a-z][a-z0-9_]*)$")


def choices(db, field, lang):
    if field.get("options"):
        return [(str(o["value"]), label_text(o.get("label"), lang) or str(o["value"])) for o in field["options"]]
    return [(i["value"], i["label"]) for i in masterdata.list_items(db, field.get("list") or "")]


def layout(db, fields, lang, *, known=None, condition=None):
    """`fields` (from start_fields / step_fields) grouped for the template:
    [{"field": f} | {"section": s, "fields": [...], "repeat": bool}] with choices resolved.

    A section whose condition uses a field that is not on this page (filled at an
    earlier step) is decided here with `condition(cond, known)`: "fixed" is then
    True (shown) or False (hidden), and the browser does not evaluate it.
    """
    on_page = {f["id"] for f in fields}
    out = []
    by_section = {}
    for f in fields:
        item = {"field": f, "label": label_text(f.get("label"), lang), "help": label_text(f.get("help"), lang),
                "choices": choices(db, f, lang) if f["type"] in ("dropdown", "multichoice") else []}
        if f["type"] == "section":
            block = {"section": f, "label": item["label"], "fields": [], "repeat": f.get("repeat") or None,
                     "add_label": label_text((f.get("repeat") or {}).get("add_label"), lang)}
            cond = f.get("show_when")
            block["fixed"] = None
            if cond and cond.get("field") not in on_page and condition is not None:
                block["fixed"] = bool(condition(cond, known or {}))
            by_section[f["id"]] = block
            out.append(block)
        elif f.get("section") in by_section:
            by_section[f["section"]]["fields"].append(item)
        elif f.get("section"):
            continue  # its section is filled by another step
        else:
            out.append(item)
    return out


def parse(fields, form):
    """Values for the engine from a posted form."""
    values = {}
    repeats = {f["id"] for f in fields if f["type"] == "section" and f.get("repeat")}
    for f in fields:
        if f["type"] in ("section", "heading", "signature") or f.get("section") in repeats:
            continue
        key = "f." + f["id"]
        if f["type"] == "multichoice":
            values[f["id"]] = form.getlist(key)
        elif f["type"] == "checkbox":
            values[f["id"]] = form.get(key, "")
        elif key in form:
            values[f["id"]] = form.get(key)
    rows = {}
    for key in form.keys():
        m = _ROW.match(key)
        if not m or m.group(1) not in repeats:
            continue
        sid, n, fid = m.group(1), int(m.group(2)), m.group(3)
        field = next((f for f in fields if f["id"] == fid and f.get("section") == sid), None)
        if field is None:
            continue
        row = rows.setdefault(sid, {}).setdefault(n, {})
        row[fid] = form.getlist(key) if field["type"] == "multichoice" else form.get(key)
    for sid in repeats:
        values[sid] = [rows.get(sid, {})[n] for n in sorted(rows.get(sid, {}))]
    return values


def repeat_rows(section, values):
    """Rows to show for a repeatable section: the posted or saved rows, else the minimum (at least one)."""
    rows = values.get(section["id"])
    if isinstance(rows, list) and rows:
        return rows
    return [{} for _ in range(max(int((section.get("repeat") or {}).get("min") or 0), 1))]


_MESSAGES_DE = [
    (r"^required$", "Pflichtfeld"),
    (r"^not a number$", "keine Zahl"),
    (r"^not a date \(YYYY-MM-DD\)$", "kein Datum"),
    (r"^not in the list: (.*)$", r"nicht in der Liste: \1"),
    (r"^choose one$", "bitte nur einen Eintrag wählen"),
    (r"^not an email address$", "keine E-Mail-Adresse"),
    (r"^not a company email address$", "keine E-Mail-Adresse der Firma"),
    (r"^not an active AD user$", "kein aktiver AD-Benutzer"),
    (r"^at most (\S+) characters$", r"höchstens \1 Zeichen"),
    (r"^at least (\S+)$", r"mindestens \1"),
    (r"^at most (\S+)$", r"höchstens \1"),
]


def errors_from(details, lang):
    """{"key": "message"} from a ValidationError's details ("key: message")."""
    out = {}
    for d in details:
        key, _, message = str(d).partition(": ")
        if lang == "de":
            for pattern, repl in _MESSAGES_DE:
                if re.match(pattern, message):
                    message = re.sub(pattern, repl, message)
                    break
        out[key] = message
    return out


def show_when_json(section):
    import json
    return json.dumps(section.get("show_when") or None)
