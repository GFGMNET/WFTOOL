"""Helpers for the end-to-end suite: the app (forms with CSRF) and the mailbox (Mailpit API)."""

import json
import os
import re
import time
import urllib.request
from pathlib import Path

BASE = os.environ.get("WFTOOL_E2E_URL", "http://127.0.0.1:8080").rstrip("/")
MAILPIT = os.environ.get("MAILPIT_URL", "http://127.0.0.1:8025").rstrip("/")
DATA = Path(os.environ.get("WFTOOL_E2E_DATA", "data"))
ADMIN_PASSWORD = os.environ.get("WFTOOL_E2E_ADMIN_PASSWORD", "test-admin-password")
ROOT = Path(__file__).resolve().parent.parent
RESULTS = Path(os.environ.get("E2E_RESULTS", ROOT / "test-results"))
STARTER_PASSWORD = "starter-pass-123"
TICKER = {"page": None}  # the admin's browser; only admins may run /test/tick


def wait_for_app(timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(BASE + "/login", timeout=3)
            return
        except OSError:
            time.sleep(1)
    raise RuntimeError(f"app not reachable at {BASE}")


def csrf_of(html):
    m = re.search(r'name="_csrf" value="([^"]+)"', html)
    return m.group(1) if m else None


def login(page, username, password):
    page.goto(BASE + "/")
    out = page.locator("header form button.link")
    if out.count():
        out.first.click()
        page.wait_for_load_state()
    page.goto(BASE + "/login")
    page.fill("input[name=username]", username)
    page.fill("input[name=password]", password)
    page.click("button.primary")
    page.wait_for_load_state()
    if "/password" in page.url:  # first sign-in: new password required
        page.fill("input[name=old_password]", password)
        page.fill("input[name=new_password]", STARTER_PASSWORD)
        page.fill("input[name=repeat_password]", STARTER_PASSWORD)
        page.click("button.primary")
        page.wait_for_load_state()


def post(page, path, data=None, token_from="/"):
    """POST a form as the signed-in browser (same cookies), with a fresh CSRF token."""
    token = csrf_of(page.request.get(BASE + token_from).text())
    form = dict(data or {})
    form["_csrf"] = token
    return page.request.post(BASE + path, form=form)


def tick_now(page):
    """Run the app's background work now (import, timers, daily email, outbox)."""
    r = post(page, "/test/tick")
    assert r.ok and r.json()["ok"], r.text()
    return r.json()


def shift_clock(page, minutes):
    return post(page, "/test/clock", {"minutes": str(minutes)}).json()


class Mailbox:
    def _get(self, path):
        with urllib.request.urlopen(MAILPIT + path, timeout=10) as r:
            return r.read()

    def clear(self):
        req = urllib.request.Request(MAILPIT + "/api/v1/messages", method="DELETE")
        urllib.request.urlopen(req, timeout=10).read()

    def messages(self):
        return json.loads(self._get("/api/v1/messages?limit=500"))["messages"]

    def message(self, mid):
        return json.loads(self._get(f"/api/v1/message/{mid}"))

    def attachment(self, mid, part):
        return self._get(f"/api/v1/message/{mid}/part/{part}")

    def wait(self, to, subject_part="", *, seen=(), timeout=30, page=None):
        """The newest email to `to` whose subject contains `subject_part`, not in `seen` (ids)."""
        end = time.time() + timeout
        while time.time() < end:
            for m in self.messages():
                rcpts = [a["Address"].lower() for a in m["To"]]
                if to.lower() in rcpts and subject_part in m["Subject"] and m["ID"] not in seen:
                    return self.message(m["ID"])
            if TICKER["page"] is not None:
                tick_now(TICKER["page"])  # send the outbox now instead of waiting 30 seconds
            time.sleep(1)
        raise AssertionError(f"no email to {to} with '{subject_part}'")

    def link(self, msg):
        m = re.search(r"(https?://\S+/t/[A-Za-z0-9_\-]+)", msg["Text"])
        assert m, "no task link in the email"
        return m.group(1)
