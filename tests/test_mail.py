"""Phase 4 core: links, outbox, emails, PDFs, reminders and expiry."""

import email
import email.policy
from datetime import datetime, timedelta, timezone

import pytest

from wftool import clock, links, masterdata, outbox, pdf, timers
from wftool.crypto import Cipher
from wftool.engine import Engine
from wftool.files import FileStore
from wftool.mailer import OutboxNotifier

from conftest import PNG, actor, open_tasks
from fake_smtp import FakeSMTP

HR_START = {"employee": "Tom Neu", "start_date": "2026-12-01", "department": "Sales", "manager": "mgr@co.de"}
IT_START = {"user": "emp@co.de", "laptop_issued": "on", "laptop_model": "Latitude 5450", "laptop_vendor": "Dell",
            "laptop_serial": "L-1", "smartphone_issued": "on", "smartphone_model": "iPhone 15",
            "smartphone_vendor": "Apple", "imei": "356938035643809", "pin": "Pin-4711", "puk": "88776655"}
MONDAY = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def mail(seeded, tmp_path):
    clock.set_now(MONDAY)
    cipher = Cipher.generate()
    files = FileStore(tmp_path / "files", cipher)
    masterdata.set_setting(seeded, "base_url", "https://wftool.co.de")
    engine = Engine(seeded, cipher, OutboxNotifier(seeded, files), files=files)
    yield engine
    clock.set_now(None)


def queued(db, **where):
    rows = [dict(r) for r in db.all("SELECT * FROM outbox ORDER BY id")]
    return [r for r in rows if all(r.get(k) == v for k, v in where.items())]


def token_of(row):
    return row["body_text"].split("/t/", 1)[1].split()[0]


def test_task_email_carries_a_single_use_link(mail):
    req = mail.start("HR Onboard", actor("hr1@co.de"), HR_START)
    [row] = queued(mail.db, to_addr="mgr@co.de")
    assert row["kind"] == "task" and row["subject"] == f"Bitte ergänzen und genehmigen: HR Onboarding {req['number']}"
    assert "https://wftool.co.de/t/" in row["body_text"] and "<html>" in row["body_html"]
    assert "Tom Neu" in row["body_html"] and "Aufgabe öffnen" in row["body_html"]
    token = token_of(row)
    assert token not in " ".join(r["token_hash"] for r in mail.db.all("SELECT token_hash FROM links"))
    link = links.find(mail.db, token)
    assert link["state"] == "valid" and link["email"] == "mgr@co.de"
    assert link["expires_at"].startswith("2026-11-16")  # 14 days

    task = open_tasks(req)[0]
    mail.decide(task["id"], "approved", actor("mgr@co.de"), values={"software": ["ERP"]}, link_id=link["id"])
    assert links.find(mail.db, token)["state"] == "used"
    assert links.find(mail.db, "nonsense") is None


def test_emails_to_the_starter_and_notify_with_pdf(mail):
    req = mail.start("IT Assets Checklist", actor("it1@co.de"), IT_START)
    [sign] = queued(mail.db, to_addr="emp@co.de", kind="task")
    assert sign["subject"].startswith("Bitte unterschreiben: IT-Ausstattung")
    assert "Pin-4711" not in sign["body_html"] and "••••" in sign["body_html"]  # secrets masked in emails
    req = mail.decide(open_tasks(req)[0]["id"], "signed", actor("emp@co.de"), signature_image=PNG)
    assert req["status"] == "completed"
    assert mail.get_task(req["tasks"][0]["id"])["signature_file"].startswith("signatures/")
    notify = queued(mail.db, kind="notify")
    assert sorted(r["to_addr"] for r in notify) == ["emp@co.de", "it1@co.de", "it2@co.de"]
    assert all(r["attachment_name"] == f"{req['number']}.pdf" and r["attachment_path"] for r in notify)
    attached = mail.files.load(notify[0]["attachment_path"])
    assert attached.startswith(b"%PDF")
    assert queued(mail.db, kind="task_decided")[0]["to_addr"] == "it1@co.de"
    assert queued(mail.db, kind="request_completed")[0]["to_addr"] == "it1@co.de"
    events = " ".join(r["data_json"] for r in mail.db.all("SELECT data_json FROM events"))
    assert "Pin-4711" not in events


def test_reject_resend_and_cancel(mail):
    req = mail.start("HR Onboard", actor("hr1@co.de"), HR_START)
    first = token_of(queued(mail.db, to_addr="mgr@co.de")[0])
    mail.resend(open_tasks(req)[0]["id"], actor("hr1@co.de"))
    assert links.find(mail.db, first)["state"] == "replaced"
    second = token_of(queued(mail.db, to_addr="mgr@co.de")[-1])
    assert links.find(mail.db, second)["state"] == "valid"

    req = mail.decide(open_tasks(req)[0]["id"], "rejected", actor("mgr@co.de"), reason="Wrong department")
    returned = queued(mail.db, kind="request_returned")[0]
    assert returned["to_addr"] == "hr1@co.de" and "Wrong department" in returned["body_text"]
    assert f"https://wftool.co.de/requests/{req['id']}" in returned["body_text"]
    assert links.find(mail.db, second)["state"] == "closed"  # decided in the app: the email link stops too

    req = mail.resubmit(req["id"], actor("hr1@co.de"), dict(HR_START, department="Service"))
    third = token_of(queued(mail.db, to_addr="mgr@co.de")[-1])
    mail.cancel(req["id"], actor("hr1@co.de"), "Candidate withdrew")
    assert links.find(mail.db, third)["state"] == "closed"
    assert queued(mail.db, kind="request_cancelled")[0]["to_addr"] == "hr1@co.de"


def test_starter_gets_english_emails_when_their_account_is_english(mail):
    from wftool import accounts
    accounts.create_account(mail.db, "hanna", "temporary-pass-1", "starter", email="hr1@co.de", language="en",
                            by="admin")
    req = mail.start("HR Onboard", actor("hr1@co.de"), HR_START)
    mail.decide(open_tasks(req)[0]["id"], "approved", actor("mgr@co.de"), values={"software": ["ERP"]})
    decided = queued(mail.db, kind="task_decided")[0]
    assert decided["subject"].startswith("HR Onboard ") and "Approved" in decided["subject"]
    assert queued(mail.db, to_addr="mgr@co.de")[0]["subject"].startswith("Bitte")  # approvers: German


def test_outbox_sends_html_with_text_copy_and_pdf(mail, tmp_path):
    smtp = FakeSMTP()
    try:
        for key, value in [("smtp_host", "127.0.0.1"), ("smtp_port", str(smtp.port)), ("smtp_security", "none"),
                           ("smtp_sender", "wftool@co.de")]:
            masterdata.set_setting(mail.db, key, value)
        req = mail.start("IT Assets Checklist", actor("it1@co.de"), IT_START)
        mail.decide(open_tasks(req)[0]["id"], "signed", actor("emp@co.de"), signature_image=PNG)
        result = outbox.send_due(mail.db, mail.cipher, mail.files)
        assert result["sent"] == len(queued(mail.db)) and result["failed"] == 0
        assert {r["status"] for r in queued(mail.db)} == {"sent"}
        assert not any(r["attachment_path"] and (tmp_path / "files" / r["attachment_path"]).exists()
                       for r in queued(mail.db))  # encrypted PDFs removed once sent
        notify = next(m for m in smtp.messages if "Benutzer und IT" in m["data"] or "Zur Information" in m["data"])
        msg = email.message_from_string(notify["data"], policy=email.policy.default)
        assert msg.get_content_type() == "multipart/mixed"
        kinds = [p.get_content_type() for p in msg.walk()]
        assert "text/plain" in kinds and "text/html" in kinds and "application/pdf" in kinds
        assert msg["Auto-Submitted"] == "auto-generated" and msg["From"] == "wftool@co.de"
        assert smtp.auth_attempts == 0
        sent = mail.db.all("SELECT * FROM events WHERE type = 'email.sent'")
        assert len(sent) == result["sent"]
    finally:
        smtp.close()


def test_outbox_retries_then_fails(mail):
    masterdata.set_setting(mail.db, "smtp_host", "127.0.0.1")
    masterdata.set_setting(mail.db, "smtp_port", "9")  # nothing listens there
    masterdata.set_setting(mail.db, "smtp_sender", "wftool@co.de")
    outbox.queue(mail.db, to="a@co.de", subject="S", text="T", html="<p>T</p>", kind="test")
    assert outbox.send_due(mail.db, mail.cipher, mail.files)["retry"] == 1
    assert outbox.send_due(mail.db, mail.cipher, mail.files)["retry"] == 0  # waiting for the next attempt
    for _ in range(outbox.MAX_ATTEMPTS):
        clock.set_now(clock.now() + timedelta(hours=13))
        outbox.send_due(mail.db, mail.cipher, mail.files)
    row = queued(mail.db, kind="test")[0]
    assert row["status"] == "failed" and row["attempts"] == outbox.MAX_ATTEMPTS and row["last_error"]
    outbox.retry(mail.db, row["id"], by="admin")
    assert queued(mail.db, kind="test")[0]["status"] == "pending"


def test_outbox_waits_when_smtp_is_not_set_up(mail):
    outbox.queue(mail.db, to="a@co.de", subject="S", text="T", kind="test")
    assert outbox.send_due(mail.db, mail.cipher, mail.files)["skipped"] == "SMTP is not set up"
    assert queued(mail.db, kind="test")[0]["attempts"] == 0


def test_one_reminder_after_five_working_days_and_notice_after_ten(mail):
    req = mail.start("HR Onboard", actor("hr1@co.de"), HR_START)
    task = open_tasks(req)[0]
    first = token_of(queued(mail.db, to_addr="mgr@co.de")[0])
    clock.set_now(MONDAY + timedelta(days=6))  # Sunday: 4 working days
    assert timers.run(mail.db, mail) == {"expired": 0, "reminded": 0, "noticed": 0}
    clock.set_now(MONDAY + timedelta(days=7))  # Monday 9 Nov: 5 working days
    assert timers.run(mail.db, mail)["reminded"] == 1
    reminder = queued(mail.db, kind="reminder")[0]
    assert reminder["subject"].startswith("Erinnerung: ") and links.find(mail.db, first)["state"] == "replaced"
    clock.set_now(MONDAY + timedelta(days=8))
    assert timers.run(mail.db, mail)["reminded"] == 0  # only once
    clock.set_now(datetime(2026, 11, 13, 8, tzinfo=timezone.utc))  # 9 working days
    assert timers.run(mail.db, mail)["noticed"] == 0
    clock.set_now(datetime(2026, 11, 16, 8, tzinfo=timezone.utc))  # 10 working days
    assert timers.run(mail.db, mail)["noticed"] == 1
    notice = queued(mail.db, kind="starter_notice")[0]
    assert notice["to_addr"] == "hr1@co.de" and "10 Arbeitstagen" in notice["body_text"]
    assert mail.get_task(task["id"])["starter_noticed_at"]


def test_working_days_skip_saxon_holidays(mail):
    from wftool import settings
    from datetime import date
    assert settings.add_working_days(mail.db, date(2026, 11, 17), 1) == date(2026, 11, 19)  # 18 Nov: Buß- und Bettag


def test_expiry_waits_or_rejects(mail):
    req = mail.start("HR Onboard", actor("hr1@co.de"), HR_START)
    masterdata.set_setting(mail.db, "reminder_working_days", "30")
    clock.set_now(MONDAY + timedelta(days=15))
    assert timers.run(mail.db, mail)["expired"] == 1
    assert {r["to_addr"] for r in queued(mail.db, kind="task_expired")} == {"hr1@co.de", "admin@co.de"}
    assert mail.get_request(req["id"])["status"] == "open"
    assert timers.run(mail.db, mail)["expired"] == 0  # handled once

    # A step set to reject automatically sends the request back
    from conftest import load_example
    from wftool import workflows
    d = load_example("hr_onboard.json")
    d["steps"][0]["on_timeout"] = "reject"
    workflows.save_draft(mail.db, d, by="admin")
    workflows.publish(mail.db, "HR Onboard", by="admin")
    clock.set_now(MONDAY + timedelta(days=20))
    req = mail.start("HR Onboard", actor("hr1@co.de"), HR_START)
    clock.set_now(MONDAY + timedelta(days=35))
    timers.run(mail.db, mail)
    req = mail.get_request(req["id"])
    assert req["status"] == "returned" and "No response within 14 days" in req["tasks"][0]["reason"]


def test_paper_signature_with_print_pdf_and_encrypted_scan(mail):
    req = mail.start("IT Assets Checklist", actor("it1@co.de"), IT_START)
    task = open_tasks(req)[0]
    token = token_of(queued(mail.db, to_addr="emp@co.de")[0])
    mail.decide(task["id"], "paper", actor("emp@co.de"))
    assert links.find(mail.db, token)["state"] == "closed"
    assert queued(mail.db, kind="paper_requested")[0]["to_addr"] == "it1@co.de"
    printout = pdf.print_pdf(mail.db, mail.get_request(req["id"]), task)
    assert printout.startswith(b"%PDF") and len(printout) > 1500

    clock.set_now(MONDAY + timedelta(days=7))
    timers.run(mail.db, mail)
    assert queued(mail.db, kind="paper_reminder")[0]["to_addr"] == "it1@co.de"  # reminders go to the starter

    scan = b"%PDF-1.4 scanned signature page"
    req = mail.complete_paper(task["id"], actor("it1@co.de"), scan)
    done = mail.get_task(task["id"])
    assert done["decision"] == "signed_paper" and done["scan_file"].startswith("scans/")
    assert mail.files.load(done["scan_file"]) == scan
    assert scan not in (mail.files.root / done["scan_file"]).read_bytes()


def test_request_pdf_contains_data_and_steps(mail):
    req = mail.start("IT Assets Checklist", actor("it1@co.de"), dict(IT_START, monitor_issued="on", monitors=[
        {"monitor_model": "P2423", "monitor_vendor": "Dell", "monitor_serial": "M-77"}]))
    req = mail.decide(open_tasks(req)[0]["id"], "signed", actor("emp@co.de", ip="10.20.1.45"), signature_image=PNG)
    data = pdf.request_pdf(mail.db, req, "de", files=mail.files)
    assert data.startswith(b"%PDF")
    from pypdf import PdfReader
    import io
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)
    for expected in ("IT-Ausstattung", req["number"], "M-77", "Pin-4711", "10.20.1.45", "Unterschrieben"):
        assert expected in text, expected
