import json
from pathlib import Path

import pytest

from wftool import masterdata, workflows
from wftool.crypto import Cipher
from wftool.db import Database
from wftool.engine import Actor, Engine
from wftool.notify import RecordingNotifier

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "workflows"
PNG = b"\x89PNG\r\n\x1a\nfake-signature-image"


def load_example(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def seed(db):
    """Example master data, as the AD and software CSV imports would deliver it."""
    users = [
        ("hr1@co.de", "Hanna HR", None), ("hr2@co.de", "Holger HR", None),
        ("it1@co.de", "Ina IT", None), ("it2@co.de", "Ivo IT", None),
        ("mgr@co.de", "Max Manager", None), ("emp@co.de", "Eva Employee", "mgr@co.de"),
        ("cad@co.de", "Carl CAD", None), ("vis@co.de", "Vera Visio", None),
        ("admin@co.de", "Adam Admin", None),
    ]
    for email, name, manager in users:
        masterdata.upsert_user(db, email, name, manager_email=manager)
    masterdata.upsert_group(db, "HR", ["hr1@co.de", "hr2@co.de"])
    masterdata.upsert_group(db, "IT", ["it1@co.de", "it2@co.de"])
    masterdata.ensure_list(db, "software", "Software", source="import")
    masterdata.upsert_list_item(db, "software", "ERP")
    masterdata.upsert_list_item(db, "software", "AutoCAD", needs_approval=True, approver_email="cad@co.de")
    masterdata.upsert_list_item(db, "software", "Visio", needs_approval=True, approver_email="vis@co.de")
    masterdata.ensure_list(db, "hardware_models", "Hardware models")
    masterdata.upsert_list_item(db, "hardware_models", "Latitude 5450")
    masterdata.ensure_list(db, "hardware_vendors", "Hardware vendors")
    masterdata.upsert_list_item(db, "hardware_vendors", "Dell")
    masterdata.set_setting(db, "admin_email", "admin@co.de")
    masterdata.set_setting(db, "company_domains", "co.de")


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "wftool.db")
    yield database
    database.close()


@pytest.fixture
def seeded(db):
    seed(db)
    for name in ("hr_onboard.json", "it_assets_checklist.json"):
        workflows.save_draft(db, load_example(name), by="admin@co.de")
        workflows.publish(db, load_example(name)["name"], by="admin@co.de")
    return db


@pytest.fixture
def notifier():
    return RecordingNotifier()


@pytest.fixture
def engine(seeded, notifier):
    return Engine(seeded, Cipher.generate(), notifier)


def actor(email, ip="10.0.0.5", ua="pytest-browser"):
    return Actor(email, ip=ip, user_agent=ua, via="link")


def open_tasks(req, step=None):
    return [t for t in req["tasks"] if t["status"] == "open" and (step is None or t["step_id"] == step)]
