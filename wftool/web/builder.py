"""Form designer and workflow builder (phase 3).

The builder page is rendered by the server with the draft and the master data
it needs as JSON; `static/builder.js` turns that into the designer. Saving
posts the whole definition as JSON in a normal form (CSRF-protected), so the
"Advanced: JSON" box also works without JavaScript. `/check` answers the
designer's live checks without saving anything.
"""

import json
import re

from flask import Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .. import emails, masterdata, workflows
from ..definitions import Problem, publish_problems
from ..errors import NotFound, WFError
from .i18n import DE, translate
from .views import _db, _flash_error, admin_required, t

MAX_IMPORT_BYTES = 2 * 1024 * 1024


def _problem_dicts(problems):
    out = []
    for p in problems:
        d = p.as_dict() if isinstance(p, Problem) else {"kind": "workflow", "index": None, "ref": None,
                                                         "template": str(p), "args": {}, "text": str(p)}
        d["message"] = _format(translate(d["template"], g.lang), d["args"])
        out.append(d)
    return out


def _format(template, args):
    try:
        return template.format(**args)
    except (KeyError, IndexError, ValueError):
        return template


def _check(definition):
    """Problems as the builder shows them: structure first, then (when that is fine) master data."""
    return publish_problems(_db(), definition)


def _builder_data(w):
    db = _db()
    lists = []
    for row in db.all("SELECT name, label, source FROM lists ORDER BY label"):
        items = [{"value": i["value"], "label": i["label"], "needs_approval": bool(i["needs_approval"])}
                 for i in masterdata.list_items(db, row["name"])]
        lists.append({"name": row["name"], "label": row["label"], "source": row["source"], "items": items})
    groups = [r["name"] for r in db.all("SELECT name FROM groups WHERE active = 1 ORDER BY name")]
    users = [{"email": r["email"], "name": r["display_name"]}
             for r in db.all("SELECT email, display_name FROM ad_users WHERE active = 1 ORDER BY display_name")]
    return {
        "definition": w["draft"] or {"name": w["name"], "fields": [], "steps": []},
        "problems": _problem_dicts(_check(w["draft"] or {"name": w["name"], "fields": [], "steps": []})),
        "lists": lists,
        "groups": groups,
        "users": users,
        "emailDefaults": emails.DEFAULTS,
        "placeholders": {k: v.get(g.lang) or v["en"] for k, v in emails.PLACEHOLDERS.items()},
        "lang": g.lang,
        "strings": DE if g.lang == "de" else {},
        "checkUrl": url_for("admin_workflow_check", workflow_id=w["id"]),
    }


@admin_required
def admin_workflows():
    db = _db()
    if request.method == "POST":
        f = request.form
        try:
            wid = workflows.create_workflow(db, f.get("name"), {"de": f.get("label_de"), "en": f.get("label_en")},
                                            by=g.user["username"])
            flash(t("Workflow created. Add fields and steps, then publish."), "ok")
            return redirect(url_for("admin_workflow", workflow_id=wid))
        except WFError as exc:
            _flash_error(exc)
    return render_template("admin/workflows.html", rows=workflows.list_workflows(db))


@admin_required
def admin_workflow_import():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash(t("Choose a workflow file (.json) first."), "error")
        return redirect(url_for("admin_workflows"))
    data = upload.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        flash(t("The file is too large for a workflow (max. 2 MB)."), "error")
        return redirect(url_for("admin_workflows"))
    db = _db()
    try:
        text = data.decode("utf-8-sig")
        parsed = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        flash(t("The file is not a valid workflow file (JSON, UTF-8)."), "error")
        return redirect(url_for("admin_workflows"))
    name = str(parsed.get("name", "")).strip() if isinstance(parsed, dict) else ""
    existed = bool(name and db.one("SELECT id FROM workflows WHERE name = ?", (name,)))
    try:
        workflows.import_workflow(db, text, by=g.user["username"], strict=False)
    except WFError as exc:
        _flash_error(exc)
        return redirect(url_for("admin_workflows"))
    w = workflows.get_workflow(db, name)
    flash(t("Imported as draft; the previous draft was replaced. Check it, then publish.") if existed
          else t("Imported as draft. Check it, then publish."), "ok")
    return redirect(url_for("admin_workflow", workflow_id=w["id"]))


@admin_required
def admin_workflow(workflow_id):
    db = _db()
    try:
        w = workflows.get_workflow_by_id(db, workflow_id)
    except NotFound:
        abort(404)
    if request.method == "POST":
        return _save(w)
    versions = workflows.versions(db, workflow_id)
    data = _builder_data(w)
    ready = not data["problems"]
    return render_template("admin/builder.html", w=w, versions=versions, ready=ready,
                           problem_count=len(data["problems"]), changed=_changed(w, versions),
                           data_json=_script_json(data),
                           definition_text=json.dumps(data["definition"], indent=2, ensure_ascii=False))


def _changed(w, versions):
    from ..crypto import fingerprint
    return bool(w["draft"] is not None and (not versions or fingerprint(w["draft"]) != versions[0]["fingerprint"]))


def _save(w):
    db = _db()
    action = request.form.get("action", "save")
    by = g.user["username"]
    target = url_for("admin_workflow", workflow_id=w["id"]) + _tab_anchor()
    if action == "discard":
        try:
            workflows.discard_draft(db, w["id"], by=by)
            flash(t("Unpublished changes discarded."), "ok")
        except WFError as exc:
            _flash_error(exc)
        return redirect(target)
    try:
        definition = json.loads(request.form.get("definition", ""))
    except json.JSONDecodeError as exc:
        flash(t("The JSON is not valid:") + f" {exc}", "error")
        return redirect(target)
    try:
        w = workflows.update_draft(db, w["id"], definition, by=by, base=request.form.get("base") or None)
    except WFError as exc:
        _flash_error(exc)
        return redirect(target)
    if action != "publish":
        flash(t("Draft saved."), "ok")
        return redirect(target)
    try:
        before = workflows.versions(db, w["id"])
        version = workflows.publish(db, w["name"], by=by)
    except WFError as exc:
        count = len(exc.details)
        flash(t("Draft saved, but not published: {n} problem(s) are marked below.").format(n=count), "error")
        return redirect(url_for("admin_workflow", workflow_id=w["id"]) + "#check")
    if before and before[0]["version"] == version["version"]:
        flash(t("Nothing changed since version {v}; no new version was made.").format(v=version["version"]), "info")
    else:
        flash(t("Published as version {v}. Running requests keep their version.").format(v=version["version"]),
              "ok")
    return redirect(target)


def _tab_anchor():
    tab = request.form.get("tab", "")
    return f"#{tab}" if re.fullmatch(r"[a-z]{1,12}", tab or "") else ""


@admin_required
def admin_workflow_check(workflow_id):
    """Live check of the posted definition; nothing is saved."""
    try:
        workflows.get_workflow_by_id(_db(), workflow_id)
        definition = json.loads(request.form.get("definition", ""))
    except NotFound:
        abort(404)
    except json.JSONDecodeError:
        return jsonify({"ok": False, "problems": [], "error": t("The JSON is not valid:")}), 400
    if not isinstance(definition, dict):
        return jsonify({"ok": False, "problems": [], "error": t("The workflow file must contain one JSON object.")}), 400
    problems = _problem_dicts(_check(definition))
    return jsonify({"ok": not problems, "problems": problems})


@admin_required
def admin_workflow_export(workflow_id):
    db = _db()
    try:
        w = workflows.get_workflow_by_id(db, workflow_id)
        text = workflows.export_workflow(db, w["name"])
    except NotFound:
        abort(404)
    slug = re.sub(r"[^a-z0-9]+", "_", w["name"].lower()).strip("_") or "workflow"
    return Response(text + "\n", mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{slug}.json"'})


def _script_json(data):
    """JSON safe to place inside <script type="application/json">."""
    return (json.dumps(data, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


ROUTES = [
    ("/admin/workflows", admin_workflows, ["GET", "POST"]),
    ("/admin/workflows/import", admin_workflow_import, ["POST"]),
    ("/admin/workflows/<int:workflow_id>", admin_workflow, ["GET", "POST"]),
    ("/admin/workflows/<int:workflow_id>/check", admin_workflow_check, ["POST"]),
    ("/admin/workflows/<int:workflow_id>/export", admin_workflow_export, ["GET"]),
]
