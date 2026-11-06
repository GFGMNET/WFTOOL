"""The workflow engine: requests, steps, tasks and decisions.

Every public method runs in one database transaction: the state change, the
audit events and the notifier calls (which queue emails in phase 4) are saved
together or not at all.

Request states: open -> completed | returned (rejected, waiting for the
starter) -> open again on resubmit | cancelled.
Task states: open -> done | cancelled; sign tasks may pass through paper_pending.
"""

import hashlib
import json
import re
from datetime import date

from . import audit, clock, masterdata
from .crypto import canonical_json, fingerprint
from .definitions import (
    APPROVAL_STEP_TYPES, FILL_STEP_TYPES, LIST_FIELD_TYPES, REPEAT_MAX, fields_by_id,
    start_fields, step_fields, who_list,
)
from .errors import NotFound, ResolveError, StateError, ValidationError
from .notify import Notifier
from .workflows import get_version, latest_version

DECISIONS = {
    "fill": {"submitted"},
    "approve": {"approved", "rejected"},
    "fill_approve": {"approved", "rejected"},
    "approve_per_item": {"approved", "rejected"},
    "sign": {"signed", "rejected", "paper"},
    "confirm": {"confirmed"},
}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SECRET = "$enc"


class Actor:
    """Who did something, and from where (recorded on every decision)."""

    def __init__(self, email, ip=None, user_agent=None, via="app"):
        self.email = (email or "").strip().lower()
        self.ip = ip
        self.user_agent = user_agent
        self.via = via


class Engine:
    def __init__(self, db, cipher, notifier=None):
        self.db = db
        self.cipher = cipher
        self.notifier = notifier or Notifier()

    # ------------------------------------------------------------------ read

    def get_request(self, request_id):
        """Request with its definition, current form (decrypted) and all tasks."""
        req = self._request(request_id)
        req["form"], req["form_version"], req["form_fingerprint"] = self._current_form(request_id)
        req["tasks"] = [dict(t) for t in self.db.all(
            "SELECT * FROM tasks WHERE request_id = ? ORDER BY id", (request_id,))]
        return req

    def form_versions(self, request_id):
        rows = self.db.all("SELECT * FROM form_versions WHERE request_id = ? ORDER BY version", (request_id,))
        return [dict(r, data=self._decrypt(json.loads(r["data_json"]))) for r in rows]

    def open_tasks_for(self, email):
        return [dict(r) for r in self.db.all(
            "SELECT * FROM tasks WHERE assignee_email = ? AND status IN ('open', 'paper_pending') ORDER BY id",
            (email.strip(),))]

    def get_task(self, task_id):
        row = self.db.one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not row:
            raise NotFound(f"Task {task_id} not found.")
        return dict(row)

    # ----------------------------------------------------------------- start

    def start(self, workflow_name, actor, values):
        """Start a request of the latest published version. `actor` is the starter."""
        version = latest_version(self.db, workflow_name)
        definition = version["definition"]
        clean, _ = self._clean_values(definition, start_fields(definition), values, require=True)
        now = clock.now_iso()
        with self.db.tx() as c:
            number = self._next_number(c)
            cur = c.execute(
                "INSERT INTO requests (number, workflow_version_id, status, starter_email, current_step, round,"
                " created_at, updated_at) VALUES (?, ?, 'open', ?, NULL, 1, ?, ?)",
                (number, version["id"], actor.email, now, now),
            )
            rid = cur.lastrowid
            self._event("request.created", rid, actor, {
                "number": number, "workflow": definition["name"], "version": version["version"],
            })
            self._save_form(rid, clean, actor)
            self._advance(rid, 0)
        return self.get_request(rid)

    def _next_number(self, c):
        year = clock.now().year
        row = c.execute("SELECT number FROM requests WHERE number LIKE ? ORDER BY id DESC LIMIT 1",
                        (f"{year}-%",)).fetchone()
        n = int(row["number"].split("-")[1]) + 1 if row else 1
        return f"{year}-{n:04d}"

    # --------------------------------------------------------------- advance

    def _advance(self, rid, index):
        """Run steps from `index` until one creates tasks or the workflow ends."""
        req = self._request(rid)
        steps = req["definition"]["steps"]
        form, _, _ = self._current_form(rid)
        while index < len(steps):
            step = steps[index]
            if step.get("when") and not self._condition(step["when"], form, req["definition"]):
                self._event("step.skipped", rid, None, {"step": step["id"], "reason": "condition not met"})
                index += 1
                continue
            if step["type"] == "notify":
                recipients = []
                for who in who_list(step):
                    recipients.extend(self._resolve(req, step, who, form, allow_fallback=False))
                recipients = sorted(set(recipients))
                self.notifier.step_notify(req, step, recipients, bool(step.get("attach_pdf")))
                self._event("step.notified", rid, None, {
                    "step": step["id"], "to": recipients, "attach_pdf": bool(step.get("attach_pdf")),
                })
                index += 1
                continue

            assignments = self._assignments(req, step, form)
            if not assignments:
                self._event("step.skipped", rid, None, {"step": step["id"], "reason": "no items need approval"})
                index += 1
                continue
            self.db.execute("UPDATE requests SET current_step = ?, status = 'open', updated_at = ? WHERE id = ?",
                            (index, clock.now_iso(), rid))
            req["current_step"] = index
            created = [self._create_task(req, index, step, email, item, group)
                       for email, item, group in assignments]
            self._event("step.started", rid, None, {
                "step": step["id"], "type": step["type"],
                "tasks": [{"task": t["id"], "to": t["assignee_email"], "item": t["item_value"]} for t in created],
            })
            for t in created:
                self.notifier.task_opened(req, t)
            return

        now = clock.now_iso()
        self.db.execute("UPDATE requests SET status = 'completed', current_step = NULL, updated_at = ?,"
                        " closed_at = ? WHERE id = ?", (now, now, rid))
        req = self._request(rid)
        self._event("request.completed", rid, None, {})
        self.notifier.request_completed(req)

    def _assignments(self, req, step, form):
        """List of (email, item_value, group_name) for a task-creating step."""
        definition = req["definition"]
        who = who_list(step)[0]
        if step["type"] == "approve_per_item":
            field = fields_by_id(definition)[step["items"]]
            out = []
            for value in _as_list(form.get(step["items"])):
                item = masterdata.list_item(self.db, field["list"], value)
                if not item or not item["needs_approval"]:
                    continue
                approver = item["approver_email"]
                if not approver or not masterdata.get_user(self.db, approver):
                    approver = self._fallback(req, step, f"no active approver for item '{value}'")
                out.append((self._not_starter(req, step, approver), value, None))
            return out
        if who["kind"] == "group":
            members = self._resolve(req, step, who, form, allow_fallback=True)
            return [(m, None, who["group"]) for m in members]
        email = self._resolve(req, step, who, form, allow_fallback=True)[0]
        return [(self._not_starter(req, step, email), None, None)]

    def _resolve(self, req, step, who, form, *, allow_fallback):
        """Resolve one recipient spec to a list of active email addresses."""
        kind = who["kind"]
        found = []
        problem = None
        if kind == "person":
            found = [who["email"]]
        elif kind == "starter":
            found = [req["starter_email"]]
        elif kind == "field":
            value = form.get(who["field"])
            found = [value] if value else []
            problem = None if value else f"field '{who['field']}' is empty"
        elif kind == "manager_of":
            person = form.get(who["field"]) if who.get("field") else req["starter_email"]
            user = masterdata.get_user(self.db, person) if person else None
            manager = user and user.get("manager_email")
            found = [manager] if manager else []
            problem = None if manager else f"no manager found for {person or 'empty field'}"
        elif kind == "group":
            found = masterdata.group_members(self.db, who["group"])
            problem = None if found else f"group '{who['group']}' has no active members"

        active = []
        for email in found:
            if kind == "field" and not masterdata.get_user(self.db, email) and step["type"] == "notify":
                active.append(email.lower())  # notify may go to a plain email field (e.g. external)
            elif masterdata.get_user(self.db, email):
                active.append(email.lower())
            else:
                problem = f"{email} is not an active AD user"
        if active:
            return active
        if not allow_fallback:
            self._event("recipient.unresolved", req["id"], None, {"step": step["id"], "problem": problem})
            return []
        return [self._fallback(req, step, problem or "recipient could not be resolved")]

    def _fallback(self, req, step, problem):
        admin = (masterdata.get_setting(self.db, "admin_email") or "").strip().lower()
        if not admin:
            raise ResolveError(f"Step '{step['id']}': {problem}, and no admin email is set.")
        self._event("recipient.fallback_admin", req["id"], None, {"step": step["id"], "problem": problem,
                                                                  "to": admin})
        return admin

    def _not_starter(self, req, step, email):
        """The starter never approves their own request; such a task goes to the admin."""
        if step["type"] in APPROVAL_STEP_TYPES and email.lower() == req["starter_email"].lower():
            admin = (masterdata.get_setting(self.db, "admin_email") or "").strip().lower()
            if not admin or admin == email.lower():
                raise ResolveError(f"Step '{step['id']}': the starter cannot approve their own request,"
                                   " and no other admin email is set.")
            self._event("recipient.self_approval_redirected", req["id"], None,
                        {"step": step["id"], "from": email.lower(), "to": admin})
            return admin
        return email.lower()

    def _create_task(self, req, index, step, email, item, group):
        cur = self.db.execute(
            "INSERT INTO tasks (request_id, round, step_index, step_id, type, assignee_email, item_value,"
            " group_name, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
            (req["id"], req["round"], index, step["id"], step["type"], email, item, group, clock.now_iso()),
        )
        return self.get_task(cur.lastrowid)

    # --------------------------------------------------------------- decide

    def decide(self, task_id, decision, actor, *, reason=None, values=None, signature_image=None):
        """Record a decision on an open task by its assignee and move the request on.

        decision: submitted (fill), approved / rejected (approve types), signed / rejected / paper (sign),
        confirmed (confirm). A rejection needs a reason. A fill or fill_approve step's `values` are
        checked and saved as a new form version; a signed sign step needs `signature_image` (bytes).
        """
        with self.db.tx():
            task = self.get_task(task_id)
            req = self._request(task["request_id"])
            self._check_open(req, task)
            if actor.email != task["assignee_email"].lower():
                raise StateError("This task belongs to someone else.")
            step = req["definition"]["steps"][task["step_index"]]
            if decision not in DECISIONS[task["type"]]:
                raise StateError(f"'{decision}' is not possible for a {task['type']} step.")
            if decision == "rejected" and not (reason or "").strip():
                raise ValidationError("A rejection needs a reason.", details=["reason: required"])
            if decision == "paper":
                return self._request_paper(req, task, step, actor)

            if task["type"] in FILL_STEP_TYPES and decision != "rejected":
                fields = step_fields(req["definition"], task["step_index"])
                form, _, _ = self._current_form(req["id"])
                clean, hidden = self._clean_values(req["definition"], fields, values or {}, require=True,
                                                   context=form)
                form.update(clean)
                for key in hidden:
                    form.pop(key, None)
                self._save_form(req["id"], form, actor)

            _, form_version, form_fp = self._current_form(req["id"])
            signature_fp = None
            if decision == "signed":
                if not signature_image:
                    raise ValidationError("A signature is missing.", details=["signature: required"])
                signature_fp = fingerprint({"image": hashlib.sha256(signature_image).hexdigest(),
                                            "form": form_fp})

            self._close_task(task, decision, actor, reason, form_version, form_fp, signature_fp)
            if signature_fp:
                self._store_signature_value(req, step, actor, signature_fp, paper=False)
            return self._after_decision(req, task, step, decision, actor)

    def mark_paper(self, task_id, actor):
        """The starter marks a sign task as 'signature on paper'."""
        with self.db.tx():
            task = self.get_task(task_id)
            req = self._request(task["request_id"])
            self._check_open(req, task)
            if actor.email != req["starter_email"].lower():
                raise StateError("Only the person who started the request can switch to paper.")
            step = req["definition"]["steps"][task["step_index"]]
            return self._request_paper(req, task, step, actor)

    def _request_paper(self, req, task, step, actor):
        if task["type"] != "sign":
            raise StateError("Only a sign step can be signed on paper.")
        if not step.get("allow_paper", True):
            raise StateError("This step does not allow a paper signature.")
        self.db.execute("UPDATE tasks SET status = 'paper_pending' WHERE id = ?", (task["id"],))
        task = self.get_task(task["id"])
        self._event("task.paper_requested", req["id"], actor, {"task": task["id"], "step": step["id"]})
        self.notifier.task_closed(req, task, "signature on paper")
        self.notifier.paper_requested(req, task)
        return self.get_request(req["id"])

    def expire_task(self, task_id, days):
        """Called when a task's link expires without an answer (scheduler, phase 4).

        Step setting on_timeout: "wait" (default) keeps the task open and tells the
        starter and the admin; "reject" rejects it automatically, which then goes
        where the step's on_reject says. Paper signatures never expire.
        """
        system = Actor("system", via="timer")
        with self.db.tx():
            task = self.get_task(task_id)
            req = self._request(task["request_id"])
            if req["status"] != "open" or task["status"] != "open" or task["round"] != req["round"]:
                return self.get_request(req["id"])  # already decided, cancelled or on paper
            step = req["definition"]["steps"][task["step_index"]]
            if step.get("on_timeout", "wait") != "reject":
                self._event("task.expired", req["id"], system, {"task": task["id"], "step": step["id"],
                                                                "days": days, "action": "wait"})
                self.notifier.task_expired(req, task)
                return self.get_request(req["id"])
            reason = (f"Keine Antwort innerhalb von {days} Tagen / "
                      f"No response within {days} days")
            _, form_version, form_fp = self._current_form(req["id"])
            self._event("task.expired", req["id"], system, {"task": task["id"], "step": step["id"],
                                                            "days": days, "action": "reject"})
            self._close_task(task, "rejected", system, reason, form_version, form_fp, None)
            return self._after_decision(req, task, step, "rejected", system)

    def complete_paper(self, task_id, actor, scan_bytes):
        """The starter uploads the signed scan; the sign step completes as 'signed_paper'."""
        with self.db.tx():
            task = self.get_task(task_id)
            req = self._request(task["request_id"])
            if req["status"] != "open" or task["status"] != "paper_pending" or task["round"] != req["round"]:
                raise StateError("This task is not waiting for a paper signature.")
            if actor.email != req["starter_email"].lower():
                raise StateError("Only the person who started the request can upload the scan.")
            if not scan_bytes:
                raise ValidationError("The scan is missing.", details=["scan: required"])
            step = req["definition"]["steps"][task["step_index"]]
            _, form_version, form_fp = self._current_form(req["id"])
            scan_fp = hashlib.sha256(scan_bytes).hexdigest()
            signature_fp = fingerprint({"scan": scan_fp, "form": form_fp})
            self._close_task(task, "signed_paper", actor, None, form_version, form_fp, signature_fp)
            self._event("task.paper_uploaded", req["id"], actor, {"task": task["id"], "scan_fingerprint": scan_fp})
            self._store_signature_value(req, step, actor, signature_fp, paper=True)
            return self._after_decision(req, task, step, "signed_paper", actor)

    def _close_task(self, task, decision, actor, reason, form_version, form_fp, signature_fp):
        now = clock.now_iso()
        self.db.execute(
            "UPDATE tasks SET status = 'done', decision = ?, reason = ?, form_version = ?, form_fingerprint = ?,"
            " decided_by = ?, decided_at = ?, ip = ?, user_agent = ?, signature_fingerprint = ? WHERE id = ?",
            (decision, (reason or "").strip() or None, form_version, form_fp, actor.email, now,
             actor.ip, actor.user_agent, signature_fp, task["id"]),
        )
        self._event("task.decided", task["request_id"], actor, {
            "task": task["id"], "step": task["step_id"], "item": task["item_value"], "decision": decision,
            "reason": (reason or "").strip() or None, "form_version": form_version,
            "form_fingerprint": form_fp, "signature_fingerprint": signature_fp, "via": actor.via,
        })

    def _store_signature_value(self, req, step, actor, signature_fp, *, paper):
        sig_fields = [f["id"] for f in step_fields(req["definition"], req["definition"]["steps"].index(step))]
        if not sig_fields:
            return
        form, _, _ = self._current_form(req["id"])
        for fid in sig_fields:
            form[fid] = {"fingerprint": signature_fp, "by": actor.email, "at": clock.now_iso(), "paper": paper}
        self._save_form(req["id"], form, actor)

    def _after_decision(self, req, task, step, decision, actor):
        task = self.get_task(task["id"])
        req = self._request(req["id"])
        self.notifier.task_decided(req, task)
        if decision == "rejected":
            self._reject(req, task, step, actor)
            return self.get_request(req["id"])
        if task["group_name"]:
            self._cancel_step_tasks(req, task, f"done by {actor.email}")
        remaining = self.db.one(
            "SELECT COUNT(*) AS n FROM tasks WHERE request_id = ? AND round = ? AND step_index = ?"
            " AND status IN ('open', 'paper_pending')",
            (req["id"], req["round"], task["step_index"]),
        )["n"]
        if remaining == 0:
            self._advance(req["id"], task["step_index"] + 1)
        return self.get_request(req["id"])

    def _reject(self, req, task, step, actor):
        self._cancel_step_tasks(req, task, "request rejected")
        if step.get("on_reject") == "previous":
            row = self.db.one(
                "SELECT MAX(step_index) AS s FROM tasks WHERE request_id = ? AND round = ? AND step_index < ?",
                (req["id"], req["round"], task["step_index"]),
            )
            if row["s"] is not None:
                self._event("request.sent_back", req["id"], actor, {
                    "from_step": step["id"], "to_step": req["definition"]["steps"][row["s"]]["id"],
                    "reason": task["reason"],
                })
                self._advance(req["id"], row["s"])
                return
        self.db.execute("UPDATE requests SET status = 'returned', updated_at = ? WHERE id = ?",
                        (clock.now_iso(), req["id"]))
        req = self._request(req["id"])
        self._event("request.returned", req["id"], actor, {"step": step["id"], "reason": task["reason"]})
        self.notifier.request_returned(req, task)

    def _cancel_step_tasks(self, req, done_task, reason):
        rows = self.db.all(
            "SELECT * FROM tasks WHERE request_id = ? AND round = ? AND step_index = ? AND id != ?"
            " AND status IN ('open', 'paper_pending')",
            (req["id"], req["round"], done_task["step_index"], done_task["id"]),
        )
        for row in rows:
            self._cancel_task(req, dict(row), reason)

    def _cancel_task(self, req, task, reason):
        self.db.execute("UPDATE tasks SET status = 'cancelled', cancel_reason = ? WHERE id = ?",
                        (reason, task["id"]))
        self._event("task.cancelled", req["id"], None, {"task": task["id"], "step": task["step_id"],
                                                        "reason": reason})
        self.notifier.task_closed(req, self.get_task(task["id"]), reason)

    def _check_open(self, req, task):
        if req["status"] != "open":
            raise StateError(f"Request {req['number']} is {req['status']}.")
        if task["status"] != "open" or task["round"] != req["round"]:
            raise StateError("This task is no longer open.")

    # ------------------------------------------------- resubmit, cancel, reassign

    def resubmit(self, request_id, actor, values):
        """The starter edits a returned request; all steps run again from the start."""
        with self.db.tx():
            req = self._request(request_id)
            if req["status"] != "returned":
                raise StateError(f"Request {req['number']} is {req['status']}, not returned.")
            if actor.email != req["starter_email"].lower():
                raise StateError("Only the person who started the request can resend it.")
            form, _, _ = self._current_form(request_id)
            clean, hidden = self._clean_values(req["definition"], start_fields(req["definition"]), values,
                                               require=True, context=form)
            form.update(clean)
            for key in hidden:
                form.pop(key, None)
            self.db.execute("UPDATE requests SET status = 'open', round = round + 1, updated_at = ? WHERE id = ?",
                            (clock.now_iso(), request_id))
            self._event("request.resubmitted", request_id, actor, {"round": req["round"] + 1})
            self._save_form(request_id, form, actor)
            self._advance(request_id, 0)
        return self.get_request(request_id)

    def cancel(self, request_id, actor, reason):
        """Cancel an open or returned request; every open task stops."""
        if not (reason or "").strip():
            raise ValidationError("Cancelling needs a reason.", details=["reason: required"])
        with self.db.tx():
            req = self._request(request_id)
            if req["status"] not in ("open", "returned"):
                raise StateError(f"Request {req['number']} is {req['status']}.")
            for row in self.db.all("SELECT * FROM tasks WHERE request_id = ? AND status IN ('open', 'paper_pending')",
                                   (request_id,)):
                self._cancel_task(req, dict(row), "request cancelled")
            now = clock.now_iso()
            self.db.execute("UPDATE requests SET status = 'cancelled', updated_at = ?, closed_at = ? WHERE id = ?",
                            (now, now, request_id))
            req = self._request(request_id)
            self._event("request.cancelled", request_id, actor, {"reason": reason.strip()})
            self.notifier.request_cancelled(req, reason.strip())
        return self.get_request(request_id)

    def reassign(self, task_id, new_email, actor, reason):
        """Move an open task to another active user (absence, user left)."""
        if not (reason or "").strip():
            raise ValidationError("Reassigning needs a reason.", details=["reason: required"])
        with self.db.tx():
            task = self.get_task(task_id)
            req = self._request(task["request_id"])
            self._check_open(req, task)
            if not masterdata.get_user(self.db, new_email):
                raise ValidationError(f"{new_email} is not an active AD user.", details=["email: unknown"])
            new_email = new_email.strip().lower()
            if task["type"] in APPROVAL_STEP_TYPES and new_email == req["starter_email"].lower():
                raise StateError("The starter cannot approve their own request.")
            step = req["definition"]["steps"][task["step_index"]]
            self._cancel_task(req, task, f"reassigned to {new_email}")
            new = self._create_task(req, task["step_index"], step, new_email, task["item_value"], task["group_name"])
            self._event("task.reassigned", req["id"], actor, {
                "from_task": task["id"], "to_task": new["id"], "from": task["assignee_email"], "to": new_email,
                "reason": reason.strip(),
            })
            self.notifier.task_opened(req, new)
        return self.get_request(req["id"])

    # ----------------------------------------------------------------- forms

    def _save_form(self, rid, data, actor):
        row = self.db.one("SELECT MAX(version) AS v FROM form_versions WHERE request_id = ?", (rid,))
        version = (row["v"] or 0) + 1
        fp = fingerprint(data)
        stored = self._encrypt(rid, data)
        self.db.execute(
            "INSERT INTO form_versions (request_id, version, data_json, fingerprint, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (rid, version, canonical_json(stored), fp, actor.email if actor else "system", clock.now_iso()),
        )
        self._event("form.saved", rid, actor, {"version": version, "fingerprint": fp,
                                               "fields": sorted(data.keys())})
        return version, fp

    def _current_form(self, rid):
        row = self.db.one("SELECT * FROM form_versions WHERE request_id = ? ORDER BY version DESC LIMIT 1", (rid,))
        if not row:
            return {}, 0, None
        return self._decrypt(json.loads(row["data_json"])), row["version"], row["fingerprint"]

    def _encrypt(self, rid, data):
        """Secret values, also those inside the rows of a repeatable section, become {"$enc": token}."""
        definition = self._request(rid)["definition"]
        secrets = {f["id"] for f in definition["fields"] if f["type"] == "secret"}

        def enc(values):
            out = {}
            for k, v in values.items():
                if k in secrets and v not in (None, ""):
                    out[k] = {_SECRET: self.cipher.encrypt_text(v)}
                elif isinstance(v, list) and v and all(isinstance(row, dict) for row in v):
                    out[k] = [enc(row) for row in v]
                else:
                    out[k] = v
            return out

        return enc(data)

    def _decrypt(self, data):
        def dec(v):
            if isinstance(v, dict) and _SECRET in v:
                return self.cipher.decrypt_text(v[_SECRET])
            if isinstance(v, list) and v and all(isinstance(row, dict) for row in v):
                return [{k: dec(x) for k, x in row.items()} for row in v]
            return v
        return {k: dec(v) for k, v in data.items()}

    def _clean_values(self, definition, fields, values, *, require, context=None):
        """Check and normalise values for `fields`; raises ValidationError listing every problem.

        Returns (clean, hidden): the values to store, and the keys of fields in hidden
        sections, which are not stored (and are removed if an earlier version had them).
        A section's condition is evaluated on `context` (the form so far) plus the clean values.
        """
        values = values or {}
        clean, hidden, problems = {}, [], []
        sections = [f for f in fields if f["type"] == "section"]
        for f in fields:
            if f["type"] in ("heading", "signature", "section") or f.get("section"):
                continue
            self._clean_into(f, values.get(f["id"], f.get("default")), require, clean, problems, f["id"])

        known = dict(context or {})
        known.update(clean)
        for sec in sections:
            sid = sec["id"]
            members = [f for f in fields if f.get("section") == sid and f["type"] not in ("heading", "signature")]
            if not members:
                continue
            if sec.get("show_when") and not self._condition(sec["show_when"], known, definition):
                hidden.extend([sid] + [m["id"] for m in members])
                continue
            if not sec.get("repeat"):
                for f in members:
                    self._clean_into(f, values.get(f["id"], f.get("default")), require, clean, problems, f["id"])
                continue
            rows = []
            for n, raw_row in enumerate(_rows(values.get(sid)), start=1):
                if all(_blank(raw_row.get(f["id"])) for f in members):
                    continue  # an empty row the person added and left empty
                row = {}
                for f in members:
                    self._clean_into(f, raw_row.get(f["id"], f.get("default")), require, row, problems,
                                     f"{sid}[{n}].{f['id']}")
                rows.append(row)
            low = int(sec["repeat"].get("min") or 0)
            high = int(sec["repeat"].get("max") or REPEAT_MAX)
            if require and len(rows) < low:
                problems.append(f"{sid}: at least {low}")
            if len(rows) > high:
                problems.append(f"{sid}: at most {high}")
            clean[sid] = rows
        if problems:
            raise ValidationError("Please correct the form.", details=problems)
        return clean, hidden

    def _clean_into(self, f, raw, require, out, problems, where):
        try:
            value = self._clean_one(f, raw)
        except ValueError as exc:
            problems.append(f"{where}: {exc}")
            return
        if require and f.get("required") and _empty(value, f["type"]):
            problems.append(f"{where}: required")
            return
        out[f["id"]] = value

    def _clean_one(self, f, raw):
        t = f["type"]
        if raw is None or (isinstance(raw, str) and not raw.strip() and t != "checkbox"):
            return [] if t == "multichoice" else (False if t == "checkbox" else None)
        if t in ("text", "longtext", "secret"):
            text = str(raw).strip() if t != "secret" else str(raw)
            if f.get("max_length") and len(text) > int(f["max_length"]):
                raise ValueError(f"at most {f['max_length']} characters")
            return text
        if t == "number":
            try:
                number = float(str(raw).replace(",", "."))
            except ValueError:
                raise ValueError("not a number") from None
            if f.get("min") is not None and number < float(f["min"]):
                raise ValueError(f"at least {f['min']}")
            if f.get("max") is not None and number > float(f["max"]):
                raise ValueError(f"at most {f['max']}")
            return int(number) if number.is_integer() else number
        if t == "date":
            try:
                return date.fromisoformat(str(raw)).isoformat()
            except ValueError:
                raise ValueError("not a date (YYYY-MM-DD)") from None
        if t == "checkbox":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("1", "true", "yes", "on", "ja")
        if t in LIST_FIELD_TYPES:
            allowed = self._allowed_values(f)
            chosen = _as_list(raw)
            bad = [v for v in chosen if v not in allowed]
            if bad:
                raise ValueError(f"not in the list: {', '.join(map(str, bad))}")
            if t == "dropdown":
                if len(chosen) > 1:
                    raise ValueError("choose one")
                return chosen[0] if chosen else None
            return list(dict.fromkeys(chosen))
        if t == "email":
            email = str(raw).strip().lower()
            if not _EMAIL.match(email):
                raise ValueError("not an email address")
            if not masterdata.email_allowed(self.db, email):
                raise ValueError("not a company email address")
            return email
        if t == "person":
            email = str(raw).strip().lower()
            if not masterdata.get_user(self.db, email):
                raise ValueError("not an active AD user")
            return email
        raise ValueError(f"unsupported field type {t}")

    def _allowed_values(self, f):
        if f.get("options"):
            return {str(o["value"]) for o in f["options"]}
        return {i["value"] for i in masterdata.list_items(self.db, f["list"])}

    # ------------------------------------------------------------ conditions

    def _condition(self, cond, form, definition):
        op = cond["op"]
        value = form.get(cond["field"])
        target = cond.get("value")
        if op == "is_set":
            return not _empty(value, None)
        if op == "is_empty":
            return _empty(value, None)
        if op == "any_needs_approval":
            field = fields_by_id(definition)[cond["field"]]
            for v in _as_list(value):
                item = masterdata.list_item(self.db, field["list"], v)
                if item and item["active"] and item["needs_approval"]:
                    return True
            return False
        if op in ("equals", "not_equals"):
            if isinstance(value, bool):
                equal = value == (str(target).strip().lower() in ("1", "true", "yes", "on", "ja"))
            elif isinstance(value, list):
                equal = sorted(map(str, value)) == sorted(map(str, _as_list(target)))
            else:
                equal = str(value if value is not None else "").strip().lower() == str(target).strip().lower()
            return equal if op == "equals" else not equal
        if op in ("contains", "not_contains"):
            if isinstance(value, list):
                found = str(target) in map(str, value)
            else:
                found = str(target).lower() in str(value or "").lower()
            return found if op == "contains" else not found
        return False

    # --------------------------------------------------------------- helpers

    def _request(self, rid):
        row = self.db.one("SELECT * FROM requests WHERE id = ?", (rid,))
        if not row:
            raise NotFound(f"Request {rid} not found.")
        req = dict(row)
        version = get_version(self.db, req["workflow_version_id"])
        req["definition"] = version["definition"]
        req["workflow"] = version["definition"]["name"]
        req["workflow_version"] = version["version"]
        return req

    def _event(self, type_, rid, actor, data):
        audit.record(self.db, type_, request_id=rid,
                     actor=actor.email if actor else "system",
                     ip=actor.ip if actor else None,
                     user_agent=actor.user_agent if actor else None, data=data)


def _as_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _rows(value):
    """Rows of a repeatable section: a list of dicts, or a dict keyed by row number (web forms)."""
    if isinstance(value, dict):
        value = [value[k] for k in sorted(value, key=lambda k: int(k) if str(k).isdigit() else 0)]
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _blank(raw):
    return raw is None or raw is False or raw == [] or (isinstance(raw, str) and not raw.strip())


def _empty(value, ftype):
    if ftype == "checkbox":
        return value is not True
    return value is None or value == "" or value == [] or value is False

