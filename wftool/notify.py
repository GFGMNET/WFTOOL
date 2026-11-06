"""Hooks the engine calls when something needs an email.

Phase 1 only defines the interface. Phase 4 implements it with single-use links
and the outbox. Every hook runs inside the engine's transaction, so an email is
queued if and only if the state change is saved.
"""


class Notifier:
    def task_opened(self, request, task):
        """A person has a new task (approve, sign, fill, confirm): send them a link."""

    def task_closed(self, request, task, reason):
        """An open task was cancelled or replaced: its links must stop working."""

    def step_notify(self, request, step, recipients, attach_pdf):
        """A notify step: send an email (with the PDF if `attach_pdf`) to `recipients`."""

    def task_decided(self, request, task):
        """A task was decided: tell the person who started the request."""

    def request_returned(self, request, task):
        """The request was rejected and goes back to the starter."""

    def request_completed(self, request):
        """All steps are done."""

    def request_cancelled(self, request, reason):
        """The request was cancelled."""

    def paper_requested(self, request, task):
        """A signature will be given on paper: tell the starter to print and later upload."""

    def task_expired(self, request, task):
        """A task's link expired and the step waits: tell the starter and the admin."""

    def task_resent(self, request, task, reason):
        """Send the task again with a new link (reason 'resent' or 'reminder'); the old link stops working."""

    def starter_notice(self, request, task, days):
        """A task has been open for `days` working days: tell the starter."""

    def paper_reminder(self, request, task):
        """A paper signature is still waiting for its scan: remind the starter."""


class RecordingNotifier(Notifier):
    """Keeps every call in `calls` as (hook, details); used by the tests."""

    def __init__(self):
        self.calls = []

    def _add(self, hook, **details):
        self.calls.append((hook, details))

    def task_opened(self, request, task):
        self._add("task_opened", request=request["number"], task=task["id"], to=task["assignee_email"],
                  type=task["type"], item=task["item_value"])

    def task_closed(self, request, task, reason):
        self._add("task_closed", request=request["number"], task=task["id"], reason=reason)

    def step_notify(self, request, step, recipients, attach_pdf):
        self._add("step_notify", request=request["number"], step=step["id"], to=list(recipients),
                  attach_pdf=attach_pdf)

    def task_decided(self, request, task):
        self._add("task_decided", request=request["number"], task=task["id"], decision=task["decision"])

    def request_returned(self, request, task):
        self._add("request_returned", request=request["number"], reason=task["reason"])

    def request_completed(self, request):
        self._add("request_completed", request=request["number"])

    def request_cancelled(self, request, reason):
        self._add("request_cancelled", request=request["number"], reason=reason)

    def paper_requested(self, request, task):
        self._add("paper_requested", request=request["number"], task=task["id"])

    def task_expired(self, request, task):
        self._add("task_expired", request=request["number"], task=task["id"])

    def task_resent(self, request, task, reason):
        self._add("task_resent", request=request["number"], task=task["id"], reason=reason)

    def starter_notice(self, request, task, days):
        self._add("starter_notice", request=request["number"], task=task["id"], days=days)

    def paper_reminder(self, request, task):
        self._add("paper_reminder", request=request["number"], task=task["id"])

    def hooks(self, name):
        return [d for h, d in self.calls if h == name]
