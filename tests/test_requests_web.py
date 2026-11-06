"""Phase 4 pages: start a request, link pages, request page actions, admin pages."""

import base64
import io
import re

import pytest

from wftool import accounts, links, masterdata, workflows

from conftest import load_example, seed
from test_web import csrf, first_login, login

PNG_DATA = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200).decode()


@pytest.fixture
def site(app, client):
    db = app.extensions["wftool"]["db"]
    seed(db)
    masterdata.set_setting(db, "base_url", "https://wftool.co.de")
    for name in ("hr_onboard.json", "it_assets_checklist.json"):
        workflows.save_draft(db, load_example(name), by="admin")
        workflows.publish(db, load_example(name)["name"], by="admin")
    workflows.set_start_groups(db, "HR Onboard", ["HR"], by="admin")
    workflows.set_start_groups(db, "IT Assets Checklist", ["IT"], by="admin")
    token = first_login(client)
    for user, email in (("hanna", "hr1@co.de"), ("ina", "it1@co.de")):
        accounts.create_account(db, user, "temporary-pass-1", "starter", email=email, language="de", by="admin")
        acc = db.one("SELECT id FROM accounts WHERE username = ?", (user,))
        db.execute("UPDATE accounts SET must_change_password = 0 WHERE id = ?", (acc["id"],))
    client.post("/logout", data={"_csrf": token})
    return db


def as_user(client, username):
    m = re.search(r'name="_csrf" value="([^"]+)"', client.get("/", follow_redirects=True).get_data(as_text=True))
    if m:
        client.post("/logout", data={"_csrf": m.group(1)})
    login(client, username, "temporary-pass-1" if username != "admin" else "new-admin-password")
    return csrf(client, "/")


def wid(db, name):
    return db.one("SELECT id FROM workflows WHERE name = ?", (name,))["id"]


def link_for(db, to):
    row = db.one("SELECT body_text FROM outbox WHERE to_addr = ? AND kind IN ('task', 'reminder') ORDER BY id DESC"
                 " LIMIT 1", (to,))
    return "/t/" + row["body_text"].split("/t/", 1)[1].split()[0]


def page_token(client, path):
    return re.search(r'name="_csrf" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def test_hr_onboard_from_start_to_finish(site, client):
    token = as_user(client, "hanna")
    home = client.get("/").get_data(as_text=True)
    assert "HR Onboarding" in home and "IT-Ausstattung" not in home
    path = f"/requests/new/{wid(site, 'HR Onboard')}"
    page = client.get(path).get_data(as_text=True)
    assert 'name="f.employee"' in page and 'list="ad-users"' in page

    bad = client.post(path, data={"_csrf": token, "f.employee": "", "f.start_date": "morgen"})
    text = bad.get_data(as_text=True)
    assert "Pflichtfeld" in text and "kein Datum" in text
    resp = client.post(path, data={"_csrf": token, "f.employee": "Tom Neu", "f.start_date": "2026-12-01",
                                   "f.department": "Vertrieb", "f.manager": "mgr@co.de"})
    assert resp.status_code == 302
    rid = int(resp.headers["Location"].rsplit("/", 1)[1])
    detail = client.get(f"/requests/{rid}").get_data(as_text=True)
    assert "Max Manager" in detail and "Offen" in detail and "Link erneut senden" in detail

    # The manager opens the link without signing in: opening does not decide
    client.post("/logout", data={"_csrf": token})
    link = link_for(site, "mgr@co.de")
    page = client.get(link).get_data(as_text=True)
    assert "Software wählen und genehmigen" in page and 'name="f.software"' in page and "Tom Neu" in page
    assert site.one("SELECT status FROM tasks WHERE id = 1")["status"] == "open"
    assert site.one("SELECT COUNT(*) AS n FROM events WHERE type = 'link.fetched'")["n"] == 1
    tok = page_token(client, link)
    missing_reason = client.post(link, data={"_csrf": tok, "action": "reject", "reason": ""}).get_data(as_text=True)
    assert "Bitte eine Begründung" in missing_reason
    done = client.post(link, data={"_csrf": tok, "action": "approve", "f.software": ["ERP", "AutoCAD"]},
                       headers={"User-Agent": "Edge 131 on Windows 11"}, environ_base={"REMOTE_ADDR": "10.20.1.45"})
    assert "Genehmigt" in done.get_data(as_text=True)
    task = site.one("SELECT * FROM tasks WHERE id = 1")
    assert (task["decision"], task["ip"], task["user_agent"]) == ("approved", "10.20.1.45", "Edge 131 on Windows 11")
    again = client.get(link)
    assert again.status_code == 410 and "Bereits entschieden von mgr@co.de" in again.get_data(as_text=True)

    # AutoCAD approver, then HR confirms for the group, then IT gets the copy
    cad = link_for(site, "cad@co.de")
    client.post(cad, data={"_csrf": page_token(client, cad), "action": "approve"})
    hr2 = link_for(site, "hr2@co.de")
    page = client.get(hr2).get_data(as_text=True)
    assert "An IT weiterleiten" in page
    client.post(hr2, data={"_csrf": page_token(client, hr2), "action": "confirm"})
    hr1 = link_for(site, "hr1@co.de")
    assert "anderer Person aus Ihrer Gruppe" in client.get(hr1).get_data(as_text=True) or \
        client.get(hr1).status_code == 410
    assert site.one("SELECT status FROM requests WHERE id = ?", (rid,))["status"] == "completed"
    assert site.one("SELECT COUNT(*) AS n FROM outbox WHERE kind = 'notify' AND attachment_path IS NOT NULL")["n"] == 4


def test_it_assets_sign_on_screen_with_sections(site, client):
    token = as_user(client, "ina")
    path = f"/requests/new/{wid(site, 'IT Assets Checklist')}"
    page = client.get(path).get_data(as_text=True)
    assert 'data-show-when=' in page and 'data-add-row="monitors"' in page and 'type="password"' in page
    data = {"_csrf": token, "f.user": "emp@co.de", "f.monitor_issued": "on",
            "r.monitors.0.monitor_model": "P2423", "r.monitors.0.monitor_vendor": "Dell",
            "r.monitors.0.monitor_serial": "M1", "r.monitors.1004.monitor_model": "U2724D",
            "r.monitors.1004.monitor_vendor": "Dell", "r.monitors.1004.monitor_serial": "",
            "f.smartphone_issued": "on", "f.smartphone_model": "iPhone 15", "f.smartphone_vendor": "Apple",
            "f.imei": "356938035643809", "f.pin": "Pin-4711"}
    bad = client.post(path, data=data).get_data(as_text=True)
    assert "Pflichtfeld" in bad and 'name="r.monitors.1.monitor_serial"' in bad  # second row kept, error shown
    data["r.monitors.1004.monitor_serial"] = "M2"
    resp = client.post(path, data=data)
    rid = int(resp.headers["Location"].rsplit("/", 1)[1])
    detail = client.get(f"/requests/{rid}").get_data(as_text=True)
    assert "Monitor 2" in detail and "M2" in detail and "Pin-4711" not in detail and "••••" in detail
    shown = client.post(f"/requests/{rid}", data={"_csrf": token, "action": "show_secrets"}).get_data(as_text=True)
    assert "Pin-4711" in shown
    assert site.one("SELECT COUNT(*) AS n FROM events WHERE type = 'secret.viewed'")["n"] == 1

    client.post("/logout", data={"_csrf": token})
    link = link_for(site, "emp@co.de")
    page = client.get(link).get_data(as_text=True)
    assert 'id="pad"' in page and "sign.js" in page and "Pin-4711" in page  # the signer sees the PIN
    tok = page_token(client, link)
    blank = client.post(link, data={"_csrf": tok, "action": "sign", "signature": ""}).get_data(as_text=True)
    assert "Bitte zuerst im Feld unterschreiben" in blank
    done = client.post(link, data={"_csrf": tok, "action": "sign", "signature": "data:image/png;base64," + PNG_DATA})
    assert "Unterschrieben" in done.get_data(as_text=True)
    task = site.one("SELECT * FROM tasks WHERE request_id = ?", (rid,))
    assert task["decision"] == "signed" and task["signature_file"]
    assert site.one("SELECT status FROM requests WHERE id = ?", (rid,))["status"] == "completed"


def test_paper_signature_from_the_request_page(site, client):
    token = as_user(client, "ina")
    resp = client.post(f"/requests/new/{wid(site, 'IT Assets Checklist')}", data={"_csrf": token, "f.user": "emp@co.de"})
    rid = int(resp.headers["Location"].rsplit("/", 1)[1])
    task_id = site.one("SELECT id FROM tasks WHERE request_id = ?", (rid,))["id"]
    client.post(f"/requests/{rid}/action", data={"_csrf": token, "action": "paper", "task_id": task_id})
    detail = client.get(f"/requests/{rid}").get_data(as_text=True)
    assert "Wartet auf den Scan" in detail or "Scan hochladen" in detail
    printed = client.get(f"/requests/{rid}/tasks/{task_id}/print.pdf")
    assert printed.mimetype == "application/pdf" and printed.data.startswith(b"%PDF")
    number = site.one("SELECT number FROM requests WHERE id = ?", (rid,))["number"]

    def upload(content, typed):
        return client.post(f"/requests/{rid}/tasks/{task_id}/scan", content_type="multipart/form-data",
                           data={"_csrf": token, "number": typed, "scan": (io.BytesIO(content), "scan.pdf")},
                           follow_redirects=True).get_data(as_text=True)

    assert "stimmt nicht" in upload(b"%PDF-1.4 scan", "2026-9999")
    assert "PDF, JPG oder PNG" in upload(b"GIF89a....", number)
    assert "Scan hochgeladen" in upload(b"%PDF-1.4 scan", number)
    assert site.one("SELECT decision FROM tasks WHERE id = ?", (task_id,))["decision"] == "signed_paper"


def test_reject_resubmit_cancel_and_permissions(site, client):
    token = as_user(client, "hanna")
    path = f"/requests/new/{wid(site, 'HR Onboard')}"
    resp = client.post(path, data={"_csrf": token, "f.employee": "Tom", "f.start_date": "2026-12-01",
                                   "f.manager": "mgr@co.de"})
    rid = int(resp.headers["Location"].rsplit("/", 1)[1])
    old = link_for(site, "mgr@co.de")
    client.post(f"/requests/{rid}/action", data={"_csrf": token, "action": "resend", "task_id": 1})
    assert client.get(old).status_code == 410  # replaced by the newer email
    link = link_for(site, "mgr@co.de")
    client.post(link, data={"_csrf": page_token(client, link), "action": "reject", "reason": "Falsche Abteilung"})
    detail = client.get(f"/requests/{rid}").get_data(as_text=True)
    assert "Zurückgegeben" in detail and "Falsche Abteilung" in detail and "Ändern und erneut senden" in detail
    page = client.get(f"/requests/{rid}/resubmit").get_data(as_text=True)
    assert 'value="Tom"' in page and "Falsche Abteilung" in page
    client.post(f"/requests/{rid}/resubmit", data={"_csrf": token, "f.employee": "Tom", "f.start_date": "2026-12-01",
                                                   "f.department": "Service", "f.manager": "mgr@co.de"})
    assert site.one("SELECT round, status FROM requests WHERE id = ?", (rid,))["round"] == 2

    # Ina (IT) can't see Hanna's request
    token_ina = as_user(client, "ina")
    assert client.get(f"/requests/{rid}").status_code == 403
    assert client.post(f"/requests/{rid}/action", data={"_csrf": token_ina, "action": "cancel",
                                                        "reason": "x"}).status_code == 403
    assert client.get("/admin/tasks").status_code == 403

    token = as_user(client, "hanna")
    client.post(f"/requests/{rid}/action", data={"_csrf": token, "action": "cancel", "reason": "Absage"})
    assert site.one("SELECT status FROM requests WHERE id = ?", (rid,))["status"] == "cancelled"
    assert client.get(link_for(site, "mgr@co.de")).status_code == 410


def test_admin_reassigns_and_sees_the_outbox(site, client):
    token = as_user(client, "hanna")
    resp = client.post(f"/requests/new/{wid(site, 'HR Onboard')}", data={
        "_csrf": token, "f.employee": "Tom", "f.start_date": "2026-12-01", "f.manager": "mgr@co.de"})
    rid = int(resp.headers["Location"].rsplit("/", 1)[1])
    masterdata.upsert_user(site, "mgr@co.de", "Max Manager", active=False)
    token = as_user(client, "admin")
    home = client.get("/").get_data(as_text=True)
    assert "nicht mehr aktiv" in home
    page = client.get("/admin/tasks?inactive=1").get_data(as_text=True)
    assert "mgr@co.de" in page and "inaktiv" in page
    client.post(f"/requests/{rid}/action", data={"_csrf": token, "action": "reassign", "task_id": 1,
                                                 "email": "emp@co.de", "reason": "Urlaub"})
    assert site.one("SELECT assignee_email FROM tasks WHERE status = 'open'")["assignee_email"] == "emp@co.de"
    outbox_page = client.get("/admin/outbox").get_data(as_text=True)
    assert "emp@co.de" in outbox_page and "wartend" in outbox_page
    sent = client.post("/admin/outbox", data={"_csrf": token, "action": "send"}, follow_redirects=True)
    assert "SMTP ist noch nicht eingerichtet" in sent.get_data(as_text=True)


def test_signed_in_assignee_can_act_in_the_app(site, client):
    db = site
    accounts.create_account(db, "max", "temporary-pass-1", "starter", email="mgr@co.de", language="en", by="admin")
    db.execute("UPDATE accounts SET must_change_password = 0 WHERE username = 'max'")
    token = as_user(client, "hanna")
    client.post(f"/requests/new/{wid(db, 'HR Onboard')}", data={
        "_csrf": token, "f.employee": "Tom", "f.start_date": "2026-12-01", "f.manager": "mgr@co.de"})
    as_user(client, "max")
    home = client.get("/").get_data(as_text=True)
    assert "Your tasks" in home and "/tasks/1" in home
    tok = page_token(client, "/tasks/1")
    done = client.post("/tasks/1", data={"_csrf": tok, "action": "approve", "f.software": ["ERP"]})
    assert "Approved" in done.get_data(as_text=True)
    task = db.one("SELECT * FROM tasks WHERE id = 1")
    assert task["decided_by"] == "mgr@co.de"
    assert links.find(db, link_for(db, "mgr@co.de")[3:])["state"] == "closed"
    assert client.get("/tasks/2").status_code == 403  # someone else's task


def test_unknown_link_explains_and_hides_nothing(client):
    resp = client.get("/t/" + "x" * 43)
    assert resp.status_code == 404 and "nicht bekannt" in resp.get_data(as_text=True)
