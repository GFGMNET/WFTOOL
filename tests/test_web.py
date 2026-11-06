import re
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from wftool import clock
from wftool.web import create_app

from fake_smtp import FakeSMTP

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "csv"


@pytest.fixture
def app(tmp_path):
    app = create_app({"DATA_DIR": tmp_path / "data", "KEY_FILE": tmp_path / "keys" / "wftool.key", "TESTING": True})
    yield app
    clock.set_now(None)
    app.extensions["wftool"]["db"].close()


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client, path):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)


def login(client, username="admin", password="admin"):
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf(client, "/login")})


def first_login(client, *, already_signed_in=False):
    if not already_signed_in:
        login(client)
    token = csrf(client, "/password")
    resp = client.post("/password", data={"_csrf": token, "old_password": "admin",
                                          "new_password": "new-admin-password", "repeat_password": "new-admin-password"})
    assert resp.status_code == 302
    return token


def test_key_and_data_in_separate_places(app, tmp_path):
    assert (tmp_path / "keys" / "wftool.key").exists()
    assert (tmp_path / "data" / "wftool.db").exists()
    assert (tmp_path / "data" / "import").is_dir()


def test_default_admin_is_forced_to_change_password(client):
    assert client.get("/").status_code == 302
    resp = login(client)
    assert resp.status_code == 302
    resp = client.get("/admin/settings")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/password")
    first_login(client, already_signed_in=True)
    page = client.get("/").get_data(as_text=True)
    assert "Bitte prüfen" in page and "Verwaltung" in page  # German by default
    assert client.get("/admin/settings").status_code == 200


def test_wrong_password_and_lockout_messages(client):
    for _ in range(4):
        assert login(client, password="nope").status_code == 401
    resp = login(client, password="nope")
    assert "Zu viele Fehlversuche" in resp.get_data(as_text=True)


def test_post_without_csrf_token_is_refused(client):
    first_login(client)
    resp = client.post("/admin/lists", data={"name": "x", "label": "X"})
    assert resp.status_code == 302
    assert "x" not in [r for r in client.get("/admin/lists").get_data(as_text=True).split() if r == "x"]


def test_session_ends_after_60_minutes_without_activity(client):
    first_login(client)
    clock.set_now(clock.now() + timedelta(minutes=61))
    resp = client.get("/admin/accounts")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_starter_cannot_open_admin_pages(client):
    token = first_login(client)
    client.post("/admin/accounts", data={"_csrf": token, "username": "hanna", "role": "starter",
                                         "email": "hanna.hr@company.de", "language": "en",
                                         "password": "temporary-pass-1"})
    client.post("/logout", data={"_csrf": token})
    login(client, "hanna", "temporary-pass-1")
    token = csrf(client, "/password")
    client.post("/password", data={"_csrf": token, "old_password": "temporary-pass-1",
                                   "new_password": "hannas-password", "repeat_password": "hannas-password"})
    assert client.get("/admin/settings").status_code == 403
    assert "No workflow is open to you yet" in client.get("/").get_data(as_text=True)  # English chosen


def test_admin_pages_import_lists_and_settings(app, client):
    token = first_login(client)
    for f in EXAMPLES.glob("*.csv"):
        shutil.copy(f, app.config["IMPORT_DIR"] / f.name)
    page = client.post("/admin/imports", data={"_csrf": token}, follow_redirects=True).get_data(as_text=True)
    assert "users.csv: importiert" in page
    people = client.get("/admin/people?q=Müller").get_data(as_text=True)
    assert "Max Müller" in people and "Eva Engel" not in people

    client.post("/admin/lists", data={"_csrf": token, "name": "hardware_models", "label": "Hardware-Modelle"})
    client.post("/admin/lists/hardware_models", data={"_csrf": token, "action": "add", "label": "Latitude 5450"})
    assert "Latitude 5450" in client.get("/admin/lists/hardware_models").get_data(as_text=True)
    software = client.get("/admin/lists/software").get_data(as_text=True)
    assert "carl.cad@company.de" in software and "software.csv" in software

    smtp = FakeSMTP()
    try:
        resp = client.post("/admin/settings", data={
            "_csrf": token, "smtp_host": "127.0.0.1", "smtp_port": str(smtp.port), "smtp_security": "none",
            "smtp_tls_verify": "yes", "smtp_sender": "wftool@company.de", "company_domains": "company.de",
            "base_url": "https://wftool.company.local", "admin_email": "it@company.de",
            "default_email_language": "de", "link_validity_days": "14", "reminder_working_days": "5",
            "starter_notice_working_days": "10", "import_max_deactivate_percent": "10"})
        assert resp.status_code == 302
        page = client.post("/admin/settings/test-email", data={"_csrf": token, "to": "it@company.de"},
                           follow_redirects=True).get_data(as_text=True)
        assert "Test email sent to it@company.de." in page
        assert smtp.messages and smtp.auth_attempts == 0
    finally:
        smtp.close()

    bad = client.post("/admin/settings", data={"_csrf": token, "smtp_port": "seventy", "base_url": "x"})
    assert bad.status_code == 200 and "field-error" in bad.get_data(as_text=True)


def test_security_headers(client):
    resp = client.get("/login")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
