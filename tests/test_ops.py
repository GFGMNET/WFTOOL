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


def test_manual_test_setup_starts_with_example_data(tmp_path, monkeypatch):
    monkeypatch.setenv("WFTOOL_TEST_MODE", "1")
    monkeypatch.setenv("WFTOOL_TEST_SEED", "1")
    monkeypatch.setenv("WFTOOL_TEST_ADMIN_PASSWORD", "test-admin-password")
    config = {"DATA_DIR": tmp_path / "d", "KEY_FILE": tmp_path / "k" / "key", "TESTING": True}
    app = create_app(config)
    db = app.extensions["wftool"]["db"]
    try:
        names = [r["name"] for r in db.all("SELECT w.name FROM workflows w JOIN workflow_versions v"
                                           " ON v.workflow_id = w.id ORDER BY w.name")]
        assert names == ["HR Onboard", "IT Assets Checklist"]
        assert db.one("SELECT COUNT(*) AS n FROM ad_users WHERE active = 1")["n"] >= 8
        assert {r["name"] for r in db.all("SELECT name FROM groups")} >= {"HR", "IT"}
        assert db.one("SELECT COUNT(*) AS n FROM list_items WHERE list_name = 'software'")["n"] >= 3
        assert db.one("SELECT COUNT(*) AS n FROM list_items WHERE list_name = 'monitor_models'")["n"] == 2
        assert (tmp_path / "d" / "import" / "users.csv").exists()  # visible in the import folder
        assert db.one("SELECT value FROM settings WHERE key = 'smtp_host'")["value"] == "mail"
        client = app.test_client()
        login(client, "hanna", "test-user-password")
        home = client.get("/").get_data(as_text=True)
        assert "HR Onboarding" in home and "IT-Ausstattung" not in home  # HR may start HR Onboard only
        other = app.test_client()
        login(other, "ina", "test-user-password")
        assert "IT-Ausstattung" in other.get("/").get_data(as_text=True)
    finally:
        db.close()
    workflows_before = 2
    app = create_app(config)  # a restart does not load the data again
    db = app.extensions["wftool"]["db"]
    try:
        assert db.one("SELECT COUNT(*) AS n FROM workflow_versions")["n"] == workflows_before
        assert db.one("SELECT COUNT(*) AS n FROM events WHERE type = 'test.seeded'")["n"] == 1
    finally:
        db.close()


def test_no_example_data_without_the_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("WFTOOL_TEST_MODE", "1")
    monkeypatch.delenv("WFTOOL_TEST_SEED", raising=False)
    app = create_app({"DATA_DIR": tmp_path / "d", "KEY_FILE": tmp_path / "k" / "key", "TESTING": True})
    db = app.extensions["wftool"]["db"]
    try:
        assert db.one("SELECT COUNT(*) AS n FROM workflows")["n"] == 0
    finally:
        db.close()
