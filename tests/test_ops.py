"""Phase 6: proxy headers, test mode, backup command, health page."""

import re

from wftool import audit, clock
from wftool.db import Database
from wftool.web import create_app

from conftest import seed
from test_web import csrf, first_login, login


def test_faked_forwarded_header_is_ignored_without_a_proxy(client, app, monkeypatch):
    first_login(client)
    client.post("/logout", data={"_csrf": csrf(client, "/")})
    client.post("/login", data={"username": "admin", "password": "new-admin-password", "_csrf": csrf(client, "/login")},
                headers={"X-Forwarded-For": "1.2.3.4"}, environ_base={"REMOTE_ADDR": "10.0.0.7"})
    row = app.extensions["wftool"]["db"].one("SELECT ip FROM events WHERE type = 'login.ok' ORDER BY id DESC LIMIT 1") \
        or app.extensions["wftool"]["db"].one("SELECT ip FROM events WHERE ip IS NOT NULL ORDER BY id DESC LIMIT 1")
    assert row["ip"] == "10.0.0.7"


def test_proxy_headers_only_when_switched_on(tmp_path, monkeypatch):
    monkeypatch.setenv("WFTOOL_BEHIND_PROXY", "1")
    app = create_app({"DATA_DIR": tmp_path / "d", "KEY_FILE": tmp_path / "k" / "key", "TESTING": True})
    assert type(app.wsgi_app).__name__ == "ProxyFix"
    app.extensions["wftool"]["db"].close()


def test_test_mode_is_off_by_default(client):
    first_login(client)
    assert client.post("/test/tick", data={"_csrf": csrf(client, "/")}).status_code == 404
    assert "TEST MODE" not in client.get("/").get_data(as_text=True)


def test_test_mode_helpers(tmp_path, monkeypatch):
    monkeypatch.setenv("WFTOOL_TEST_MODE", "1")
    monkeypatch.setenv("WFTOOL_TEST_ADMIN_PASSWORD", "test-admin-password")
    app = create_app({"DATA_DIR": tmp_path / "d", "KEY_FILE": tmp_path / "k" / "key", "TESTING": True})
    client = app.test_client()
    try:
        assert client.post("/test/tick").status_code == 302  # admins only
        login(client, "admin", "test-admin-password")
        home = client.get("/").get_data(as_text=True)
        assert "TEST MODE" in home
        token = csrf(client, "/")
        assert client.post("/test/tick", data={"_csrf": token}).get_json()["ok"]
        before = clock.now()
        shifted = client.post("/test/clock", data={"_csrf": token, "minutes": str(60 * 24 * 8)}).get_json()["now"]
        assert shifted[:10] != before.isoformat()[:10]
        login(client, "admin", "test-admin-password")  # 8 days later the session has expired (60 minutes)
        token = csrf(client, "/")
        client.post("/test/clock", data={"_csrf": token, "minutes": "0"})
        info = client.get("/test/info").get_json()
        assert info["key_inside_data"] is False and info["behind_proxy"] is False
        assert client.post("/test/backup", data={"_csrf": token}).get_json()["file"] == "backup-test.db"
        assert "Testmodus ist an" in client.get("/admin/health").get_data(as_text=True)
    finally:
        clock.set_offset(None)
        app.extensions["wftool"]["db"].close()


def test_backup_is_a_working_copy(seeded, tmp_path):
    seed_db = seeded
    target = tmp_path / "copy.db"
    seed_db.backup(target)
    copy = Database(target)
    assert copy.one("SELECT COUNT(*) AS n FROM ad_users")["n"] == seed_db.one("SELECT COUNT(*) AS n FROM ad_users")["n"]
    assert audit.verify(copy)["ok"]
    copy.close()


def test_backup_command(tmp_path, monkeypatch, capsys):
    from wftool.__main__ import main
    monkeypatch.setenv("WFTOOL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WFTOOL_KEY_FILE", str(tmp_path / "keys" / "k.key"))
    main(["backup", str(tmp_path / "b.db")])
    assert (tmp_path / "b.db").exists() and "key file separately" in capsys.readouterr().out


def test_health_page(client, app):
    seed(app.extensions["wftool"]["db"])
    first_login(client)
    page = client.get("/admin/health").get_data(as_text=True)
    assert "Letzter Import" in page and "Speicherplatz" in page and re.search(r"\d+\.\d GB", page)
