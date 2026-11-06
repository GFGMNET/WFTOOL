"""Form values as people read them: labels, list labels, names, dates, sections.

Used by the request pages, the emails and the PDFs, so all three show a request
the same way. Secret values are masked unless `show_secrets` is set.
"""

from datetime import date

from . import masterdata
from .definitions import label_text

MASK = "••••"


def _date(value, lang):
    try:
        d = date.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return d.strftime("%d.%m.%Y") if lang == "de" else d.isoformat()


def _choice_labels(db, field, values, lang):
    out = []
    for v in values:
        label = None
        if field.get("options"):
            opt = next((o for o in field["options"] if str(o.get("value")) == str(v)), None)
            label = label_text(opt.get("label"), lang) if opt else None
        elif field.get("list"):
            item = masterdata.list_item(db, field["list"], v)
            label = item["label"] if item else None
        out.append(label or str(v))
    return out


def value_text(db, field, value, lang="de", *, show_secrets=False):
    """One stored value as text."""
    t = field["type"]
    if value is None or value == "" or value == []:
        return "–"
    if t == "secret":
        return str(value) if show_secrets else MASK
    if t == "checkbox":
        return ("Ja" if lang == "de" else "Yes") if value is True else ("Nein" if lang == "de" else "No")
    if t == "date":
        return _date(value, lang)
    if t in ("dropdown", "multichoice"):
        values = value if isinstance(value, list) else [value]
        return ", ".join(_choice_labels(db, field, values, lang))
    if t == "person":
        user = masterdata.get_user(db, value, active_only=False)
        return f"{user['display_name']} <{value}>" if user else str(value)
    if t == "signature" and isinstance(value, dict):
        how = ("auf Papier" if lang == "de" else "on paper") if value.get("paper") else \
            ("am Bildschirm" if lang == "de" else "on screen")
        return f"{value.get('by', '')} · {local_time(value.get('at'), lang)} · {how}"
    if t == "number" and isinstance(value, float) and lang == "de":
        return str(value).replace(".", ",")
    return str(value)


def rows(db, definition, form, lang="de", *, show_secrets=False):
    """The filled-in form as a list of blocks:

    {"kind": "field", "label", "value", "secret"}, {"kind": "heading", "label"},
    {"kind": "section", "label", "items": [...]},
    {"kind": "repeat", "label", "rows": [[...], ...]}.
    Fields that were not filled in yet, and sections that are hidden, are left out.
    """
    fields = definition.get("fields", [])
    out = []
    for f in fields:
        if f.get("section"):
            continue
        if f["type"] == "heading":
            out.append({"kind": "heading", "label": label_text(f["label"], lang)})
            continue
        if f["type"] == "section":
            members = [m for m in fields if m.get("section") == f["id"]]
            if f.get("repeat"):
                rows_data = form.get(f["id"])
                if not isinstance(rows_data, list) or not rows_data:
                    continue
                blocks = [[_field_block(db, m, row.get(m["id"]), lang, show_secrets) for m in members
                           if m["type"] != "heading" and m["id"] in row] for row in rows_data]
                out.append({"kind": "repeat", "label": label_text(f["label"], lang), "rows": blocks})
            else:
                items = [_field_block(db, m, form.get(m["id"]), lang, show_secrets) for m in members
                         if m["type"] != "heading" and m["id"] in form]
                if items:
                    out.append({"kind": "section", "label": label_text(f["label"], lang), "items": items})
            continue
        if f["id"] in form:
            out.append(_field_block(db, f, form.get(f["id"]), lang, show_secrets))
    return out


def _field_block(db, f, value, lang, show_secrets):
    return {"kind": "field", "id": f["id"], "label": label_text(f["label"], lang),
            "value": value_text(db, f, value, lang, show_secrets=show_secrets), "secret": f["type"] == "secret"}


def flat(blocks):
    """(label, value) pairs of `rows()`, sections prefixed, for emails and plain text."""
    out = []
    for b in blocks:
        if b["kind"] == "field":
            out.append((b["label"], b["value"]))
        elif b["kind"] == "section":
            out.extend((f"{b['label']} – {i['label']}", i["value"]) for i in b["items"])
        elif b["kind"] == "repeat":
            for n, row in enumerate(b["rows"], start=1):
                out.extend((f"{b['label']} {n} – {i['label']}", i["value"]) for i in row)
    return out


def placeholder_values(db, definition, form, lang="de"):
    """{field id: text} for email placeholders: fields outside repeatable sections, never secrets."""
    repeats = {f["id"] for f in definition.get("fields", []) if f["type"] == "section" and f.get("repeat")}
    out = {}
    for f in definition.get("fields", []):
        if f["type"] in ("heading", "section", "signature", "secret") or f.get("section") in repeats:
            continue
        out[f["id"]] = value_text(db, f, form.get(f["id"]), lang) if f["id"] in form else ""
    return out


def person_name(db, email):
    user = masterdata.get_user(db, email, active_only=False) if email else None
    return user["display_name"] if user else (email or "")


STEP_TYPE_NAMES = {
    "fill": ("Formular ausfüllen", "Fill form"), "approve": ("Genehmigen", "Approve"),
    "fill_approve": ("Ausfüllen und genehmigen", "Fill and approve"),
    "approve_per_item": ("Freigabe je Eintrag", "Approve per item"), "sign": ("Unterschreiben", "Sign"),
    "confirm": ("Bestätigen", "Confirm"), "notify": ("Benachrichtigen", "Notify"),
}

DECISION_NAMES = {
    "submitted": ("Ausgefüllt", "Submitted"), "approved": ("Genehmigt", "Approved"),
    "rejected": ("Abgelehnt", "Rejected"), "signed": ("Unterschrieben", "Signed"),
    "signed_paper": ("Auf Papier unterschrieben", "Signed on paper"), "confirmed": ("Bestätigt", "Confirmed"),
}

STATUS_NAMES = {
    "open": ("Offen", "Open"), "returned": ("Zurückgegeben", "Returned"), "completed": ("Abgeschlossen", "Completed"),
    "cancelled": ("Storniert", "Cancelled"), "done": ("Erledigt", "Done"), "paper_pending": ("Wartet auf Scan",
                                                                                                "Waiting for scan"),
}


def name_of(table, key, lang):
    pair = table.get(key)
    return (pair[0] if lang == "de" else pair[1]) if pair else (key or "")


def local_time(iso, lang="de"):
    """'2026-11-06T08:14:32+00:00' -> '06.11.2026 09:14' in Europe/Berlin."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso).astimezone(ZoneInfo("Europe/Berlin"))
    except ValueError:
        return iso
    return dt.strftime("%d.%m.%Y %H:%M") if lang == "de" else dt.strftime("%Y-%m-%d %H:%M")
