"""Example data for the hands-on test setup (`--profile manual`). Test mode only.

On the first start of an empty test database (WFTOOL_TEST_MODE=1 and WFTOOL_TEST_SEED=1) this loads:
settings for the test mail server, the example CSV files (copied into the import folder and imported),
the hardware lists, both example workflows (published), who may start, and test accounts.
It never runs again on that database, and never in production (no test mode there).
"""

import json
import os
import shutil
from pathlib import Path

from . import accounts, applists, audit, clock, imports, masterdata, workflows

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
USER_PASSWORD = "test-user-password"

ACCOUNTS = [  # username, role, email (must match the example users.csv)
    ("hanna", "starter", "hanna.hr@company.de"),
    ("ina", "starter", "ina.it@company.de"),
    ("auditor", "auditor", "audit@company.de"),
]

LISTS = [
    ("laptop_models", "Laptop-Modelle", ["Latitude 5450", "ThinkPad T14", "EliteBook 840"]),
    ("docking_models", "Dockingstation-Modelle", ["WD19S", "ThinkPad USB-C Dock"]),
    ("monitor_models", "Monitor-Modelle", ["P2423", "U2724D"]),
    ("smartphone_models", "Smartphone-Modelle", ["iPhone 15", "Galaxy A55"]),
    ("hardware_vendors", "Hersteller", ["Dell", "Lenovo", "HP", "Apple", "Samsung"]),
]

WORKFLOWS = [("hr_onboard.json", ["HR"]), ("it_assets_checklist.json", ["IT"])]


def needed(db):
    return (not masterdata.get_setting(db, "test_seeded", "")
            and db.one("SELECT COUNT(*) AS n FROM workflows")["n"] == 0
            and db.one("SELECT COUNT(*) AS n FROM requests")["n"] == 0)


def seed(db, import_dir, examples=EXAMPLES):
    """Load the example data. Returns a short summary."""
    by = "test-setup"
    settings = {
        "smtp_host": os.environ.get("WFTOOL_TEST_SMTP_HOST", "mail"),
        "smtp_port": os.environ.get("WFTOOL_TEST_SMTP_PORT", "1025"),
        "smtp_security": "none", "smtp_tls_verify": "yes", "smtp_sender": "wftool@company.de",
        "company_domains": "company.de", "admin_email": "admin@company.de", "default_email_language": "de",
        "base_url": os.environ.get("WFTOOL_TEST_BASE_URL", "http://localhost:8080"),
    }
    for key, value in settings.items():
        masterdata.set_setting(db, key, value)

    import_dir = Path(import_dir)
    import_dir.mkdir(parents=True, exist_ok=True)
    for f in sorted((examples / "csv").glob("*.csv")):
        if not (import_dir / f.name).exists():  # never overwrite the tester's own files
            shutil.copy(f, import_dir / f.name)
    results = imports.run_folder(db, import_dir)

    for name, label, items in LISTS:
        if not masterdata.get_list(db, name):
            applists.create_list(db, name, label, by=by)
        for item in items:
            if not masterdata.list_item(db, name, item):
                applists.add_item(db, name, item, by=by)

    published = []
    for file, groups in WORKFLOWS:
        definition = json.loads((examples / "workflows" / file).read_text(encoding="utf-8"))
        workflows.save_draft(db, definition, by=by)
        workflows.publish(db, definition["name"], by=by)
        workflows.set_start_groups(db, definition["name"], groups, by=by)
        published.append(definition["name"])

    for username, role, email in ACCOUNTS:
        if not db.one("SELECT id FROM accounts WHERE username = ?", (username,)):
            accounts.create_account(db, username, USER_PASSWORD, role, email=email, language="de", by=by)
            db.execute("UPDATE accounts SET must_change_password = 0 WHERE username = ?", (username,))

    masterdata.set_setting(db, "test_seeded", clock.now_iso())
    summary = {"imports": {r["file"]: r["status"] for r in results}, "workflows": published,
               "accounts": [a[0] for a in ACCOUNTS]}
    audit.record(db, "test.seeded", actor=by, data=summary)
    return summary
