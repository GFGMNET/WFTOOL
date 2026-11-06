"""PDFs of a request (phase 4: simple version; phase 5 adds the audit part).

`request_pdf()` is attached by Notify steps: the form data (secret fields
visible, as decided for the IT Assets Checklist), every step with decision,
person, time, IP address and browser, and the on-screen signatures.
`print_pdf()` is the page for a paper signature: the document, a signature and
date line, and the request number and form fingerprint in the footer.
"""

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import present
from .definitions import label_text

INK = colors.HexColor("#1D2633")
SOFT = colors.HexColor("#55606F")
RULE = colors.HexColor("#D3D9E2")
STAMP = colors.HexColor("#2B4C9B")

TXT = {
    "de": {"request": "Antrag", "workflow": "Workflow", "version": "Version", "status": "Status",
           "started": "Gestartet von", "on": "am", "data": "Angaben", "steps": "Schritte",
           "step": "Schritt", "who": "Person", "decision": "Entscheidung", "when": "Zeitpunkt",
           "from": "IP / Browser", "open": "offen", "reason": "Begründung", "signature": "Unterschrift",
           "sign_line": "Datum, Unterschrift", "confirm": "Mit meiner Unterschrift bestätige ich die Angaben oben.",
           "fingerprint": "Fingerabdruck der Formularversion", "page": "Seite", "signed_by": "Unterschrieben von",
           "paper_hint": "Bitte unterschreiben, einscannen und vom Antragsteller hochladen lassen.",
           "secret_hint": "Enthält geheime Angaben (PIN, PUK). Bitte sicher aufbewahren."},
    "en": {"request": "Request", "workflow": "Workflow", "version": "Version", "status": "Status",
           "started": "Started by", "on": "on", "data": "Details", "steps": "Steps",
           "step": "Step", "who": "Person", "decision": "Decision", "when": "Time",
           "from": "IP / browser", "open": "open", "reason": "Reason", "signature": "Signature",
           "sign_line": "Date, signature", "confirm": "With my signature I confirm the details above.",
           "fingerprint": "Fingerprint of the form version", "page": "Page", "signed_by": "Signed by",
           "paper_hint": "Please sign, scan, and have the person who started the request upload it.",
           "secret_hint": "Contains secret values (PIN, PUK). Keep it safe."},
}


def _styles():
    ss = getSampleStyleSheet()
    base = ParagraphStyle("base", parent=ss["Normal"], fontName="Helvetica", fontSize=9.5, leading=12.5,
                          textColor=INK, alignment=TA_LEFT)
    return {
        "base": base,
        "small": ParagraphStyle("small", parent=base, fontSize=8, leading=10, textColor=SOFT),
        "title": ParagraphStyle("title", parent=base, fontName="Helvetica-Bold", fontSize=16, leading=20),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=11.5, leading=15,
                             spaceBefore=10, spaceAfter=4),
        "label": ParagraphStyle("label", parent=base, textColor=SOFT),
        "bold": ParagraphStyle("bold", parent=base, fontName="Helvetica-Bold"),
    }


def _p(text, style):
    return Paragraph(escape(str(text or "")).replace("\n", "<br/>"), style)


def _table(rows, widths, header=False):
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
             ("LEFTPADDING", (0, 0), (-1, -1), 2)]
    if header:
        style.append(("LINEBELOW", (0, 0), (-1, 0), 0.8, INK))
    t.setStyle(TableStyle(style))
    return t


def _data_flowables(db, req, lang, st, width):
    out = []
    blocks = present.rows(db, req["definition"], req["form"], lang, show_secrets=True)
    label_w = width * 0.36

    def field_rows(items, indent=""):
        return [[_p(indent + i["label"], st["label"]), _p(i["value"], st["base"])]
                for i in items if i["kind"] == "field" and not (i["id"] in _signature_ids(req))]

    plain = []
    for b in blocks:
        if b["kind"] == "field":
            plain.extend(field_rows([b]))
        else:
            if plain:
                out.append(_table(plain, [label_w, width - label_w]))
                plain = []
            if b["kind"] == "heading":
                out.append(_p(b["label"], st["bold"]))
            elif b["kind"] == "section":
                out.append(Spacer(1, 4))
                out.append(_p(b["label"], st["bold"]))
                out.append(_table(field_rows(b["items"]), [label_w, width - label_w]))
            elif b["kind"] == "repeat":
                for n, row in enumerate(b["rows"], start=1):
                    out.append(Spacer(1, 4))
                    out.append(_p(f"{b['label']} {n}", st["bold"]))
                    out.append(_table(field_rows(row), [label_w, width - label_w]))
    if plain:
        out.append(_table(plain, [label_w, width - label_w]))
    return out


def _signature_ids(req):
    return {f["id"] for f in req["definition"]["fields"] if f["type"] == "signature"}


def _header(req, lang, st, db):
    T = TXT[lang]
    definition = req["definition"]
    title = label_text(definition.get("label"), lang) or definition["name"]
    meta = (f"{T['request']} {req['number']} · {T['workflow']} {definition['name']} · "
            f"{T['version']} {req['workflow_version']} · {T['status']}: "
            f"{present.name_of(present.STATUS_NAMES, req['status'], lang)}")
    started = (f"{T['started']} {present.person_name(db, req['starter_email'])} ({req['starter_email']}) "
               f"{T['on']} {present.local_time(req['created_at'], lang)}")
    return [_p(title, st["title"]), Spacer(1, 3), _p(meta, st["small"]), _p(started, st["small"]), Spacer(1, 8)]


def _steps(req, lang, st, width, files):
    T = TXT[lang]
    definition = req["definition"]
    tasks = [t for t in req["tasks"] if t["round"] == req["round"] and t["status"] in ("done", "open", "paper_pending")]
    if not tasks:
        return []
    rows = [[_p(T["step"], st["bold"]), _p(T["who"], st["bold"]), _p(T["decision"], st["bold"]),
             _p(T["when"], st["bold"]), _p(T["from"], st["bold"])]]
    images = []
    for t in tasks:
        step = definition["steps"][t["step_index"]]
        name = label_text(step.get("label"), lang) or step["id"]
        if t["item_value"]:
            name += f" ({t['item_value']})"
        decision = present.name_of(present.DECISION_NAMES, t["decision"], lang) if t["decision"] else T["open"]
        if t["reason"]:
            decision += f"\n{T['reason']}: {t['reason']}"
        origin = " / ".join(x for x in (t["ip"], _short_agent(t["user_agent"])) if x)
        rows.append([_p(f"{t['step_index'] + 1}. {name}", st["base"]), _p(t["decided_by"] or t["assignee_email"], st["base"]),
                     _p(decision, st["base"]), _p(present.local_time(t["decided_at"], lang), st["base"]),
                     _p(origin, st["small"])])
        if t.get("signature_file") and files is not None:
            try:
                img = Image(io.BytesIO(files.load(t["signature_file"])))
                ratio = img.imageHeight / float(img.imageWidth or 1)
                img.drawWidth, img.drawHeight = 60 * mm, 60 * mm * ratio
                images.append(KeepTogether([_p(f"{T['signed_by']} {t['decided_by']} · "
                                               f"{present.local_time(t['decided_at'], lang)}", st["small"]), img,
                                            _p(f"SHA-256: {t['signature_fingerprint']}", st["small"])]))
            except Exception:  # an unreadable image must not stop the PDF
                pass
    widths = [width * 0.25, width * 0.22, width * 0.2, width * 0.13, width * 0.2]
    return [_p(T["steps"], st["h2"]), _table(rows, widths, header=True), Spacer(1, 6)] + images


def _short_agent(ua):
    if not ua:
        return ""
    for name in ("Edg/", "Firefox/", "Chrome/", "Safari/"):
        if name in ua:
            browser = name.rstrip("/").replace("Edg", "Edge")
            break
    else:
        browser = ua[:30]
    system = next((s for s in ("Windows", "Mac OS X", "Android", "iPhone", "Linux") if s in ua), "")
    return f"{browser} {system}".strip()


def _build(story, footer_text):
    buf = io.BytesIO()

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(SOFT)
        canvas.drawString(18 * mm, 10 * mm, footer_text)
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, str(doc.page))
        canvas.setStrokeColor(STAMP)
        canvas.setLineWidth(2)
        canvas.line(18 * mm, A4[1] - 12 * mm, A4[0] - 18 * mm, A4[1] - 12 * mm)
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title=footer_text, author="WFTOOL")
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def request_pdf(db, req, lang="de", files=None):
    """The request with its data and steps, as PDF bytes."""
    st = _styles()
    width = A4[0] - 36 * mm
    T = TXT[lang]
    story = _header(req, lang, st, db)
    story.append(_p(T["data"], st["h2"]))
    story.extend(_data_flowables(db, req, lang, st, width))
    story.extend(_steps(req, lang, st, width, files))
    if any(f["type"] == "secret" for f in req["definition"]["fields"]):
        story.append(Spacer(1, 6))
        story.append(_p(T["secret_hint"], st["small"]))
    footer = f"{T['request']} {req['number']} · {T['fingerprint']}: {req['form_fingerprint'] or ''}"
    return _build(story, footer)


def print_pdf(db, req, task, lang="de"):
    """The page to sign on paper."""
    st = _styles()
    width = A4[0] - 36 * mm
    T = TXT[lang]
    story = _header(req, lang, st, db)
    story.append(_p(T["data"], st["h2"]))
    story.extend(_data_flowables(db, req, lang, st, width))
    signer = f"{present.person_name(db, task['assignee_email'])} ({task['assignee_email']})"
    line = Table([["", ""], [_p(T["sign_line"], st["small"]), _p(signer, st["small"])]],
                 colWidths=[width * 0.55, width * 0.45], rowHeights=[22 * mm, None], hAlign="LEFT")
    line.setStyle(TableStyle([("LINEABOVE", (0, 1), (0, 1), 0.8, INK), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [Spacer(1, 10), _p(T["confirm"], st["base"]), KeepTogether([line]), Spacer(1, 8),
              _p(T["paper_hint"], st["small"])]
    footer = f"{T['request']} {req['number']} · {T['fingerprint']}: {req['form_fingerprint'] or ''}"
    return _build(story, footer)
