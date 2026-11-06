"""Request pages (signed in) and task pages (email link or signed in).

Opening a task link only shows the page and logs "link fetched"; the decision
is made by pressing a button, which records email, time, IP address and browser
and uses up the link (design: "Why opening the link does not decide").
"""

import base64
import binascii
import json  # noqa: F401  (used by the audit page)

from flask import (
    Response, abort, current_app, flash, g, redirect, render_template, request, url_for,
)

from datetime import date

from .. import audit, links, masterdata, outbox, pdf, present, reports, workflows
from ..definitions import label_text, start_fields, step_fields
from ..engine import Actor
from ..errors import NotFound, StateError, ValidationError, WFError
from . import forms
from .views import _client, _db, _flash_error, admin_required, login_required, t

MAX_SIGNATURE_BYTES = 600 * 1024
MAX_SCAN_BYTES = 20 * 1024 * 1024
SCAN_TYPES = {b"%PDF": "pdf", b"\xff\xd8\xff": "jpg", b"\x89PNG": "png"}


def _engine():
    return current_app.extensions["wftool"]["engine"]


def _files():
    return current_app.extensions["wftool"]["files"]


def _actor(email, via):
    c = _client()
    return Actor(email, ip=c["ip"], user_agent=c["user_agent"], via=via)


def _users():
    return [dict(r) for r in _db().all("SELECT email, display_name FROM ad_users WHERE active = 1"
                                       " ORDER BY display_name")]


def _my_email():
    return (g.user.get("email") or "").strip().lower()


def _can_see(req):
    if g.user["role"] in ("admin", "auditor"):
        return True
    return _my_email() and req["starter_email"].lower() == _my_email()


def _can_act(req):
    """Starter or admin may resend, cancel and handle paper."""
    return g.user["role"] == "admin" or (_my_email() and req["starter_email"].lower() == _my_email())


def _load(request_id):
    try:
        req = _engine().get_request(request_id)
    except NotFound:
        abort(404)
    if not _can_see(req):
        abort(403)
    return req


def register(app):
    app.jinja_env.globals.update(repeat_rows=forms.repeat_rows, local_time=_local_time, name_of=_name_of,
                                 person_name=lambda e: present.person_name(_db(), e))


def _local_time(iso):
    return present.local_time(iso, g.lang)


def _name_of(table, key):
    return present.name_of(getattr(present, table), key, g.lang)


# ------------------------------------------------------------------- list

def _filters():
    a = request.args
    out = {k: a.get(k, "").strip() for k in ("workflow", "person", "status", "number")}
    for k in ("date_from", "date_to"):
        try:
            out[k] = date.fromisoformat(a.get(k, "")) if a.get(k) else None
        except ValueError:
            out[k] = None
    return out


def _starter_only():
    return (_my_email() or "-") if g.user["role"] == "starter" else None


@login_required
def request_list():
    db = _db()
    filters = _filters()
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    result = reports.search(db, filters, starter_only=_starter_only(), page=page, lang=g.lang)
    names = [r["name"] for r in db.all("SELECT DISTINCT w.name FROM workflows w JOIN workflow_versions v"
                                       " ON v.workflow_id = w.id ORDER BY w.name")]
    query = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in filters.items() if v}
    return render_template("requests/list.html", result=result, rows=result["rows"], f=query,
                           workflows=names, startable=_startable(), query=query)


@login_required
def request_csv():
    db = _db()
    rows = reports.all_rows(db, _filters(), starter_only=_starter_only(), lang=g.lang)
    audit.record(db, "report.csv_exported", actor=g.user["username"], **_client(),
                 data={"rows": len(rows), "filters": {k: str(v) for k, v in _filters().items() if v}})
    return Response(reports.to_csv(rows, g.lang), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="wftool-{date.today().isoformat()}.csv"'})


def _startable():
    db = _db()
    names = workflows.startable_workflows(db, g.user)
    out = []
    for name in names:
        w = workflows.get_workflow(db, name)
        version = workflows.latest_version(db, name)
        out.append({"id": w["id"], "name": name,
                    "label": label_text(version["definition"].get("label"), g.lang) or name})
    return out


# ------------------------------------------------------------------ start

@login_required
def request_new(workflow_id):
    db = _db()
    row = db.one("SELECT name FROM workflows WHERE id = ?", (workflow_id,))
    if not row:
        abort(404)
    name = row["name"]
    if not workflows.can_start(db, g.user, name):
        abort(403)
    try:
        version = workflows.latest_version(db, name)
    except NotFound:
        abort(404)
    definition = version["definition"]
    fields = start_fields(definition)
    values, errors = {}, {}
    if not _my_email():
        flash(t("Your account has no email address. An admin sets it under Accounts; the request is sent "
                "under this address."), "error")
    elif request.method == "POST":
        values = forms.parse(fields, request.form)
        try:
            req = _engine().start(name, _actor(_my_email(), "app"), values)
        except ValidationError as exc:
            errors = forms.errors_from(exc.details, g.lang)
            flash(t("Please correct the marked fields."), "error")
        except WFError as exc:
            _flash_error(exc)
        else:
            flash(t("Request {number} sent.").format(number=req["number"]), "ok")
            return redirect(url_for("request_detail", request_id=req["id"]))
    else:
        values = {f["id"]: f["default"] for f in fields if "default" in f}
    return render_template("requests/new.html", definition=definition, version=version,
                           blocks=forms.layout(db, fields, g.lang), values=values, errors=errors, users=_users(),
                           title=label_text(definition.get("label"), g.lang) or name,
                           action=url_for("request_new", workflow_id=workflow_id), resubmit=None)


# ----------------------------------------------------------------- detail

@login_required
def request_detail(request_id):
    db = _db()
    req = _load(request_id)
    has_secrets = any(f["type"] == "secret" for f in req["definition"]["fields"])
    auditor = g.user["role"] == "auditor"
    show = has_secrets and (auditor or (request.method == "POST" and request.form.get("action") == "show_secrets"
                                        and _can_act(req)))
    if show:
        audit.record(db, "secret.viewed", request_id=req["id"], actor=g.user["username"], **_client(),
                     data={"page": "request"})
    definition = req["definition"]
    events = audit.events_for(db, req["id"])
    # what happened to steps without tasks in the current round (skipped, or a Notify that was sent)
    starts = [i for i, e in enumerate(events) if e["type"] in ("request.created", "request.resubmitted")]
    this_round = events[starts[-1]:] if starts else events
    outcome = {}
    for e in this_round:
        if e["type"] == "step.skipped":
            outcome[e["data"].get("step")] = "skipped"
        elif e["type"] == "step.notified":
            outcome[e["data"].get("step")] = "sent"
    steps = []
    for i, step in enumerate(definition["steps"]):
        tasks = [x for x in req["tasks"] if x["round"] == req["round"] and x["step_index"] == i]
        if tasks:
            state = "open" if any(x["status"] in ("open", "paper_pending") for x in tasks) else "done"
        else:
            state = outcome.get(step["id"], "waiting")
        steps.append({"index": i, "step": step, "label": label_text(step.get("label"), g.lang) or step["id"],
                      "tasks": tasks, "state": state})
    earlier = [x for x in req["tasks"] if x["round"] != req["round"]]
    history = list(reversed(events))[:200]
    return render_template(
        "requests/detail.html", req=req, steps=steps, earlier=earlier, history=history,
        blocks=present.rows(db, definition, req["form"], g.lang, show_secrets=show),
        has_secrets=has_secrets, show=show,
        can_act=_can_act(req), is_admin=g.user["role"] == "admin",
        is_starter=bool(_my_email()) and _my_email() == req["starter_email"].lower(),
        title=label_text(definition.get("label"), g.lang) or definition["name"], users=_users())


@login_required
def request_action(request_id):
    req = _load(request_id)
    engine = _engine()
    f = request.form
    action = f.get("action")
    who = _actor(_my_email() or g.user["username"], "app")
    try:
        task_id = int(f.get("task_id", 0) or 0)
        if action == "resend" and _can_act(req):
            engine.resend(task_id, who)
            flash(t("A new link was sent; the old one no longer works."), "ok")
        elif action == "cancel" and _can_act(req):
            engine.cancel(req["id"], who, f.get("reason", ""))
            flash(t("Request cancelled."), "ok")
        elif action == "reassign" and g.user["role"] == "admin":
            engine.reassign(task_id, f.get("email", "").strip(), who, f.get("reason", ""))
            flash(t("Task reassigned; the new person gets an email."), "ok")
        elif action == "paper" and _is_starter(req):
            engine.mark_paper(task_id, who)
            flash(t("Marked as signature on paper. Print the page, have it signed and upload the scan."), "ok")
        else:
            abort(403)
    except WFError as exc:
        _flash_error(exc)
    return redirect(url_for("request_detail", request_id=req["id"]))


def _is_starter(req):
    return bool(_my_email()) and _my_email() == req["starter_email"].lower()


@login_required
def request_resubmit(request_id):
    db = _db()
    req = _load(request_id)
    if req["status"] != "returned" or not _is_starter(req):
        abort(403)
    fields = start_fields(req["definition"])
    values, errors = dict(req["form"]), {}
    if request.method == "POST":
        values = forms.parse(fields, request.form)
        try:
            _engine().resubmit(req["id"], _actor(_my_email(), "app"), values)
        except ValidationError as exc:
            errors = forms.errors_from(exc.details, g.lang)
            flash(t("Please correct the marked fields."), "error")
        except WFError as exc:
            _flash_error(exc)
        else:
            flash(t("Request sent again; all steps run once more."), "ok")
            return redirect(url_for("request_detail", request_id=req["id"]))
    reason = next((x["reason"] for x in reversed(req["tasks"]) if x["decision"] == "rejected"), None)
    return render_template("requests/new.html", definition=req["definition"], version=None,
                           blocks=forms.layout(db, fields, g.lang), values=values, errors=errors, users=_users(),
                           title=label_text(req["definition"].get("label"), g.lang) or req["definition"]["name"],
                           action=url_for("request_resubmit", request_id=req["id"]), resubmit=req, reason=reason)


@login_required
def request_pdf(request_id):
    """The full report (phase 5). Secret values are shown: to IT and admins as before, and to
    auditors, as decided on 6 Nov 2026. Each download is logged."""
    db = _db()
    req = _load(request_id)
    engine = _engine()
    data = pdf.report_pdf(db, req, g.lang, files=_files(), versions=engine.form_versions(req["id"]),
                          events=audit.events_for(db, req["id"]), check=audit.verify(db), show_secrets=True)
    audit.record(db, "request.pdf_downloaded", request_id=req["id"], actor=g.user["username"], **_client(),
                 data={"secrets_shown": True, "report": True})
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{req["number"]}.pdf"'})


def _is_secret(req, key):
    return any(f["id"] == key and f["type"] == "secret" for f in req["definition"]["fields"])


@login_required
def task_print(request_id, task_id):
    db = _db()
    req = _load(request_id)
    if not _can_act(req):
        abort(403)
    task = next((x for x in req["tasks"] if x["id"] == task_id and x["type"] == "sign"), None)
    if not task:
        abort(404)
    data = pdf.print_pdf(db, req, task, "de")
    audit.record(db, "paper.printed", request_id=req["id"], actor=g.user["username"], **_client(),
                 data={"task": task_id})
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{req["number"]}-unterschrift.pdf"'})


@login_required
def task_scan(request_id, task_id):
    req = _load(request_id)
    if not _is_starter(req):
        abort(403)
    upload = request.files.get("scan")
    data = upload.read(MAX_SCAN_BYTES + 1) if upload else b""
    number = request.form.get("number", "").strip()
    if not data:
        flash(t("Choose the scan (PDF, JPG or PNG) first."), "error")
    elif len(data) > MAX_SCAN_BYTES:
        flash(t("The scan is larger than 20 MB."), "error")
    elif not any(data.startswith(magic) for magic in SCAN_TYPES):
        flash(t("The scan must be a PDF, JPG or PNG file."), "error")
    elif number != req["number"]:
        flash(t("The request number does not match. Type the number printed at the bottom of the page."), "error")
    else:
        try:
            _engine().complete_paper(task_id, _actor(_my_email(), "app"), data)
            flash(t("Scan uploaded; the step is signed on paper."), "ok")
        except WFError as exc:
            _flash_error(exc)
    return redirect(url_for("request_detail", request_id=req["id"]))


# ------------------------------------------------------------------ tasks

def task_link(token):
    """A task opened from an email link (no sign-in)."""
    db = _db()
    link = links.find(db, token)
    if link is None:
        return render_template("tasks/invalid.html", reason="unknown", task=None, req=None, admin=_admin()), 404
    task = _engine().get_task(link["task_id"])
    req = _engine().get_request(task["request_id"])
    if request.method == "GET":
        links.record_fetch(db, link, req["id"], **_client())
    if link["state"] != "valid":
        return render_template("tasks/invalid.html", reason=link["state"], task=task, req=req, admin=_admin()), 410
    return _task_page(req, task, link["email"], via="link", link=link)


@login_required
def task_app(task_id):
    """The same task page for a signed-in person whose account email is the task's."""
    try:
        task = _engine().get_task(task_id)
    except NotFound:
        abort(404)
    if not _my_email() or task["assignee_email"].lower() != _my_email():
        abort(403)
    req = _engine().get_request(task["request_id"])
    if task["status"] != "open" or req["status"] != "open" or task["round"] != req["round"]:
        return render_template("tasks/invalid.html", reason="closed", task=task, req=req, admin=_admin()), 410
    return _task_page(req, task, _my_email(), via="app", link=None)


def _admin():
    return masterdata.get_setting(_db(), "admin_email", "")


def _task_page(req, task, email, *, via, link):
    db = _db()
    definition = req["definition"]
    step = definition["steps"][task["step_index"]]
    fields = step_fields(definition, task["step_index"]) if task["type"] in ("fill", "fill_approve") else []
    values, errors = dict(req["form"]), {}
    if request.method == "POST":
        action = request.form.get("action", "")
        decision = {"submit": "submitted", "approve": "approved", "reject": "rejected", "sign": "signed",
                    "confirm": "confirmed", "paper": "paper"}.get(action)
        posted = forms.parse(fields, request.form) if fields else {}
        values.update(posted)
        image = None
        if decision == "signed":
            image = _signature_bytes(request.form.get("signature", ""))
            if image is None:
                errors["signature"] = t("Please sign in the box first.")
        if decision and not errors:
            try:
                _engine().decide(task["id"], decision, _actor(email, via), reason=request.form.get("reason"),
                                 values=posted, signature_image=image, link_id=link["id"] if link else None)
            except ValidationError as exc:
                errors = forms.errors_from(exc.details, g.lang)
            except StateError as exc:
                flash(t(exc.message), "error")
                return render_template("tasks/invalid.html", reason="closed", task=task, req=req,
                                       admin=_admin()), 409
            else:
                done = _engine().get_task(task["id"])
                return render_template("tasks/done.html", req=req, task=done, step=step,
                                       step_label=label_text(step.get("label"), g.lang) or step["id"], via=via)
        elif not decision:
            abort(400)
        if "reason" in errors:
            errors["reason"] = t("Please give a reason for the rejection.")
    sign = task["type"] == "sign"
    return render_template(
        "tasks/task.html", req=req, task=task, step=step, email=email, via=via,
        step_label=label_text(step.get("label"), g.lang) or step["id"],
        title=label_text(definition.get("label"), g.lang) or definition["name"],
        blocks_view=present.rows(db, definition, {k: v for k, v in req["form"].items()
                                                  if k not in {f["id"] for f in fields}}, g.lang, show_secrets=sign),
        blocks=forms.layout(db, fields, g.lang, known=req["form"],
                            condition=lambda c, known: _engine()._condition(c, known, definition)),
        values=values, errors=errors, users=_users() if fields else [],
        allow_paper=sign and step.get("allow_paper", True), item=task["item_value"],
        signature_error=errors.get("signature"))


def _signature_bytes(data_url):
    prefix = "data:image/png;base64,"
    if not data_url.startswith(prefix):
        return None
    try:
        data = base64.b64decode(data_url[len(prefix):], validate=True)
    except (binascii.Error, ValueError):
        return None
    if not data.startswith(b"\x89PNG") or len(data) > MAX_SIGNATURE_BYTES or len(data) < 100:
        return None
    return data


# ------------------------------------------------------------------ admin

@admin_required
def admin_tasks():
    db = _db()
    only_inactive = request.args.get("inactive") == "1"
    rows = []
    for r in db.all("SELECT t.*, r.number, r.starter_email, u.active AS user_active, u.display_name"
                    " FROM tasks t JOIN requests r ON r.id = t.request_id AND r.round = t.round"
                    " LEFT JOIN ad_users u ON u.email = t.assignee_email"
                    " WHERE t.status IN ('open', 'paper_pending') AND r.status = 'open' ORDER BY t.created_at"):
        r = dict(r)
        r["inactive"] = not r["user_active"]
        if only_inactive and not r["inactive"]:
            continue
        rows.append(r)
    return render_template("admin/tasks.html", rows=rows, only_inactive=only_inactive, users=_users())


@admin_required
def admin_outbox():
    db = _db()
    if request.method == "POST":
        if request.form.get("action") == "send":
            ext = current_app.extensions["wftool"]
            result = outbox.send_due(db, ext["cipher"], ext["files"])
            if result["skipped"]:
                flash(t("SMTP is not set up yet (Settings)."), "error")
            else:
                flash(t("Sent: {sent}, retry later: {retry}, failed: {failed}.").format(**result),
                      "ok" if not result["failed"] and not result["retry"] else "error")
        elif request.form.get("action") == "retry":
            outbox.retry(db, int(request.form.get("id", 0) or 0), by=g.user["username"])
            flash(t("The email will be sent again."), "ok")
        return redirect(url_for("admin_outbox"))
    status = request.args.get("status") or None
    return render_template("admin/outbox.html", rows=outbox.recent(db, 300, status), counts=outbox.counts(db),
                           status=status)


def auditor_required(view):
    from functools import wraps

    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if g.user["role"] not in ("admin", "auditor"):
            abort(403)
        return view(*args, **kwargs)
    return wrapper


@auditor_required
def audit_page():
    db = _db()
    check = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "verify":
            check = audit.verify(db)
            audit.record(db, "audit.verified", actor=g.user["username"], **_client(),
                         data={"ok": check["ok"], "checked": check["checked"], "first_bad_id": check["first_bad_id"]})
        elif action == "anchor" and g.user["role"] == "admin":
            if reports.send_anchor(db, by=g.user["username"]):
                flash(t("The fingerprint email was queued for the admin email."), "ok")
            else:
                flash(t("No admin email in Settings."), "error")
            return redirect(url_for("audit_page"))
    a = request.args
    where, params = ["1 = 1"], []
    for key, col in (("date_from", ">="), ("date_to", "<")):
        try:
            day = date.fromisoformat(a.get(key, ""))
        except ValueError:
            continue
        if key == "date_to":
            from datetime import timedelta
            day = day + timedelta(days=1)
        where.append(f"e.ts {col} ?")
        params.append(reports._day_start_utc(day))
    if a.get("type"):
        where.append("e.type LIKE ?")
        params.append(a.get("type").strip() + "%")
    if a.get("person"):
        where.append("LOWER(e.actor) LIKE ?")
        params.append(f"%{a.get('person').strip().lower()}%")
    if a.get("number"):
        where.append("r.number LIKE ?")
        params.append(f"%{a.get('number').strip()}%")
    try:
        page = max(1, int(a.get("page", "1")))
    except ValueError:
        page = 1
    size = 100
    base = " FROM events e LEFT JOIN requests r ON r.id = e.request_id WHERE " + " AND ".join(where)
    total = db.one("SELECT COUNT(*) AS n" + base, params)["n"]
    rows = [dict(r, data=json.loads(r["data_json"])) for r in db.all(
        "SELECT e.*, r.number" + base + " ORDER BY e.id DESC LIMIT ? OFFSET ?", params + [size, (page - 1) * size])]
    anchors = [dict(x, matches=reports.anchor_matches(db, x)) for x in reports.anchors(db)]
    types = [r["type"] for r in db.all("SELECT DISTINCT type FROM events ORDER BY type")]
    query = {k: a.get(k) for k in ("date_from", "date_to", "type", "person", "number") if a.get(k)}
    return render_template("audit/index.html", rows=rows, total=total, page=page,
                           pages=max(1, (total + size - 1) // size), check=check, anchors=anchors, types=types,
                           f=query, query=query, retention=reports.retention_months(db),
                           last_hash=audit.latest_hash(db))


@admin_required
def admin_health():
    """Daily check for the admin: is everything running? (design: Operations)"""
    import shutil
    db = _db()
    data_dir = current_app.config["DATA_DIR"]
    imports = [dict(r) for r in db.all(
        "SELECT i.* FROM imports i JOIN (SELECT kind, MAX(id) AS id FROM imports GROUP BY kind) m ON m.id = i.id"
        " ORDER BY i.kind")]
    counts = outbox.counts(db)
    oldest = db.one("SELECT MIN(created_at) AS t FROM outbox WHERE status = 'pending'")["t"]
    anchors = reports.anchors(db, 1)
    reminded = db.one("SELECT COUNT(*) AS n FROM tasks t JOIN requests r ON r.id = t.request_id AND r.round = t.round"
                      " WHERE t.status IN ('open', 'paper_pending') AND r.status = 'open'"
                      " AND t.reminded_at IS NOT NULL")["n"]
    usage = shutil.disk_usage(data_dir)
    db_size = (data_dir / "wftool.db").stat().st_size if (data_dir / "wftool.db").exists() else 0
    return render_template("admin/health.html", imports=imports, counts=counts, oldest=oldest,
                           anchor=anchors[0] if anchors else None, reminded=reminded,
                           free_gb=usage.free / 1024 ** 3, free_pct=100 * usage.free / usage.total,
                           db_mb=db_size / 1024 ** 2, test_mode=current_app.config.get("TEST_MODE"))


ROUTES = [
    ("/admin/health", admin_health, ["GET"]),
    ("/requests.csv", request_csv, ["GET"]),
    ("/audit", audit_page, ["GET", "POST"]),
    ("/requests", request_list, ["GET"]),
    ("/requests/new/<int:workflow_id>", request_new, ["GET", "POST"]),
    ("/requests/<int:request_id>", request_detail, ["GET", "POST"]),
    ("/requests/<int:request_id>/action", request_action, ["POST"]),
    ("/requests/<int:request_id>/resubmit", request_resubmit, ["GET", "POST"]),
    ("/requests/<int:request_id>/pdf", request_pdf, ["GET"]),
    ("/requests/<int:request_id>/tasks/<int:task_id>/print.pdf", task_print, ["GET"]),
    ("/requests/<int:request_id>/tasks/<int:task_id>/scan", task_scan, ["POST"]),
    ("/t/<token>", task_link, ["GET", "POST"]),
    ("/tasks/<int:task_id>", task_app, ["GET", "POST"]),
    ("/admin/tasks", admin_tasks, ["GET"]),
    ("/admin/outbox", admin_outbox, ["GET", "POST"]),
]
