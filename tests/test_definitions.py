import copy

import pytest

from wftool import masterdata, workflows
from wftool.definitions import validate
from wftool.errors import DefinitionError, PublishError

from conftest import actor, load_example, seed


@pytest.mark.parametrize("name", ["hr_onboard.json", "it_assets_checklist.json"])
def test_examples_are_valid(name):
    assert validate(load_example(name)) == []


def _hr():
    return copy.deepcopy(load_example("hr_onboard.json"))


def test_approval_cannot_go_to_a_group():
    d = _hr()
    d["steps"][0]["who"] = {"kind": "group", "group": "HR"}
    assert any("never a group" in p for p in validate(d))


def test_group_is_fine_for_confirm_and_notify():
    assert validate(_hr()) == []  # forward_to_it (confirm) and copy_to_* (notify) use groups


def test_recipient_field_must_be_filled_before_the_step():
    d = _hr()
    d["steps"][0]["who"] = {"kind": "field", "field": "software"}
    problems = validate(d)
    assert any("person or email field" in p for p in problems)
    d = _hr()
    d["fields"].append({"id": "deputy", "type": "person", "label": "Deputy"})
    d["steps"][1]["fields"] = []
    d["steps"][0]["fields"] = ["software", "deputy"]
    d["steps"][0]["who"] = {"kind": "field", "field": "deputy"}
    assert any("filled only at or after this step" in p for p in validate(d))


def test_signature_field_must_belong_to_a_sign_step():
    d = load_example("it_assets_checklist.json")
    d["steps"][0]["fields"] = []
    assert any("must belong to a sign step" in p for p in validate(d))


def test_approve_per_item_needs_a_list_field_and_item_approver():
    d = _hr()
    d["steps"][1]["items"] = "employee"
    d["steps"][1]["who"] = {"kind": "starter"}
    problems = validate(d)
    assert any("with a list" in p for p in problems)
    assert any("approver of each item" in p for p in problems)


def test_ids_labels_and_types_are_checked():
    d = _hr()
    d["fields"][0]["id"] = "Bad Id"
    d["fields"][1]["type"] = "colour"
    d["steps"][2]["id"] = "manager_approval"
    d["steps"][0]["on_reject"] = "previous"
    problems = validate(d)
    assert any("must start with a letter" in p for p in problems)
    assert any("unknown type 'colour'" in p for p in problems)
    assert any("used twice" in p for p in problems)
    assert any("first step cannot send" in p for p in problems)


def test_publish_blocked_when_an_item_has_no_approver(db):
    seed(db)
    masterdata.upsert_list_item(db, "software", "AutoCAD", needs_approval=True, approver_email=None)
    workflows.save_draft(db, _hr(), by="admin@co.de")
    with pytest.raises(PublishError) as exc:
        workflows.publish(db, "HR Onboard", by="admin@co.de")
    assert any("'AutoCAD' needs approval but has no approver" in p for p in exc.value.details)


def test_publish_blocked_for_unknown_group_and_list(db):
    seed(db)
    d = _hr()
    d["steps"][2]["who"] = [{"kind": "group", "group": "Purchasing"}]
    d["fields"][4]["list"] = "licences"
    workflows.save_draft(db, d, by="admin@co.de")
    with pytest.raises(PublishError) as exc:
        workflows.publish(db, "HR Onboard", by="admin@co.de")
    text = " ".join(exc.value.details)
    assert "group 'Purchasing' does not exist" in text
    assert "list 'licences' does not exist" in text


def test_versions_and_running_requests(seeded):
    from wftool.crypto import Cipher
    from wftool.engine import Engine

    engine = Engine(seeded, Cipher.generate())
    v1 = workflows.latest_version(seeded, "HR Onboard")
    assert workflows.publish(seeded, "HR Onboard", by="admin@co.de")["version"] == 1  # unchanged draft

    old = engine.start("HR Onboard", actor("hr1@co.de"),
                       {"employee": "Tom", "start_date": "2026-12-01", "manager": "mgr@co.de"})
    d = _hr()
    d["fields"].append({"id": "phone", "type": "text", "label": "Phone"})
    workflows.save_draft(seeded, d, by="admin@co.de")
    v2 = workflows.publish(seeded, "HR Onboard", by="admin@co.de")
    assert v2["version"] == 2

    new = engine.start("HR Onboard", actor("hr1@co.de"),
                       {"employee": "Ann", "start_date": "2026-12-01", "manager": "mgr@co.de", "phone": "1"})
    assert engine.get_request(old["id"])["workflow_version_id"] == v1["id"]
    assert new["workflow_version_id"] == v2["id"]


def test_export_and_import_round_trip(seeded, tmp_path):
    text = workflows.export_workflow(seeded, "IT Assets Checklist")
    from wftool.db import Database
    other = Database(tmp_path / "other.db")
    seed(other)
    version = workflows.import_workflow(other, text, by="admin@co.de", publish_now=True)
    assert version["version"] == 1
    assert version["fingerprint"] == workflows.latest_version(seeded, "IT Assets Checklist")["fingerprint"]
    with pytest.raises(DefinitionError):
        workflows.import_workflow(other, "{not json", by="admin@co.de")
