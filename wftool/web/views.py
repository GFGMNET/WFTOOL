"""Routes for login and the admin pages."""

import secrets
from functools import wraps

from flask import (
    abort, current_app, flash, g, redirect, render_template, request, session, url_for,
)

from .. import accounts, applists, audit, clock, imports, masterdata, settings, workflows
from ..errors import WFError
from .i18n import translate


def _db():
    return current_app.extensions["wftool"]["db"]


def _cipher():
    return current_app.extensions["wftool"]["cipher"]


def t(text):
    return translate(text, getattr(g, "lang", "de"))


def register(app):
    app.jinja_env.globals.update(t=t, csrf_token=_csrf_token, label_of=_label_of)
    app.before_request(_load_user)
    app.before_request(_check_csrf)

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        return resp

    @app.errorhandler(403)
    def _forbidden(_):
        return render_template("error.html", message=t("You don't have access to this page.")), 403

    from . import builder
    for rule, view, methods in ROUTES + builder.ROUTES:
        app.add_url_rule(rule, view.__name__, view, methods=methods)


# --- request setup ----------------------------------------------------

def _load_user():
    g.user = None
    g.lang = session.get("lang", "de")
    account_id = session.get("account_id")
    if not account_id:
        return None
    seen = session.get("seen", 0)
    now = clock.now().timestamp()
    if now - seen > accounts.SESSION_MINUTES * 60:
        session.clear()
        flash(translate("You were signed out after 60 minutes without activity.", g.lang), "info")
        return None
    try:
        acc = accounts.get_account(_db(), account_id)
    except WFError:
        session.clear()
        return None
    if not acc["active"]:
        session.clear()
        return None
    session["seen"] = now
    g.user = acc
    g.lang = acc["language"]
    allowed = {"password", "logout", "static"}
    if acc["must_change_password"] and request.endpoint not in allowed:
        return redirect(url_for("password"))
    return None


def _csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _check_csrf():
    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            flash(t("This form expired. Please try again."), "error")
            return redirect(request.referrer or url_for("home"))
    return None


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def _client():
    return {"ip": request.remote_addr, "user_agent": (request.user_agent.string or "")[:300]}


def _flash_error(exc):
    flash(t(exc.message), "error")
    for d in exc.details:
        flash(d, "detail")


def _label_of(label):
    if isinstance(label, dict):
        return label.get(g.lang) or label.get("de") or label.get("en") or ""
    return label or ""


# --- login and password -----------------------------------------------

def login():
    if g.user:
        return redirect(url_for("home"))
    if request.method == "POST":
        try:
            acc = accounts.authenticate(_db(), request.form.get("username", ""), request.form.get("password", ""),
                                        **_client())
        except accounts.LoginError as exc:
            flash(t(exc.message), "error")
            return render_template("login.html", username=request.form.get("username", "")), 401
        csrf = session.get("csrf")
        session.clear()
        session.permanent = True
        session.update(account_id=acc["id"], seen=clock.now().timestamp(), lang=acc["language"], csrf=csrf)
        target = request.args.get("next", "")
        return redirect(target if target.startswith("/") and not target.startswith("//") else url_for("home"))
    return render_template("login.html", username="")


def logout():
    if g.user:
        audit.record(_db(), "logout", actor=g.user["username"], **_client(), data={})
    lang = session.get("lang", "de")
    session.clear()
    session["lang"] = lang
    return redirect(url_for("login"))


@login_required
def password():
    if request.method == "POST":
        new = request.form.get("new_password", "")
        if new != request.form.get("repeat_password", ""):
            flash(t("The two new passwords are different."), "error")
            return render_template("password.html"), 400
        try:
            accounts.change_password(_db(), g.user["id"], request.form.get("old_password", ""), new)
        except WFError as exc:
            _flash_error(exc)
            return render_template("password.html"), 400
        flash(t("Password changed."), "ok")
        return redirect(url_for("home"))
    return render_template("password.html")


def set_language(lang):
    if lang in accounts.LANGUAGES:
        session["lang"] = lang
        if g.user:
            accounts.update_account(_db(), g.user["id"], language=lang, by=g.user["username"])
    target = request.referrer or url_for("home")
    return redirect(target)


# --- home ---------------------------------------------------------------

@login_required
def home():
    db = _db()
    attention = {}
    if g.user["role"] == "admin":
        attention["missing"] = [settings.BY_KEY[k] for k in settings.missing_required(db)]
        last = {}
        for row in imports.history(db, 30):
            last.setdefault(row["kind"], row)
        attention["rejected_imports"] = [r for r in last.values() if r["status"] == "rejected"]
        admin = db.one("SELECT password_hash FROM accounts WHERE username = 'admin' AND active = 1")
        from werkzeug.security import check_password_hash
        attention["default_admin"] = bool(admin and check_password_hash(admin["password_hash"], "admin"))
    return render_template("home.html", startable=workflows.startable_workflows(db, g.user), attention=attention)


# --- accounts -----------------------------------------------------------

@admin_required
def admin_accounts():
    db = _db()
    if request.method == "POST":
        f = request.form
        try:
            accounts.create_account(db, f.get("username"), f.get("password"), f.get("role"),
                                    email=f.get("email"), language=f.get("language", "de"), by=g.user["username"])
            flash(t("Account created."), "ok")
            return redirect(url_for("admin_accounts"))
        except WFError as exc:
            _flash_error(exc)
    return render_template("admin/accounts.html", accounts=accounts.list_accounts(db), roles=accounts.ROLES,
                           now=clock.now_iso())


@admin_required
def admin_account_update(account_id):
    f = request.form
    try:
        if f.get("action") == "password":
            accounts.reset_password(_db(), account_id, f.get("password", ""), by=g.user["username"])
            flash(t("Temporary password set."), "ok")
        else:
            accounts.update_account(_db(), account_id, by=g.user["username"], role=f.get("role"),
                                    email=f.get("email"), language=f.get("language"),
                                    active=f.get("active") == "1")
            flash(t("Changes saved."), "ok")
    except WFError as exc:
        _flash_error(exc)
    return redirect(url_for("admin_accounts"))


# --- people (read-only) ---------------------------------------------------

@admin_required
def admin_people():
    db = _db()
    q = request.args.get("q", "").strip()
    show_inactive = request.args.get("inactive") == "1"
    sql = "SELECT * FROM ad_users WHERE 1 = 1"
    params = []
    if not show_inactive:
        sql += " AND active = 1"
    if q:
        sql += " AND (email LIKE ? OR display_name LIKE ? OR department LIKE ?)"
        params += [f"%{q}%"] * 3
    users = [dict(r) for r in db.all(sql + " ORDER BY display_name LIMIT 500", params)]
    groups = []
    for grp in db.all("SELECT * FROM groups ORDER BY name"):
        if not grp["active"] and not show_inactive:
            continue
        members = [r["member_email"] for r in db.all(
            "SELECT member_email FROM group_members WHERE group_name = ? AND active = 1 ORDER BY member_email",
            (grp["name"],))]
        groups.append(dict(grp, members=members))
    return render_template("admin/people.html", users=users, groups=groups, q=q, show_inactive=show_inactive)


# --- lists ---------------------------------------------------------------

@admin_required
def admin_lists():
    db = _db()
    if request.method == "POST":
        try:
            lst = applists.create_list(db, request.form.get("name"), request.form.get("label"), by=g.user["username"])
            return redirect(url_for("admin_list", name=lst["name"]))
        except WFError as exc:
            _flash_error(exc)
    return render_template("admin/lists.html", lists=applists.all_lists(db))


@admin_required
def admin_list(name):
    db = _db()
    lst = masterdata.get_list(db, name)
    if not lst:
        abort(404)
    if request.method == "POST":
        f = request.form
        by = g.user["username"]
        try:
            action = f.get("action")
            item_id = int(f.get("item_id", 0) or 0)
            if action == "add":
                applists.add_item(db, name, f.get("label"), by=by)
            elif action == "rename":
                applists.rename_item(db, item_id, f.get("label"), by=by)
            elif action in ("activate", "deactivate"):
                applists.set_item_active(db, item_id, action == "activate", by=by)
            elif action in ("up", "down"):
                applists.move_item(db, item_id, -1 if action == "up" else 1, by=by)
            flash(t("Changes saved."), "ok")
        except WFError as exc:
            _flash_error(exc)
        return redirect(url_for("admin_list", name=name))
    items = masterdata.list_items(db, name, active_only=False)
    return render_template("admin/list.html", lst=lst, items=items)


# --- settings ---------------------------------------------------------------

@admin_required
def admin_settings():
    db = _db()
    errors = []
    if request.method == "POST":
        values = {s.key: request.form.get(s.key, "") for s in settings.SETTINGS}
        clear = [k for k in ("smtp_password",) if request.form.get(f"clear_{k}") == "1"]
        try:
            settings.save(db, _cipher(), values, by=g.user["username"], clear_secrets=clear)
            flash(t("Changes saved."), "ok")
            return redirect(url_for("admin_settings"))
        except WFError as exc:
            flash(t("Please correct the marked fields."), "error")
            errors = exc.details
    values = settings.current(db)
    if errors:
        values.update({k: v for k, v in request.form.items() if k in settings.BY_KEY and settings.BY_KEY[k].kind
                       != "secret"})
    error_keys = {e.split(":", 1)[0]: e.split(":", 1)[1].strip() for e in errors}
    groups = [("smtp", "Email (SMTP)"), ("general", "General"), ("deadlines", "Deadlines"), ("import", "Import")]
    return render_template("admin/settings.html", settings=settings.SETTINGS, values=values, groups=groups,
                           errors=error_keys)


@admin_required
def admin_settings_test():
    to = request.form.get("to", "").strip() or masterdata.get_setting(_db(), "admin_email", "")
    ok, message = settings.send_test_email(_db(), _cipher(), to, by=g.user["username"])
    flash(message, "ok" if ok else "error")
    return redirect(url_for("admin_settings"))


# --- imports ---------------------------------------------------------------

@admin_required
def admin_imports():
    db = _db()
    if request.method == "POST":
        results = imports.run_folder(db, current_app.config["IMPORT_DIR"])
        if not results:
            flash(t("No CSV files found in the import folder."), "info")
        for r in results:
            flash(f"{r['file']}: {t(r['status'])}" + (f" – {r['message']}" if r["message"] else ""),
                  "error" if r.rejected else "ok")
        return redirect(url_for("admin_imports"))
    return render_template("admin/imports.html", history=imports.history(db, 100),
                           folder=current_app.config["IMPORT_DIR"])


# --- who may start ------------------------------------------------------------

@admin_required
def admin_start_groups():
    db = _db()
    names = [r["name"] for r in db.all(
        "SELECT DISTINCT w.name FROM workflows w JOIN workflow_versions v ON v.workflow_id = w.id ORDER BY w.name")]
    ids = {r["name"]: r["id"] for r in db.all("SELECT id, name FROM workflows")}
    if request.method == "POST":
        name = request.form.get("workflow", "")
        if name in names:
            try:
                workflows.set_start_groups(db, name, request.form.getlist("groups"), by=g.user["username"])
                flash(t("Changes saved."), "ok")
            except WFError as exc:
                _flash_error(exc)
        return redirect(url_for("admin_start_groups"))
    all_groups = [r["name"] for r in db.all("SELECT name FROM groups WHERE active = 1 ORDER BY name")]
    rows = [{"name": n, "id": ids[n], "groups": workflows.start_groups(db, n)} for n in names]
    return render_template("admin/start_groups.html", rows=rows, all_groups=all_groups)


ROUTES = [
    ("/", home, ["GET"]),
    ("/login", login, ["GET", "POST"]),
    ("/logout", logout, ["POST"]),
    ("/password", password, ["GET", "POST"]),
    ("/lang/<lang>", set_language, ["GET"]),
    ("/admin/accounts", admin_accounts, ["GET", "POST"]),
    ("/admin/accounts/<int:account_id>", admin_account_update, ["POST"]),
    ("/admin/people", admin_people, ["GET"]),
    ("/admin/lists", admin_lists, ["GET", "POST"]),
    ("/admin/lists/<name>", admin_list, ["GET", "POST"]),
    ("/admin/settings", admin_settings, ["GET", "POST"]),
    ("/admin/settings/test-email", admin_settings_test, ["POST"]),
    ("/admin/imports", admin_imports, ["GET", "POST"]),
    ("/admin/start", admin_start_groups, ["GET", "POST"]),
]
