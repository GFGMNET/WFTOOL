"""The Notifier of phase 4: links and emails through the outbox.

The engine calls these hooks inside its transaction, so a link and its email
exist if and only if the state change is saved. Emails to the people who act
(approvers, signers, ...) are German, as decided; emails to the person who
started the request use that person's account language; Notify steps use the
default email language from Settings.
"""

from . import emails, links, masterdata, outbox, pdf, present
from .definitions import label_text
from .notify import Notifier

TASK_LANG = "de"


class OutboxNotifier(Notifier):
    def __init__(self, db, files):
        self.db = db
        self.files = files
        self.engine = None

    def bind(self, engine):
        self.engine = engine

    # ------------------------------------------------------------ helpers

    def base_url(self):
        return (masterdata.get_setting(self.db, "base_url", "") or "http://localhost:8080").rstrip("/")

    def request_url(self, req):
        return f"{self.base_url()}/requests/{req['id']}"

    def default_lang(self):
        lang = masterdata.get_setting(self.db, "default_email_language", "de")
        return lang if lang in ("de", "en") else "de"

    def lang_of(self, email):
        row = self.db.one("SELECT language FROM accounts WHERE email = ? AND active = 1 ORDER BY id LIMIT 1",
                          (email,))
        return row["language"] if row and row["language"] in ("de", "en") else self.default_lang()

    def admin(self):
        return (masterdata.get_setting(self.db, "admin_email", "") or "").strip().lower()

    def _full(self, req):
        """The request with form and tasks (the engine passes the bare row)."""
        return self.engine.get_request(req["id"]) if self.engine else req

    def _context(self, req, lang, step=None, **extra):
        full = self._full(req)
        definition = full["definition"]
        ctx = present.placeholder_values(self.db, definition, full.get("form") or {}, lang)
        ctx.update({
            "request_no": full["number"],
            "workflow": label_text(definition.get("label"), lang) or definition["name"],
            "step": label_text(step.get("label"), lang) or step["id"] if step else "",
            "starter": present.person_name(self.db, full["starter_email"]),
            "url": self.request_url(full),
        })
        ctx.update(extra)
        return full, ctx

    def _details(self, full, lang):
        return present.flat(present.rows(self.db, full["definition"], full.get("form") or {}, lang))

    def _send(self, to, subject, text, html, req, kind, attachment=None, attachment_name=None):
        if not to:
            return
        outbox.queue(self.db, to=to, subject=subject, text=text, html=html, request_id=req["id"], kind=kind,
                     attachment=attachment, attachment_name=attachment_name, files=self.files)

    def _system(self, kind, to, req, step=None, lang=None, **extra):
        lang = lang or self.lang_of(to)
        full, ctx = self._context(req, lang, step, **extra)
        subject, body = emails.system_texts(kind, lang, ctx)
        html = emails.html_email(lang, body, title=subject, button=(ctx["url"], emails.BUTTON["request"][lang]))
        self._send(to, subject, body, html, full, kind)

    def _step(self, req, task):
        return req["definition"]["steps"][task["step_index"]]

    # -------------------------------------------------------------- tasks

    def task_opened(self, request, task):
        self._task_email(request, task, reminder=False)

    def task_resent(self, request, task, reason):
        """Resend or reminder: a new link replaces the old one."""
        self._task_email(request, task, reminder=(reason == "reminder"))

    def _task_email(self, req, task, *, reminder):
        token = links.create(self.db, task)
        lang = TASK_LANG
        step = self._step(req, task)
        url = f"{self.base_url()}/t/{token}"
        full, ctx = self._context(req, lang, step, link=url)
        subject, body = (emails.render(t, ctx) for t in emails.texts_for(step, lang))
        if task["item_value"]:
            subject = f"{subject} – {task['item_value']}"
        if reminder:
            subject = emails.REMINDER_PREFIX[lang] + subject
        html = emails.html_email(lang, body, button=(url, emails.BUTTON["task"][lang]),
                                 details=self._details(full, lang), title=subject)
        self._send(task["assignee_email"], subject, body, html, full, "reminder" if reminder else "task")

    def task_closed(self, request, task, reason):
        links.revoke_for_task(self.db, task["id"], links.CLOSED)

    # --------------------------------------------------------------- steps

    def step_notify(self, request, step, recipients, attach_pdf):
        lang = self.default_lang()
        full, ctx = self._context(request, lang, step)
        subject, body = (emails.render(t, ctx) for t in emails.texts_for(step, lang))
        attachment = pdf.request_pdf(self.db, full, lang, files=self.files) if attach_pdf else None
        name = f"{full['number']}.pdf" if attach_pdf else None
        html = emails.html_email(lang, body, details=self._details(full, lang), title=subject)
        for to in recipients:
            self._send(to, subject, body, html, full, "notify", attachment, name)

    def task_decided(self, request, task):
        req = request
        if not task["decided_by"] or task["decided_by"].lower() == req["starter_email"].lower():
            return  # the starter did it themselves
        if task["decision"] == "rejected":
            return  # request_returned (or the step going back) tells the starter
        lang = self.lang_of(req["starter_email"])
        decision = present.name_of(present.DECISION_NAMES, task["decision"], lang)
        if task["item_value"]:
            decision += f" – {task['item_value']}"
        self._system("task_decided", req["starter_email"], req, self._step(req, task), lang,
                     decision=decision, person=present.person_name(self.db, task["decided_by"]))

    def request_returned(self, request, task):
        self._system("request_returned", request["starter_email"], request, self._step(request, task),
                     person=present.person_name(self.db, task["decided_by"]), reason=task["reason"] or "")

    def request_completed(self, request):
        self._system("request_completed", request["starter_email"], request)

    def request_cancelled(self, request, reason):
        self._system("request_cancelled", request["starter_email"], request, reason=reason)

    def paper_requested(self, request, task):
        self._system("paper_requested", request["starter_email"], request, self._step(request, task),
                     person=present.person_name(self.db, task["assignee_email"]))

    def task_expired(self, request, task):
        person = present.person_name(self.db, task["assignee_email"])
        step = self._step(request, task)
        self._system("task_expired", request["starter_email"], request, step, person=person)
        admin = self.admin()
        if admin and admin != request["starter_email"].lower():
            self._system("task_expired", admin, request, step, person=person)

    def starter_notice(self, request, task, days):
        self._system("starter_notice", request["starter_email"], request, self._step(request, task),
                     person=present.person_name(self.db, task["assignee_email"]), days=days)

    def paper_reminder(self, request, task):
        self._system("paper_reminder", request["starter_email"], request, self._step(request, task),
                     person=present.person_name(self.db, task["assignee_email"]))
