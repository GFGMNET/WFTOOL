"""End-to-end: the whole app from an empty start, in a real browser, with real email.

The tests run in this order and build on each other (one app, one mailbox):
settings → import → lists and accounts → workflows → IT Assets (sign on a
phone) → IT Assets (reject, resend, paper) → HR Onboard → reminder and expiry →
search, PDF, CSV → tamper check → backup → image and security checks.
"""

import io
import os
import re
import shutil
import sqlite3

import pytest
from playwright.sync_api import expect
from pypdf import PdfReader

from e2e.helpers import (
    ADMIN_PASSWORD, BASE, DATA, ROOT, STARTER_PASSWORD, Mailbox, csrf_of, login, post, shift_clock, tick_now,
)

mail = Mailbox()
S = {}  # state shared between the steps

SETTINGS = {
    "smtp_host": os.environ.get("SMTP_HOST", "127.0.0.1"), "smtp_port": os.environ.get("SMTP_PORT", "1025"),
    "smtp_security": "none", "smtp_tls_verify": "yes", "smtp_user": "", "smtp_password": "",
    "smtp_sender": "wftool@company.de", "company_domains": "company.de", "base_url": BASE,
    "admin_email": "admin@company.de", "default_email_language": "de", "link_validity_days": "14",
    "reminder_working_days": "5", "starter_notice_working_days": "10", "extra_holidays": "",
    "import_max_deactivate_percent": "10", "anchor_time": "06:00", "retention_months": "",
}


def seen():
    return {m["ID"] for m in mail.messages()}


def pdf_text(data):
    return " ".join(" ".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages).split())


def db():
    con = sqlite3.connect(DATA / "wftool.db", timeout=30)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------- set-up

def test_01_settings_and_test_email(admin):
    """Admin signs in, enters the settings and sends a test email."""
    mail.clear()
    login(admin, "admin", ADMIN_PASSWORD)
    assert "TEST MODE" in admin.content()
    r = post(admin, "/admin/settings", SETTINGS, token_from="/admin/settings")
    assert r.ok and "field-error" not in r.text()
    post(admin, "/admin/settings/test-email", {"to": "admin@company.de"}, token_from="/admin/settings")
    msg = mail.wait("admin@company.de", "Test")
    assert "WFTOOL" in msg["Subject"]


def test_02_csv_import_from_the_import_folder(admin):
    """The example CSV files are put into the import folder and imported."""
    folder = DATA / "import"
    folder.mkdir(parents=True, exist_ok=True)
    for f in (ROOT / "examples" / "csv").glob("*.csv"):
        shutil.copy(f, folder / f.name)
    page = post(admin, "/admin/imports", token_from="/admin/imports")
    admin.goto(BASE + "/admin/imports")
    text = admin.content()
    for name in ("users.csv", "groups.csv", "software.csv"):
        assert name in text
    assert "abgelehnt" not in admin.locator("table").first.inner_text()
    admin.goto(BASE + "/admin/people?q=Engel")
    assert "Eva Engel" in admin.content()
    assert page.ok


def test_03_lists_and_accounts(admin):
    """Hardware lists are entered; HR and IT get starter accounts."""
    lists = {"laptop_models": ["Latitude 5450", "ThinkPad T14"], "docking_models": ["WD19S"],
             "monitor_models": ["P2423", "U2724D"], "smartphone_models": ["iPhone 15", "Galaxy A55"],
             "hardware_vendors": ["Dell", "Lenovo", "Apple", "Samsung"]}
    for name, items in lists.items():
        post(admin, "/admin/lists", {"name": name, "label": name.replace("_", " ").title()}, token_from="/admin/lists")
        for item in items:
            post(admin, f"/admin/lists/{name}", {"action": "add", "label": item}, token_from=f"/admin/lists/{name}")
    admin.goto(BASE + "/admin/lists/monitor_models")
    assert "U2724D" in admin.content()
    for user, email in (("ina", "ina.it@company.de"), ("hanna", "hanna.hr@company.de")):
        r = post(admin, "/admin/accounts", {"username": user, "role": "starter", "email": email, "language": "de",
                                            "password": "temporary-pass-1"}, token_from="/admin/accounts")
        assert r.ok
    admin.goto(BASE + "/admin/accounts")
    assert "ina.it@company.de" in admin.content()


def test_04_build_it_assets_in_the_builder(admin):
    """The IT Assets Checklist is built from an empty workflow and published."""
    admin.goto(BASE + "/admin/workflows")
    admin.fill("input[name=name]", "IT Assets Checklist")
    admin.fill("input[name=label_de]", "IT-Ausstattung")
    admin.fill("input[name=label_en]", "IT Assets Checklist")
    admin.click("form.form button.primary")
    admin.wait_for_selector("#builder .palette")

    def card(fid):
        return admin.locator(f"li.field-card[data-id='{fid}']")

    def add(ftype, de, en, fid, required=False, section=None):
        if section:
            card(section).locator("[data-key$=':addin']").select_option(ftype)
        else:
            admin.click(f".palette button[data-type='{ftype}']")
        c = admin.locator("li.field-card.open").last
        c.locator("[data-key$=':label:de']").first.fill(de)
        c.locator("[data-key$=':label:en']").first.fill(en)
        box = c.locator("[data-key$=':id']").first
        box.fill(fid)
        box.press("Tab")
        if required:
            card(fid).locator("[data-key$=':req']").check()
        return card(fid)

    def close(fid):
        card(fid).locator(":scope > .card-head .card-title").click()

    add("person", "Benutzer", "User", "user", required=True)
    close("user")
    for sid, de, en, models in (("laptop", "Laptop", "Laptop", "laptop_models"),
                                ("monitors", "Monitor", "Monitor", "monitor_models"),
                                ("smartphone", "Smartphone", "Smartphone", "smartphone_models")):
        prefix = "monitor" if sid == "monitors" else sid
        add("checkbox", f"{de} ausgegeben", f"{en} issued", f"{prefix}_issued")
        close(f"{prefix}_issued")
        add("section", de, en, sid)
        card(sid).locator("[data-key$=':show']").select_option("when")
        card(sid).locator("[data-key$=':sw:cf']").select_option(f"{prefix}_issued")
        if sid == "monitors":
            card(sid).locator("[data-key$=':rep']").check()
        close(sid)
        add("dropdown", "Modell", "Model", f"{prefix}_model", required=True, section=sid)
        card(f"{prefix}_model").locator("[data-key$=':list']").select_option(models)
        close(f"{prefix}_model")
        add("dropdown", "Hersteller", "Vendor", f"{prefix}_vendor", required=True, section=sid)
        card(f"{prefix}_vendor").locator("[data-key$=':list']").select_option("hardware_vendors")
        close(f"{prefix}_vendor")
        if sid == "smartphone":
            add("text", "IMEI", "IMEI", "imei", required=True, section=sid)
            close("imei")
            add("secret", "PIN", "PIN", "pin", section=sid)
            close("pin")
        else:
            add("text", "Seriennummer", "Serial number", f"{prefix}_serial", required=True, section=sid)
            close(f"{prefix}_serial")

    admin.click("#tab-steps")
    admin.select_option("#new-step-type", "sign")
    admin.click(".add-step button.primary")
    step = admin.locator("li.step-card.open").last
    step.locator("[data-key$=':who:k']").select_option("field")
    admin.locator("li.step-card.open").last.locator("[data-key$=':who:f']").select_option("user")
    admin.select_option("#new-step-type", "notify")
    admin.click(".add-step button.primary")

    def st():
        return admin.locator("li.step-card.open").last
    st().locator("[data-key$=':whoadd']").click()
    st().locator("[data-key$=':who0:g']").select_option("IT")
    st().locator("[data-key$=':whoadd']").click()
    st().locator("[data-key$=':who1:k']").select_option("field")
    st().locator("[data-key$=':who1:f']").select_option("user")
    expect(admin.locator("#check-stamp")).to_contain_text("Bereit", timeout=15000)
    admin.click("button[data-publish]")
    admin.wait_for_load_state()
    assert "Als Version 1 veröffentlicht" in admin.content()
    assert not admin.errors, admin.errors


def test_05_import_hr_onboard_and_who_may_start(admin):
    """HR Onboard comes in as a workflow file and is published; groups may start."""
    admin.goto(BASE + "/admin/workflows")
    admin.set_input_files("input[name=file]", str(ROOT / "examples" / "workflows" / "hr_onboard.json"))
    admin.click("form[action$='/import'] button")
    admin.wait_for_selector("#builder .palette")
    admin.click("button[data-publish]")
    admin.wait_for_load_state()
    assert "Als Version 1 veröffentlicht" in admin.content()
    admin.goto(BASE + "/admin/start")
    for workflow, group in (("HR Onboard", "HR"), ("IT Assets Checklist", "IT")):
        row = admin.locator("tr", has_text=workflow)
        row.locator(f"input[name=groups][value='{group}']").check()
        row.locator("button").click()
        admin.wait_for_load_state()
    assert admin.locator("tr", has_text="HR Onboard").locator("input[value='HR']").is_checked()


# --------------------------------------------------------------- requests

def test_06_it_assets_signed_on_a_phone(desk, phone):
    """IT starts a request with sections and two monitors; the user signs on a phone from the email."""
    login(desk, "ina", "temporary-pass-1")
    desk.goto(BASE + "/")
    desk.click("text=IT-Ausstattung")
    desk.fill("#f-user", "eva.engel@company.de")
    assert desk.locator("fieldset[data-section=laptop]").is_hidden()
    desk.check("#f-laptop_issued")
    desk.select_option("#f-laptop_model", "Latitude 5450")
    desk.select_option("#f-laptop_vendor", "Dell")
    desk.fill("#f-laptop_serial", "5CG4471ZQ")
    desk.check("#f-monitor_issued")
    desk.select_option("#f-monitors-0-monitor_model", "P2423")
    desk.select_option("#f-monitors-0-monitor_vendor", "Dell")
    desk.fill("#f-monitors-0-monitor_serial", "CN-0M1")
    desk.click("[data-add-row=monitors]")
    row = desk.locator("[data-rows=monitors] > [data-row]").nth(1)
    row.locator("select").nth(0).select_option("U2724D")
    row.locator("select").nth(1).select_option("Dell")
    row.locator("input").first.fill("CN-0M2")
    desk.check("#f-smartphone_issued")
    desk.select_option("#f-smartphone_model", "iPhone 15")
    desk.select_option("#f-smartphone_vendor", "Apple")
    desk.fill("#f-imei", "356938035643809")
    desk.fill("#f-pin", "4711")
    before = seen()
    desk.click("button.primary:has-text('Antrag senden')")
    desk.wait_for_url(re.compile(r"/requests/\d+$"))
    S["it_url"] = desk.url
    S["it_number"] = re.search(r"\d{4}-\d{4}", desk.content()).group(0)

    msg = mail.wait("eva.engel@company.de", "Bitte unterschreiben", seen=before, page=desk)
    assert "4711" not in msg["HTML"]  # secrets are masked in emails
    phone.goto(mail.link(msg))
    assert "4711" in phone.content()  # the signer sees the PIN
    assert phone.evaluate("document.documentElement.scrollWidth") <= 390
    phone.click("button[data-sign]")
    assert phone.locator("#pad-error").is_visible()  # an empty signature is not accepted
    box = phone.locator("#pad").bounding_box()
    phone.mouse.move(box["x"] + 30, box["y"] + 100)
    phone.mouse.down()
    for i in range(1, 25):
        phone.mouse.move(box["x"] + 30 + i * 11, box["y"] + 100 - 25 * (i % 3 == 0), steps=2)
    phone.mouse.up()
    phone.click("button[data-sign]")
    phone.wait_for_load_state()
    assert "Unterschrieben" in phone.content()

    for to in ("eva.engel@company.de", "ina.it@company.de", "ivo.it@company.de"):
        m = mail.wait(to, "Zur Information", seen=before, page=desk)
        att = [a for a in m["Attachments"] if a["FileName"].endswith(".pdf")]
        assert att, f"no PDF for {to}"
        assert S["it_number"] in pdf_text(mail.attachment(m["ID"], att[0]["PartID"]))
    desk.goto(S["it_url"])
    assert "Abgeschlossen" in desk.content()


def test_07_reject_resend_and_paper_signature(desk, phone):
    """The user rejects; IT corrects and resends; the user chooses paper; IT uploads the scan."""
    desk.goto(BASE + "/")
    desk.click("text=IT-Ausstattung")
    desk.fill("#f-user", "eva.engel@company.de")
    desk.check("#f-laptop_issued")
    desk.select_option("#f-laptop_model", "ThinkPad T14")
    desk.select_option("#f-laptop_vendor", "Lenovo")
    desk.fill("#f-laptop_serial", "WRONG")
    before = seen()
    desk.click("button.primary:has-text('Antrag senden')")
    desk.wait_for_url(re.compile(r"/requests/\d+$"))
    url = desk.url
    number = re.search(r"\d{4}-\d{4}", desk.content()).group(0)

    phone.goto(mail.link(mail.wait("eva.engel@company.de", number, seen=before, page=desk)))
    phone.click(".reject-box summary")
    phone.fill("#reason", "Seriennummer falsch")
    phone.click("button[data-reject]")
    phone.wait_for_load_state()
    assert "Abgelehnt" in phone.content()

    before = seen()
    desk.goto(url)
    assert "Seriennummer falsch" in desk.content()
    desk.click("text=Ändern und erneut senden")
    desk.fill("#f-laptop_serial", "PF4ABCDE")
    desk.click("button.primary:has-text('Erneut senden')")
    desk.wait_for_load_state()

    phone.goto(mail.link(mail.wait("eva.engel@company.de", number, seen=before, page=desk)))
    phone.click(".reject-box summary >> text=Stattdessen auf Papier")
    phone.click("button[value=paper]")
    phone.wait_for_load_state()
    assert "Papier" in phone.content()

    desk.goto(url)
    href = desk.locator("a", has_text="Seite zum Unterschreiben drucken").get_attribute("href")
    scan = desk.request.get(BASE + href).body()
    assert scan.startswith(b"%PDF")
    desk.set_input_files("input[name=scan]", files=[{"name": "scan.pdf", "mimeType": "application/pdf",
                                                      "buffer": scan}])
    desk.fill("input[name=number]", number)
    desk.click("button:has-text('Scan hochladen')")
    desk.wait_for_load_state()
    assert "Scan hochgeladen" in desk.content() and "Abgeschlossen" in desk.content()


def test_08_hr_onboard_by_email_links(desk, phone):
    """HR starts; the manager approves with software; the AutoCAD approver; HR confirms as a group."""
    login(desk, "hanna", "temporary-pass-1")
    desk.goto(BASE + "/")
    desk.click("text=HR Onboarding")
    desk.fill("#f-employee", "Tom Neu")
    desk.fill("#f-start_date", "2026-12-01")
    desk.fill("#f-manager", "max.mueller@company.de")
    before = seen()
    desk.click("button.primary:has-text('Antrag senden')")
    desk.wait_for_url(re.compile(r"/requests/\d+$"))
    url = desk.url

    phone.goto(mail.link(mail.wait("max.mueller@company.de", "genehmigen", seen=before, page=desk)))
    phone.check("input[name='f.software'][value='ERP']")
    phone.check("input[name='f.software'][value='AutoCAD']")
    phone.click("button.approve")
    phone.wait_for_load_state()
    phone.goto(mail.link(mail.wait("carl.cad@company.de", "Freigabe", seen=before, page=desk)))
    phone.click("button.approve")
    phone.wait_for_load_state()
    holger = mail.link(mail.wait("holger.hr@company.de", "bestätigen", seen=before, page=desk))
    hanna = mail.link(mail.wait("hanna.hr@company.de", "bestätigen", seen=before, page=desk))
    phone.goto(holger)
    phone.click(".decide button.primary")
    phone.wait_for_load_state()
    phone.goto(hanna)
    assert "funktioniert nicht mehr" in phone.content()  # Holger confirmed for the group
    mail.wait("ivo.it@company.de", "Zur Information", seen=before, page=desk)
    desk.goto(url)
    assert "Abgeschlossen" in desk.content()


def test_09_reminder_notice_and_expiry(admin, desk, phone):
    """One reminder after 5 working days, notice to the starter, then the link expires."""
    desk.goto(BASE + "/")
    desk.click("text=HR Onboarding")
    desk.fill("#f-employee", "Lea Lang")
    desk.fill("#f-start_date", "2027-01-04")
    desk.fill("#f-manager", "max.mueller@company.de")
    before = seen()
    desk.click("button.primary:has-text('Antrag senden')")
    desk.wait_for_url(re.compile(r"/requests/\d+$"))
    first = mail.link(mail.wait("max.mueller@company.de", "genehmigen", seen=before, page=desk))
    try:
        shift_clock(admin, 60 * 24 * 8)
        login(admin, "admin", ADMIN_PASSWORD)  # the session expired with the time jump
        tick_now(admin)
        reminder = mail.wait("max.mueller@company.de", "Erinnerung", seen=before, page=admin)
        phone.goto(first)
        assert "ersetzt" in phone.content()  # the old link was replaced
        assert mail.link(reminder) != first
        shift_clock(admin, 60 * 24 * 23)
        login(admin, "admin", ADMIN_PASSWORD)
        tick_now(admin)
        mail.wait("hanna.hr@company.de", "Noch offen", seen=before, page=admin)
        mail.wait("hanna.hr@company.de", "Keine Antwort", seen=before, page=admin)
        mail.wait("admin@company.de", "Keine Antwort", seen=before, page=admin)
    finally:
        shift_clock(admin, 0)
        login(admin, "admin", ADMIN_PASSWORD)
        login(desk, "hanna", STARTER_PASSWORD)


# ---------------------------------------------------------------- reports

def test_10_search_pdf_and_csv(admin):
    """Search by person, the full PDF report and the CSV export."""
    admin.goto(BASE + "/requests?person=Max")
    assert "HR Onboarding" in admin.content()
    admin.goto(BASE + "/requests?number=" + S["it_number"])
    href = admin.locator("td a", has_text="PDF").first.get_attribute("href")
    report = admin.request.get(BASE + href).body()
    text = pdf_text(report)
    assert S["it_number"] in text and "4711" in text and "Prüfung des Ereignisprotokolls" in text
    csv = admin.request.get(BASE + "/requests.csv").text()
    assert csv.startswith("﻿Antragsnummer;") and S["it_number"] in csv and "4711" not in csv


def test_11_tamper_check_finds_a_changed_entry(admin):
    """A changed entry in the event log is found; restored, the log checks out again."""
    con = db()
    row = con.execute("SELECT id, actor FROM events WHERE type = 'task.decided' ORDER BY id LIMIT 1").fetchone()
    con.execute("UPDATE events SET actor = 'someone-else' WHERE id = ?", (row["id"],))
    con.commit()
    try:
        page = post(admin, "/audit", {"action": "verify"}, token_from="/audit").text()
        assert "wurde verändert" in page and f"Eintrag: {row['id']}" in page
    finally:
        con.execute("UPDATE events SET actor = ? WHERE id = ?", (row["actor"], row["id"]))
        con.commit()
        con.close()
    assert "Kette ist unverändert" in post(admin, "/audit", {"action": "verify"}, token_from="/audit").text()


def test_12_backup_copy_is_complete_and_checks_out(admin):
    """The backup copy taken while the app runs has every request and an intact event log."""
    r = post(admin, "/test/backup")
    assert r.ok
    from wftool import audit
    from wftool.db import Database
    copy = Database(DATA / r.json()["file"])
    con = db()
    try:
        assert copy.one("SELECT COUNT(*) AS n FROM requests")["n"] == con.execute(
            "SELECT COUNT(*) FROM requests").fetchone()[0]
        assert audit.verify(copy)["ok"]
    finally:
        copy.close()
        con.close()


# ------------------------------------------------------------ image checks

def test_13_image_and_security_checks(admin, browser):
    """Not root, key file outside the data folder, security headers, no external resources, no faked IPs."""
    info = admin.request.get(BASE + "/test/info").json()
    assert info["key_inside_data"] is False
    if os.environ.get("E2E_EXPECT_NONROOT") == "1":
        assert info["uid"] not in (0, None)
    r = admin.request.get(BASE + "/login")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    for path in ("/", "/requests", "/admin/workflows/1", "/audit", "/admin/health"):
        html = admin.request.get(BASE + path).text()
        for url in re.findall(r'(?:src|href)="(https?://[^"]+)"', html):
            assert url.startswith(BASE), f"external resource on {path}: {url}"

    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(BASE + "/login")
    token = csrf_of(page.content())
    page.request.post(BASE + "/login", form={"_csrf": token, "username": "admin", "password": ADMIN_PASSWORD},
                      headers={"X-Forwarded-For": "9.9.9.9"})
    con = db()
    try:
        ip = con.execute("SELECT ip FROM events WHERE type LIKE 'login%' ORDER BY id DESC LIMIT 1").fetchone()[0]
    finally:
        con.close()
    assert ip and ip != "9.9.9.9"
    ctx.close()


@pytest.fixture(autouse=True)
def _no_page_errors(admin, desk, phone):
    yield
    for p in (admin, desk, phone):
        errors, p.errors[:] = list(p.errors), []
        assert not errors, errors
