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


# --- full report (phase 5) ------------------------------------------------------

REPORT_TXT = {
    "de": {"versions": "Frühere Versionen des Formulars", "version": "Version", "changed": "Geändert",
           "by": "von", "none_changed": "keine Änderung an den Angaben", "history": "Verlauf",
           "fetched": "Geöffnete Links (keine Entscheidung; auch Mailfilter öffnen Links)",
           "check": "Prüfung des Ereignisprotokolls", "check_ok": "In Ordnung: {n} Einträge geprüft, Kette unverändert.",
           "check_bad": "FEHLER: Eintrag {id} – {problem}. Das Protokoll wurde verändert.",
           "time": "Zeitpunkt", "event": "Ereignis", "who": "Wer", "ip": "IP / Browser",
           "scan": "Scan der Unterschrift auf Papier", "scan_fp": "SHA-256 des Scans",
           "report": "Bericht", "created": "Erstellt", "removed": "(entfernt)", "added": "(neu)",
           "rounds": "Frühere Durchgänge", "round": "Durchgang"},
    "en": {"versions": "Earlier versions of the form", "version": "Version", "changed": "Changed",
           "by": "by", "none_changed": "no change to the details", "history": "History",
           "fetched": "Links opened (not a decision; mail filters open links too)",
           "check": "Check of the event log", "check_ok": "OK: {n} entries checked, chain unchanged.",
           "check_bad": "ERROR: entry {id} – {problem}. The log was changed.",
           "time": "Time", "event": "Event", "who": "Who", "ip": "IP / browser",
           "scan": "Scan of the paper signature", "scan_fp": "SHA-256 of the scan",
           "report": "Report", "created": "Created", "removed": "(removed)", "added": "(new)",
           "rounds": "Earlier rounds", "round": "Round"},
}


def _diff(db, definition, old, new, lang, show_secrets):
    a = dict(present.flat(present.rows(db, definition, old, lang, show_secrets=show_secrets)))
    b = dict(present.flat(present.rows(db, definition, new, lang, show_secrets=show_secrets)))
    R = REPORT_TXT[lang]
    out = []
    for key in list(dict.fromkeys(list(a) + list(b))):
        if a.get(key) != b.get(key):
            out.append((key, a.get(key, R["added"]), b.get(key, R["removed"])))
    return out


def report_pdf(db, req, lang="de", *, files=None, versions=(), events=(), check=None, show_secrets=True):
    """The full report: data, steps with signatures, the paper scan, earlier versions with their changes,
    the history (opened links apart), and the result of the tamper check. Returns PDF bytes."""
    st = _styles()
    width = A4[0] - 36 * mm
    T, R = TXT[lang], REPORT_TXT[lang]
    if not show_secrets:
        secret_ids = {f["id"] for f in req["definition"]["fields"] if f["type"] == "secret"}
        req = dict(req, form={k: (present.MASK if k in secret_ids and v else v) for k, v in req["form"].items()})
    story = _header(req, lang, st, db)
    story.append(_p(f"{R['report']} · {R['created']} {present.local_time(clock_now_iso(), lang)}", st["small"]))
    story.append(_p(T["data"], st["h2"]))
    story.extend(_data_flowables(db, req, lang, st, width))
    story.extend(_steps(req, lang, st, width, files))

    # paper scans: fingerprint, and the scan itself
    scans = []
    for t in req["tasks"]:
        if t["decision"] == "signed_paper" and t.get("scan_file") and files is not None:
            scan_event = next((e for e in events if e["type"] == "task.paper_uploaded"
                               and e["data"].get("task") == t["id"]), None)
            story.append(_p(R["scan"], st["h2"]))
            story.append(_p(f"{t['decided_by']} · {present.local_time(t['decided_at'], lang)}", st["base"]))
            if scan_event:
                story.append(_p(f"{R['scan_fp']}: {scan_event['data'].get('scan_fingerprint')}", st["small"]))
            try:
                scans.append(files.load(t["scan_file"]))
            except Exception:
                pass

    # decisions of earlier rounds (before a rejection sent the request back)
    earlier = [t for t in req["tasks"] if t["round"] != req["round"] and t["status"] == "done"]
    if earlier:
        story.append(_p(R["rounds"], st["h2"]))
        rows = [[_p(R["round"], st["bold"]), _p(T["step"], st["bold"]), _p(T["who"], st["bold"]),
                 _p(T["decision"], st["bold"]), _p(T["when"], st["bold"])]]
        for t in earlier:
            step = req["definition"]["steps"][t["step_index"]]
            decision = present.name_of(present.DECISION_NAMES, t["decision"], lang)
            if t["reason"]:
                decision += f"\n{T['reason']}: {t['reason']}"
            rows.append([_p(str(t["round"]), st["base"]),
                         _p(f"{t['step_index'] + 1}. {label_text(step.get('label'), lang) or step['id']}", st["base"]),
                         _p(t["decided_by"] or t["assignee_email"], st["base"]), _p(decision, st["base"]),
                         _p(present.local_time(t["decided_at"], lang), st["base"])])
        story.append(_table(rows, [width * 0.1, width * 0.3, width * 0.22, width * 0.22, width * 0.16], header=True))

    # earlier versions
    if len(versions) > 1:
        story.append(_p(R["versions"], st["h2"]))
        for prev, cur in zip(versions, versions[1:]):
            story.append(_p(f"{R['version']} {cur['version']} · {present.local_time(cur['created_at'], lang)} · "
                            f"{R['by']} {cur['created_by']}", st["bold"]))
            changes = _diff(db, req["definition"], prev["data"], cur["data"], lang, show_secrets)
            if not changes:
                story.append(_p(R["none_changed"], st["small"]))
                continue
            rows = [[_p(k, st["label"]), _p(old, st["base"]), _p("→ " + new, st["base"])] for k, old, new in changes]
            story.append(_table(rows, [width * 0.34, width * 0.33, width * 0.33]))

    # history: decisions and other events, opened links apart
    def event_rows(items):
        rows = [[_p(R["time"], st["bold"]), _p(R["event"], st["bold"]), _p(R["who"], st["bold"]),
                 _p(R["ip"], st["bold"])]]
        for e in items:
            detail = e["data"].get("step") or e["data"].get("decision") or ""
            rows.append([_p(present.local_time(e["ts"], lang), st["small"]), _p(f"{e['type']} {detail}".strip(), st["small"]),
                         _p(e["actor"] or "", st["small"]),
                         _p(" / ".join(x for x in (e["ip"], _short_agent(e["user_agent"])) if x), st["small"])])
        return _table(rows, [width * 0.18, width * 0.34, width * 0.26, width * 0.22], header=True)

    main = [e for e in events if e["type"] != "link.fetched"]
    fetched = [e for e in events if e["type"] == "link.fetched"]
    if main:
        story += [_p(R["history"], st["h2"]), event_rows(main)]
    if fetched:
        story += [_p(R["fetched"], st["h2"]), event_rows(fetched)]

    if check is not None:
        story.append(_p(R["check"], st["h2"]))
        story.append(_p(R["check_ok"].format(n=check["checked"]) if check["ok"]
                        else R["check_bad"].format(id=check["first_bad_id"], problem=check["problem"]), st["bold"]))

    footer = f"{T['request']} {req['number']} · {T['fingerprint']}: {req['form_fingerprint'] or ''}"
    data = _build(story, footer)
    return _append_scans(data, scans)


def clock_now_iso():
    from . import clock
    return clock.now_iso()


def _append_scans(data, scans):
    """Add the paper scans as pages: PDF pages as they are, images on a page of their own."""
    if not scans:
        return data
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(data)).pages:
        writer.add_page(page)
    for scan in scans:
        try:
            if scan.startswith(b"%PDF"):
                for page in PdfReader(io.BytesIO(scan)).pages:
                    writer.add_page(page)
            else:
                img = Image(io.BytesIO(scan))
                ratio = img.imageHeight / float(img.imageWidth or 1)
                w = A4[0] - 36 * mm
                img.drawWidth, img.drawHeight = w, min(w * ratio, A4[1] - 40 * mm)
                buf = io.BytesIO()
                SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm,
                                  bottomMargin=18 * mm).build([img])
                for page in PdfReader(io.BytesIO(buf.getvalue())).pages:
                    writer.add_page(page)
        except Exception:  # a broken scan must not stop the report
            continue
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
