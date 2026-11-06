from datetime import date

import pytest

from wftool import accounts, applists, masterdata, settings, workflows
from wftool.crypto import Cipher
from wftool.errors import DefinitionError, StateError, ValidationError

from fake_smtp import FakeSMTP


@pytest.fixture
def smtp():
    server = FakeSMTP()
    yield server
    server.close()


def test_settings_are_checked_and_logged(db):
    cipher = Cipher.generate()
    with pytest.raises(ValidationError) as exc:
        settings.save(db, cipher, {"smtp_port": "abc", "base_url": "wftool.local", "company_domains": "company"},
                      by="admin")
    text = " ".join(exc.value.details)
    assert "smtp_port" in text and "base_url" in text and "company_domains" in text

    changed = settings.save(db, cipher, {"smtp_host": "relay.company.local", "smtp_sender": "WF@Company.de",
                                         "company_domains": "company.de; @Company-Group.com",
                                         "base_url": "https://wftool.company.local/", "admin_email": "it@company.de"},
                            by="admin")
    assert masterdata.get_setting(db, "company_domains") == "company.de, company-group.com"
    assert masterdata.get_setting(db, "base_url") == "https://wftool.company.local"
    assert masterdata.get_setting(db, "smtp_sender") == "wf@company.de"
    assert changed["smtp_host"] == "relay.company.local"
    assert settings.missing_required(db) == []


def test_smtp_password_is_encrypted_and_never_logged(db):
    cipher = Cipher.generate()
    settings.save(db, cipher, {"smtp_user": "svc", "smtp_password": "S3cret!pw"}, by="admin")
    stored = masterdata.get_setting(db, "smtp_password")
    assert stored and "S3cret!pw" not in stored
    assert settings.smtp_config(db, cipher)["password"] == "S3cret!pw"
    assert settings.current(db)["smtp_password"] == "(set)"
    settings.save(db, cipher, {"smtp_password": ""}, by="admin")  # empty keeps it
    assert settings.smtp_config(db, cipher)["password"] == "S3cret!pw"
    settings.save(db, cipher, {}, by="admin", clear_secrets=["smtp_password"])
    assert settings.smtp_config(db, cipher)["password"] == ""
    log = " ".join(r["data_json"] for r in db.all("SELECT data_json FROM events"))
    assert "S3cret!pw" not in log


def test_test_email_through_a_relay_without_login(db, smtp):
    cipher = Cipher.generate()
    settings.save(db, cipher, {"smtp_host": "127.0.0.1", "smtp_port": str(smtp.port), "smtp_security": "none",
                               "smtp_sender": "wftool@company.de"}, by="admin")
    ok, message = settings.send_test_email(db, cipher, "it@company.de", by="admin")
    assert ok, message
    assert smtp.messages[0]["to"] == ["it@company.de"]
    assert smtp.auth_attempts == 0  # user empty: no login attempted


def test_test_email_reports_failure(db):
    cipher = Cipher.generate()
    settings.save(db, cipher, {"smtp_host": "127.0.0.1", "smtp_port": "1", "smtp_sender": "wf@company.de"},
                  by="admin")
    ok, message = settings.send_test_email(db, cipher, "it@company.de", by="admin")
    assert not ok and "Sending failed" in message


def test_saxony_working_days(db):
    assert settings.easter_sunday(2027) == date(2027, 3, 28)
    assert date(2027, 11, 17) in settings.saxony_holidays(2027)  # Buß- und Bettag
    # Fri 30 Oct 2026 + 1 working day: Sat, Sun skipped; Sat 31 Oct is Reformationstag anyway -> Mon 2 Nov
    assert settings.add_working_days(db, date(2026, 10, 30), 1) == date(2026, 11, 2)
    # 17 Nov 2026 (Tue) + 1 -> 18 Nov is Buß- und Bettag -> Thu 19 Nov
    assert settings.add_working_days(db, date(2026, 11, 17), 1) == date(2026, 11, 19)
    settings.save(db, Cipher.generate(), {"extra_holidays": "2026-12-24\n2026-12-31"}, by="admin")
    assert not settings.is_working_day(db, date(2026, 12, 24))


def test_app_lists(db):
    with pytest.raises(ValidationError):
        applists.create_list(db, "Hardware Models", "Hardware", by="admin")
    applists.create_list(db, "hardware_models", "Hardware models", by="admin")
    a = applists.add_item(db, "hardware_models", "Latitude 5450", by="admin")
    b = applists.add_item(db, "hardware_models", "ThinkPad T14", by="admin")
    with pytest.raises(ValidationError):
        applists.add_item(db, "hardware_models", "Latitude 5450", by="admin")
    applists.move_item(db, b["id"], -1, by="admin")
    assert [i["value"] for i in masterdata.list_items(db, "hardware_models")] == ["ThinkPad T14", "Latitude 5450"]
    applists.rename_item(db, a["id"], "Dell Latitude 5450", by="admin")
    item = masterdata.list_item(db, "hardware_models", "Latitude 5450")
    assert item["label"] == "Dell Latitude 5450"  # stored value unchanged for old requests
    applists.set_item_active(db, b["id"], False, by="admin")
    assert [i["value"] for i in masterdata.list_items(db, "hardware_models")] == ["Latitude 5450"]

    masterdata.ensure_list(db, "software", "Software", source="import")
    with pytest.raises(StateError):
        applists.add_item(db, "software", "Paint", by="admin")


def test_who_may_start(seeded):
    db = seeded
    with pytest.raises(DefinitionError):
        workflows.set_start_groups(db, "HR Onboard", ["Nobody"], by="admin")
    workflows.set_start_groups(db, "HR Onboard", ["HR"], by="admin")
    workflows.set_start_groups(db, "IT Assets Checklist", ["IT"], by="admin")
    accounts.ensure_default_admin(db)
    hanna = accounts.create_account(db, "hanna", "twelve-chars-ok", "starter", email="hr1@co.de", by="admin")
    ina = accounts.create_account(db, "ina", "twelve-chars-ok", "starter", email="it1@co.de", by="admin")
    audi = accounts.create_account(db, "audi", "twelve-chars-ok", "auditor", email="hr2@co.de", by="admin")
    admin = accounts.authenticate(db, "admin", "admin")
    assert workflows.startable_workflows(db, hanna) == ["HR Onboard"]
    assert workflows.startable_workflows(db, ina) == ["IT Assets Checklist"]
    assert workflows.startable_workflows(db, audi) == []
    assert workflows.startable_workflows(db, admin) == ["HR Onboard", "IT Assets Checklist"]
    masterdata.upsert_group(db, "HR", ["hr2@co.de"])  # Hanna leaves the HR group in AD
    assert workflows.startable_workflows(db, hanna) == []
