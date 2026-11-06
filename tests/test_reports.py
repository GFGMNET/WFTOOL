"""Phase 5: search, CSV, full PDF report, tamper check, daily anchor, retention, auditor access."""

import io
from datetime import date, datetime, timedelta, timezone

import pytest
from pypdf import PdfReader

from wftool import accounts, audit, clock, masterdata, outbox, pdf, reports, workflows
from wftool.crypto import Cipher
from wftool.engine import Engine
from wftool.files import FileStore
from wftool.mailer import OutboxNotifier

from conftest import PNG, actor, load_example, open_tasks, seed
from test_web import csrf, first_login, login

HR = {"employee": "Tom Neu", "start_date": "2026-12-01", "department": "Sales", "manager": "mgr@co.de"}
IT = {"user": "emp@co.de", "smartphone_issued": "on", "smartphone_model": "iPhone 15", "smartphone_vendor": "Apple",
      "imei": "356938035643809", "pin": "Pin-4711"}
THU = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def eng(seeded, tmp_path):
    clock.set_now(THU)
    cipher = Cipher.generate()
    files = FileStore(tmp_path / "files", cipher)
    e = Engine(seeded, cipher, OutboxNotifier(seeded, files), files=files)
    yield e
    clock.set_now(None)


def text_of(data):
    return " ".join(" ".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages).split())


# ------------------------------------------------------------ audit chain

def test_ip_and_browser_are_protected_and_can_be_removed(eng):
    req = eng.start("HR Onboard", actor("hr1@co.de", ip="10.1.1.1"), HR)
    eng.decide(open_tasks(req)[0]["id"], "approved", actor("mgr@co.de", ip="10.2.2.2"), values={"software": []})
    assert audit.verify(eng.db)["ok"]
    row = eng.db.one("SELECT * FROM events WHERE ip = '10.2.2.2'")
    assert row["hash_version"] == 2 and row["ip_hash"] and row["field_salt"]

    eng.db.execute("UPDATE events SET ip = '10.9.9.9' WHERE id = ?", (row["id"],))
    bad = audit.verify(eng.db)
    assert not bad["ok"] and bad["first_bad_id"] == row["id"] and "IP address" in bad["problem"]
    eng.db.execute("UPDATE events SET ip = '10.2.2.2' WHERE id = ?", (row["id"],))

    assert audit.redact(eng.db, [row["id"]]) == 1
    after = eng.db.one("SELECT * FROM events WHERE id = ?", (row["id"],))
    assert after["ip"] is None and after["field_salt"] is None and after["ip_hash"] == row["ip_hash"]
    assert audit.verify(eng.db)["ok"]  # the chain still holds without the values
    eng.db.execute("UPDATE events SET ip = '10.2.2.2' WHERE id = ?", (row["id"],))
    assert not audit.verify(eng.db)["ok"]  # putting a value back is noticed too


# --------------------------------------------------------------- search

def _flow(eng):
    a = eng.start("HR Onboard", actor("hr1@co.de"), HR)
    eng.decide(open_tasks(a)[0]["id"], "approved", actor("mgr@co.de"), values={"software": ["ERP"]})
    clock.set_now(THU + timedelta(days=1))
    b = eng.start("IT Assets Checklist", actor("it1@co.de"), IT)
    return a, b


def test_search_filters(eng):
    a, b = _flow(eng)
    db = eng.db
    find = lambda **f: [r["number"] for r in reports.search(db, f)["rows"]]  # noqa: E731
    assert find() == [b["number"], a["number"]]
    assert find(date_from=date(2026, 11, 6)) == [b["number"]]
    assert find(date_to=date(2026, 11, 5)) == [a["number"]]
    assert find(workflow="HR Onboard") == [a["number"]]
    assert find(person="Max Manager") == [a["number"]]  # by the approver's name
    assert find(person="emp@co.de") == [b["number"]]  # by the person who signs
    assert find(status="open") == [b["number"], a["number"]] and find(status="completed") == []
    assert find(number=b["number"][-4:]) == [b["number"]]
    assert reports.search(db, {}, starter_only="hr1@co.de")["total"] == 1
    page = reports.search(db, {}, page=2, page_size=1)
    assert page["pages"] == 2 and [r["number"] for r in page["rows"]] == [a["number"]]
    hr = reports.search(db, {"workflow": "HR Onboard"})["rows"][0]
    assert hr["decisions"][0]["by"] == "mgr@co.de"


def test_csv_for_excel_without_secret_values(eng):
    _flow(eng)
    data = reports.to_csv(reports.all_rows(eng.db, {}))
    assert data.startswith("﻿Antragsnummer;Workflow;")
    assert "Genehmigt – mgr@co.de" in data and "Pin-4711" not in data
    assert data.count("\r\n") == 3


# ---------------------------------------------------------------- report

def test_full_report_with_versions_history_scan_and_check(eng):
    req = eng.start("IT Assets Checklist", actor("it1@co.de", ip="10.20.1.45"), IT)
    task = open_tasks(req)[0]
    eng.decide(task["id"], "rejected", actor("emp@co.de"), reason="IMEI falsch")
    req = eng.resubmit(req["id"], actor("it1@co.de"), dict(IT, imei="356938035643810"))
    task = open_tasks(req)[0]
    eng.decide(task["id"], "paper", actor("emp@co.de"))
    scan = pdf.print_pdf(eng.db, eng.get_request(req["id"]), task)  # a real PDF stands in for the scan
    req = eng.complete_paper(task["id"], actor("it1@co.de"), scan)
    events = audit.events_for(eng.db, req["id"])
    data = pdf.report_pdf(eng.db, req, "de", files=eng.files, versions=eng.form_versions(req["id"]),
                          events=events, check=audit.verify(eng.db))
    pages = len(PdfReader(io.BytesIO(data)).pages)
    assert pages > len(PdfReader(io.BytesIO(scan)).pages)
    text = text_of(data)
    for expected in ("Frühere Versionen", "356938035643809", "356938035643810", "IMEI falsch", "Pin-4711",
                     "Scan der Unterschrift", "Prüfung des Ereignisprotokolls", "In Ordnung", "10.20.1.45",
                     "Auf Papier unterschrieben"):
        assert expected in text, expected

    eng.db.execute("UPDATE events SET actor = 'someone' WHERE id = 3")
    bad = pdf.report_pdf(eng.db, req, "de", files=eng.files, versions=[], events=[], check=audit.verify(eng.db))
    assert "FEHLER: Eintrag 3" in text_of(bad)


# ----------------------------------------------------------------- anchor

def test_daily_anchor_once_after_six(eng):
    eng.start("HR Onboard", actor("hr1@co.de"), HR)
    clock.set_now(datetime(2026, 11, 6, 4, 59, tzinfo=timezone.utc))  # 05:59 in Berlin
    assert not reports.anchor_due(eng.db)
    clock.set_now(datetime(2026, 11, 6, 5, 0, tzinfo=timezone.utc))  # 06:00 in Berlin
    assert reports.anchor_due(eng.db)
    reports.send_anchor(eng.db)
    assert not reports.anchor_due(eng.db)
    mail = [r for r in outbox.recent(eng.db) if r["kind"] == "anchor"][0]
    assert mail["to_addr"] == "admin@co.de" and "06.11.2026" in mail["subject"]
    [anchor] = reports.anchors(eng.db)
    assert anchor["data"]["chain_ok"] and reports.anchor_matches(eng.db, anchor)
    eng.db.execute("UPDATE events SET hash = 'x' WHERE id = ?", (anchor["data"]["last_id"],))
    assert not reports.anchor_matches(eng.db, anchor)
    clock.set_now(datetime(2026, 11, 7, 5, 30, tzinfo=timezone.utc))
    assert reports.anchor_due(eng.db)


# -------------------------------------------------------------- retention

def test_retention_off_by_default_then_removes_ip_of_old_closed_requests(eng):
    old = eng.start("HR Onboard", actor("hr1@co.de", ip="10.1.1.1"), HR)
    eng.cancel(old["id"], actor("hr1@co.de", ip="10.1.1.1"), "Absage")
    clock.set_now(THU + timedelta(days=200))
    recent = eng.start("HR Onboard", actor("hr1@co.de", ip="10.3.3.3"), HR)
    assert reports.apply_retention(eng.db) == {"on": False}
    masterdata.set_setting(eng.db, "retention_months", "6")
    dry = reports.apply_retention(eng.db, dry_run=True)
    assert dry["requests"] == 1 and dry["events"] >= 2
    assert eng.db.one("SELECT COUNT(*) AS n FROM events WHERE ip = '10.1.1.1'")["n"] >= 2
    result = reports.apply_retention(eng.db)
    assert result["events"] >= 2
    assert eng.db.one("SELECT COUNT(*) AS n FROM events WHERE ip = '10.1.1.1'")["n"] == 0
    assert eng.db.one("SELECT COUNT(*) AS n FROM events WHERE ip = '10.3.3.3'")["n"] >= 1  # still open
    assert eng.get_request(old["id"])["status"] == "cancelled"  # decisions stay
    assert audit.verify(eng.db)["ok"]
    assert eng.db.one("SELECT COUNT(*) AS n FROM events WHERE type = 'retention.applied'")["n"] == 1
    assert recent["status"] == "open"


# ---------------------------------------------------------------- pages

@pytest.fixture
def web(app, client):
    db = app.extensions["wftool"]["db"]
    seed(db)
    for name in ("hr_onboard.json", "it_assets_checklist.json"):
        workflows.save_draft(db, load_example(name), by="admin")
        workflows.publish(db, load_example(name)["name"], by="admin")
    token = first_login(client)
    for user, role, email in (("audrey", "auditor", "audit@co.de"), ("ina", "starter", "it1@co.de")):
        accounts.create_account(db, user, "temporary-pass-1", role, email=email, language="de", by="admin")
        db.execute("UPDATE accounts SET must_change_password = 0 WHERE username = ?", (user,))
    engine = app.extensions["wftool"]["engine"]
    req = engine.start("IT Assets Checklist", actor("it1@co.de"), IT)
    engine.decide(open_tasks(req)[0]["id"], "signed", actor("emp@co.de"), signature_image=PNG)
    client.post("/logout", data={"_csrf": token})
    return db, req


def _as(client, user):
    login(client, user, "temporary-pass-1")
    return csrf(client, "/")


def test_auditor_sees_secret_values_and_everything_is_logged(web, client):
    db, req = web
    token = _as(client, "audrey")
    page = client.get(f"/requests/{req['id']}").get_data(as_text=True)
    assert "Pin-4711" in page and 'href="/audit"' in page
    assert db.one("SELECT COUNT(*) AS n FROM events WHERE type = 'secret.viewed'")["n"] == 1
    report = client.get(f"/requests/{req['id']}/pdf")
    assert report.mimetype == "application/pdf" and "Pin-4711" in text_of(report.data)
    listing = client.get("/requests?workflow=IT+Assets+Checklist&status=completed").get_data(as_text=True)
    assert req["number"] in listing and "1 Antrag" in listing
    csv = client.get("/requests.csv?status=completed")
    assert csv.mimetype == "text/csv" and req["number"] in csv.get_data(as_text=True)
    assert "Pin-4711" not in csv.get_data(as_text=True)
    audit_page = client.get("/audit").get_data(as_text=True)
    assert "task.decided" in audit_page and "Pin-4711" not in audit_page
    checked = client.post("/audit", data={"_csrf": token, "action": "verify"}).get_data(as_text=True)
    assert "Kette ist unverändert" in checked
    client.post("/audit", data={"_csrf": token, "action": "anchor"})
    assert not reports.anchors(db)  # auditors cannot send the anchor
    assert client.get("/admin/settings").status_code == 403
    types = {r["type"] for r in db.all("SELECT type FROM events")}
    assert {"request.pdf_downloaded", "report.csv_exported", "audit.verified"} <= types


def test_starter_search_is_limited_and_audit_closed(web, client):
    db, req = web
    _as(client, "ina")
    assert req["number"] in client.get("/requests").get_data(as_text=True)  # her own request
    page = client.get(f"/requests/{req['id']}").get_data(as_text=True)
    assert "Pin-4711" not in page  # starters see secrets only on request
    assert client.get("/audit").status_code == 403


def test_admin_sends_anchor_and_sets_retention(web, client):
    db, _ = web
    login(client, "admin", "new-admin-password")
    token = csrf(client, "/")
    page = client.post("/audit", data={"_csrf": token, "action": "anchor"}, follow_redirects=True).get_data(as_text=True)
    assert "Prüfwert-E-Mail" in page or "Admin-E-Mail" in page
    assert len(reports.anchors(db)) == 1
    settings_page = client.get("/admin/settings").get_data(as_text=True)
    assert "retention_months" in settings_page and "anchor_time" in settings_page
