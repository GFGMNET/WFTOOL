import shutil
from pathlib import Path

from wftool import audit, imports, masterdata

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "csv"


def csv_bytes(*lines, bom=False):
    text = "\n".join(lines) + "\n"
    return ("﻿" if bom else "").encode() + text.encode("utf-8")


USERS_HEADER = "samaccountname;email;displayname;department;manager_email;enabled"


def users_file(n, *, skip=(), disabled=()):
    rows = [USERS_HEADER]
    for i in range(n):
        if i in skip:
            continue
        rows.append(f"u{i};user{i}@co.de;User {i};Dept;;{'False' if i in disabled else 'True'}")
    return csv_bytes(*rows)


def test_example_files_import_cleanly(db, tmp_path):
    for f in EXAMPLES.glob("*.csv"):
        shutil.copy(f, tmp_path / f.name)
    results = {r["kind"]: r for r in imports.run_folder(db, tmp_path)}
    assert [r["status"] for r in results.values()] == ["imported"] * 3
    assert results["users"]["added"] == 9 and results["users"]["deactivated"] == 0
    assert masterdata.get_user(db, "otto.alt@company.de") is None  # enabled = False
    assert masterdata.get_user(db, "max.mueller@company.de")["display_name"] == "Max Müller"  # umlaut kept
    assert masterdata.get_user(db, "eva.engel@company.de")["manager_email"] == "max.mueller@company.de"
    assert masterdata.group_members(db, "HR") == ["hanna.hr@company.de", "holger.hr@company.de"]
    cad = masterdata.list_item(db, "software", "AutoCAD")
    assert cad["needs_approval"] == 1 and cad["approver_email"] == "carl.cad@company.de"
    assert masterdata.get_list(db, "software")["source"] == "import"

    again = imports.run_folder(db, tmp_path)
    assert [r["status"] for r in again] == ["unchanged"] * 3


def test_bom_and_column_order_and_extra_columns(db):
    data = csv_bytes("enabled;Email;displayname;samaccountname;department;manager_email;extra",
                     "True;A@Co.de;Anna;a;;;x", bom=True)
    r = imports.import_bytes(db, "users", data, file_name="users.csv")
    assert r["status"] == "imported" and masterdata.get_user(db, "a@co.de")


def test_rejected_files_change_nothing(db):
    imports.import_bytes(db, "users", users_file(3), file_name="users.csv")
    cases = {
        "empty": b"",
        "header only": csv_bytes(USERS_HEADER),
        "comma separated": "samaccountname,email,displayname,department,manager_email,enabled\n".encode(),
        "not utf-8": (USERS_HEADER + "\nx;y@co.de;M\xfcller;;;True\n").encode("latin-1"),
    }
    for name, data in cases.items():
        r = imports.import_bytes(db, "users", data, file_name="users.csv")
        assert r["status"] == "rejected", name
    comma = imports.import_bytes(db, "users", cases["comma separated"], file_name="users.csv")
    assert "semicolon" in comma["message"]
    assert len([u for u in db.all("SELECT * FROM ad_users WHERE active = 1")]) == 3


def test_ten_percent_protection(db):
    imports.import_bytes(db, "users", users_file(30), file_name="users.csv")
    # 30 active users -> at most 3 may be deactivated at once
    ok = imports.import_bytes(db, "users", users_file(30, skip={0, 1}, disabled={2}), file_name="users.csv")
    assert ok["status"] == "imported" and ok["deactivated"] == 3
    big = imports.import_bytes(db, "users", users_file(30, skip=set(range(10, 30))), file_name="users.csv")
    assert big["status"] == "rejected" and "Nothing was changed" in big["message"]
    assert db.one("SELECT COUNT(*) AS n FROM ad_users WHERE active = 1")["n"] == 27
    masterdata.set_setting(db, "import_max_deactivate_percent", "100")
    assert imports.import_bytes(db, "users", users_file(30, skip=set(range(10, 30))),
                                file_name="users.csv")["status"] == "imported"


def test_never_delete_and_bad_rows_skipped(db):
    imports.import_bytes(db, "users", users_file(20), file_name="users.csv")
    data = users_file(20, skip={5}) + b"x;not-an-email;Bad;;;True\nz;user1@co.de;Twice;;;True\n"
    r = imports.import_bytes(db, "users", data, file_name="users.csv")
    assert r["status"] == "imported" and r["deactivated"] == 1
    assert {row["reason"] for row in r["rejected_rows"]} == {"email missing or invalid", "email appears twice"}
    gone = masterdata.get_user(db, "user5@co.de", active_only=False)
    assert gone and gone["active"] == 0  # still there, only inactive


def test_groups_import(db):
    imports.import_bytes(db, "users", users_file(20), file_name="users.csv")
    r = imports.import_bytes(db, "groups", csv_bytes("group;member_email", "HR;user1@co.de", "HR;user2@co.de",
                                                     "IT;user3@co.de", "IT;ghost@co.de"), file_name="groups.csv")
    assert r["status"] == "imported" and r["added"] == 2
    assert r["rejected_rows"][0]["reason"] == "ghost@co.de is not an active user"
    r = imports.import_bytes(db, "groups", csv_bytes("group;member_email", "HR;user1@co.de", "HR;user2@co.de",
                                                     "HR;user4@co.de"), file_name="groups.csv")
    assert r["status"] == "imported"
    assert masterdata.get_group(db, "IT") is None  # missing from file -> inactive
    assert masterdata.group_members(db, "HR") == ["user1@co.de", "user2@co.de", "user4@co.de"]


def test_software_import_marks_items_without_approver_unusable(db):
    imports.import_bytes(db, "users", users_file(5), file_name="users.csv")
    r = imports.import_bytes(db, "software", csv_bytes(
        "software;needs_approval;approver_email", "ERP;nein;", "AutoCAD;ja;user1@co.de",
        "Visio;yes;", "Photoshop;yes;nobody@co.de", "Bad;maybe;"), file_name="software.csv")
    assert r["status"] == "imported"
    assert masterdata.list_item(db, "software", "AutoCAD")["active"] == 1
    assert masterdata.list_item(db, "software", "Visio")["active"] == 0
    assert masterdata.list_item(db, "software", "Photoshop")["active"] == 0
    assert masterdata.list_item(db, "software", "Bad") is None
    reasons = " ".join(x["reason"] for x in r["rejected_rows"])
    assert "not usable until fixed" in reasons and "'maybe' is not yes/no" in reasons


def test_every_run_is_logged(db):
    imports.import_bytes(db, "users", users_file(3), file_name="users.csv")
    imports.import_bytes(db, "users", b"", file_name="users.csv")
    kinds = [(r["kind"], r["status"]) for r in imports.history(db)]
    assert kinds == [("users", "rejected"), ("users", "imported")]
    events = [e for e in db.all("SELECT type FROM events WHERE type = 'import.users'")]
    assert len(events) == 2
    assert audit.verify(db)["ok"]
