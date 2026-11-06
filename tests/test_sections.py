"""Conditional and repeatable sections, located problems and email texts (phase 3)."""

import copy
import json

import pytest

from wftool import emails, workflows
from wftool.crypto import Cipher
from wftool.definitions import Problem, field_owner, start_fields, step_fields, validate
from wftool.engine import Engine
from wftool.errors import ValidationError

from conftest import PNG, actor, load_example, open_tasks


def _it():
    return copy.deepcopy(load_example("it_assets_checklist.json"))


def _field(d, fid):
    return next(f for f in d["fields"] if f["id"] == fid)


def _problems(d, needle):
    return [p for p in validate(d) if needle in p]


# ------------------------------------------------------------- validation

def test_rebuilt_example_is_valid_and_sections_belong_to_start():
    d = _it()
    assert validate(d) == []
    owner = field_owner(d)
    assert owner["monitors"] is None and owner["laptop_model"] is None and owner["signature"] == 0
    ids = [f["id"] for f in start_fields(d)]
    assert "monitors" in ids and "monitor_serial" in ids and "signature" not in ids
    assert [f["id"] for f in step_fields(d, 0)] == ["signature"]


def test_section_rules():
    d = _it()
    _field(d, "laptop_model")["section"] = "nowhere"
    assert _problems(d, "section 'nowhere' does not exist")

    d = _it()
    _field(d, "docking")["section"] = "laptop"
    assert _problems(d, "cannot be inside another section")

    d = _it()
    _field(d, "signature")["section"] = "laptop"
    assert _problems(d, "signature field cannot be inside a section")

    d = _it()
    d["fields"].append({"id": "empty", "type": "section", "label": "Empty"})
    assert _problems(d, "section has no fields yet")

    d = _it()
    _field(d, "laptop")["show_when"] = {"field": "laptop_serial", "op": "is_set"}
    assert _problems(d, "only depend on a field outside sections")

    d = _it()
    _field(d, "monitors")["repeat"] = {"min": 4, "max": 2}
    assert _problems(d, "minimum count is larger")
    _field(d, "monitors")["repeat"] = {"min": 0, "max": 99}
    assert _problems(d, "at most 50 times")

    d = _it()
    _field(d, "user")["repeat"] = {"min": 1}
    assert _problems(d, "Only a section can be shown on a condition or repeated")


def test_section_fields_share_one_step_and_condition_comes_first():
    d = _it()
    d["steps"].insert(0, {"id": "it_fill", "type": "fill", "label": "IT", "who": {"kind": "starter"},
                          "fields": ["laptop_serial"]})
    assert _problems(d, "All fields of section 'laptop' must be filled by the same step")

    d["steps"][0]["fields"] = ["laptop_model", "laptop_vendor", "laptop_serial"]
    assert validate(d) == []  # the whole section moves to step 1; its checkbox is filled earlier

    d["steps"][0]["fields"] = ["laptop_issued"]
    assert _problems(d, "filled only at or after this step")  # section at start, checkbox later


def test_repeatable_fields_cannot_drive_steps_or_emails():
    d = _it()
    d["fields"].append({"id": "monitor_owner", "type": "person", "label": "Owner", "section": "monitors"})
    d["steps"][1]["who"].append({"kind": "field", "field": "monitor_owner"})
    d["steps"][1]["when"] = {"field": "monitor_serial", "op": "is_set"}
    problems = _problems(d, "inside a repeatable section")
    assert {p.kind for p in problems} == {"step"} and len(problems) == 2


def test_email_placeholders_are_checked():
    d = _it()
    d["steps"][0]["email"] = {"subject": {"de": "Unterschrift {request_no} für {user}"},
                              "body": {"de": "{link} {pin} {employe}", "en": "{monitor_serial}"}}
    d["steps"][1]["email"] = {"body": "Kopie {link}"}
    text = " ".join(validate(d))
    assert "secret field {pin} must not appear" in text
    assert "Unknown placeholder {employe}" in text
    assert "{monitor_serial} is inside a repeatable section" in text
    assert "A notify email has no link" in text
    assert "{user}" not in text and "{request_no}" not in text


def test_problems_know_where_they_are():
    d = _it()
    _field(d, "laptop_model")["list"] = None
    d["steps"][0]["who"] = {"kind": "group", "group": "IT"}
    problems = validate(d)
    field_problem = next(p for p in problems if p.kind == "field")
    assert (field_problem.ref, field_problem.index) == ("laptop_model", 3)
    assert field_problem.template == "Choose a list or enter fixed options (one of the two)."
    step_problem = next(p for p in problems if p.kind == "step")
    assert (step_problem.ref, step_problem.index) == ("user_signs", 0)
    assert str(step_problem).startswith("Step 'user_signs': ")
    assert json.loads(json.dumps(step_problem.as_dict()))["kind"] == "step"
    assert isinstance(Problem("workflow", None, None, "The workflow needs a name."), str)


def test_email_defaults_and_rendering():
    step = {"type": "approve"}
    subject, body = emails.texts_for(step, "de")
    assert subject == "Bitte genehmigen: {workflow} {request_no}" and "{link}" in body
    step["email"] = {"subject": {"de": "Freigabe für {employee}", "en": ""}}
    assert emails.texts_for(step, "de")[0] == "Freigabe für {employee}"
    assert emails.texts_for(step, "en")[0] == "Please approve: {workflow} {request_no}"  # empty -> default
    assert emails.render("Antrag {request_no} für {employee} {x}", {"request_no": "2026-0042",
                                                                     "employee": "Tom"}) == \
        "Antrag 2026-0042 für Tom {x}"
    for step_type, texts in emails.DEFAULTS.items():
        for part in ("subject", "body"):
            for lang in ("de", "en"):
                assert set(emails.placeholders_in(texts[part][lang])) <= set(emails.PLACEHOLDERS)
        assert ("{link}" in texts["body"]["de"]) == (step_type != "notify")


# ----------------------------------------------------------------- engine

IT_BASE = {"user": "emp@co.de"}
LAPTOP = {"laptop_issued": "on", "laptop_model": "Latitude 5450", "laptop_vendor": "Dell", "laptop_serial": "L-1"}


def test_hidden_section_fields_are_not_required_or_stored(engine):
    values = dict(IT_BASE, laptop_model="Latitude 5450", laptop_serial="ignored", pin="1234")
    req = engine.start("IT Assets Checklist", actor("it1@co.de"), values)  # no device ticked
    assert "laptop_model" not in req["form"] and "pin" not in req["form"] and "monitors" not in req["form"]
    assert req["form"]["laptop_issued"] is False


def test_shown_section_fields_are_required(engine):
    with pytest.raises(ValidationError) as exc:
        engine.start("IT Assets Checklist", actor("it1@co.de"), dict(IT_BASE, laptop_issued="on"))
    details = " ".join(exc.value.details)
    assert "laptop_model: required" in details and "laptop_serial: required" in details
    req = engine.start("IT Assets Checklist", actor("it1@co.de"), dict(IT_BASE, **LAPTOP))
    assert req["form"]["laptop_serial"] == "L-1"


def test_repeat_rows_min_max_and_empty_rows(engine):
    def start(rows):
        return engine.start("IT Assets Checklist", actor("it1@co.de"),
                            dict(IT_BASE, monitor_issued="on", monitors=rows))

    with pytest.raises(ValidationError) as exc:
        start([])
    assert "monitors: at least 1" in exc.value.details
    with pytest.raises(ValidationError) as exc:
        start([{"monitor_model": "P2423", "monitor_vendor": "Dell", "monitor_serial": f"M{n}"} for n in range(7)])
    assert "monitors: at most 6" in exc.value.details
    with pytest.raises(ValidationError) as exc:
        start([{"monitor_model": "P2423", "monitor_vendor": "Dell"}])
    assert "monitors[1].monitor_serial: required" in exc.value.details

    req = start({"1": {"monitor_model": "U2724D", "monitor_vendor": "Dell", "monitor_serial": "M2"},
                 "0": {"monitor_model": "P2423", "monitor_vendor": "Dell", "monitor_serial": "M1"},
                 "2": {"monitor_model": "", "monitor_vendor": "", "monitor_serial": " "}})
    assert req["form"]["monitors"] == [
        {"monitor_model": "P2423", "monitor_vendor": "Dell", "monitor_serial": "M1"},
        {"monitor_model": "U2724D", "monitor_vendor": "Dell", "monitor_serial": "M2"},
    ]


def test_secret_fields_in_repeat_rows_are_encrypted(seeded):
    definition = {
        "name": "Phones", "fields": [
            {"id": "user", "type": "person", "label": "User", "required": True},
            {"id": "phones", "type": "section", "label": "Phone", "repeat": {"min": 1, "max": 3}},
            {"id": "phone_model", "type": "text", "label": "Model", "section": "phones", "required": True},
            {"id": "phone_pin", "type": "secret", "label": "PIN", "section": "phones"},
        ],
        "steps": [{"id": "copy", "type": "notify", "label": "Copy", "who": [{"kind": "field", "field": "user"}]}],
    }
    workflows.save_draft(seeded, definition, by="admin@co.de")
    workflows.publish(seeded, "Phones", by="admin@co.de")
    engine = Engine(seeded, Cipher.generate())
    req = engine.start("Phones", actor("it1@co.de"), {"user": "emp@co.de", "phones": [
        {"phone_model": "A55", "phone_pin": "Pin-0815!"}, {"phone_model": "iPhone", "phone_pin": ""}]})
    stored = seeded.one("SELECT data_json FROM form_versions WHERE request_id = ?", (req["id"],))["data_json"]
    assert "Pin-0815!" not in stored and "$enc" in stored and "A55" in stored
    assert req["form"]["phones"][0]["phone_pin"] == "Pin-0815!"
    assert engine.form_versions(req["id"])[0]["data"]["phones"][1]["phone_pin"] is None
    events = " ".join(r["data_json"] for r in seeded.all("SELECT data_json FROM events"))
    assert "Pin-0815!" not in events


def test_unticking_a_section_on_resubmit_removes_its_values(engine):
    req = engine.start("IT Assets Checklist", actor("it1@co.de"), dict(IT_BASE, **LAPTOP))
    req = engine.decide(open_tasks(req)[0]["id"], "rejected", actor("emp@co.de"), reason="No laptop here")
    req = engine.resubmit(req["id"], actor("it1@co.de"), dict(IT_BASE, laptop_issued=""))
    assert "laptop_serial" not in req["form"] and req["form"]["laptop_issued"] is False
    assert engine.form_versions(req["id"])[0]["data"]["laptop_serial"] == "L-1"  # history keeps it


def test_section_filled_by_a_later_step(seeded):
    d = _it()
    d["name"] = "IT Assets Two Steps"
    d["steps"].insert(0, {"id": "it_details", "type": "fill", "label": "IT details",
                          "who": {"kind": "starter"},
                          "fields": ["monitor_model", "monitor_vendor", "monitor_serial"]})
    workflows.save_draft(seeded, d, by="admin@co.de")
    workflows.publish(seeded, d["name"], by="admin@co.de")
    engine = Engine(seeded, Cipher.generate())
    req = engine.start(d["name"], actor("it1@co.de"), dict(IT_BASE, monitor_issued="on"))
    task = open_tasks(req)[0]
    with pytest.raises(ValidationError):
        engine.decide(task["id"], "submitted", actor("it1@co.de"), values={"monitors": []})
    req = engine.decide(task["id"], "submitted", actor("it1@co.de"), values={"monitors": [
        {"monitor_model": "P2423", "monitor_vendor": "Dell", "monitor_serial": "M1"}]})
    assert req["form"]["monitors"][0]["monitor_serial"] == "M1"
    assert open_tasks(req)[0]["type"] == "sign"


def test_rebuilt_example_full_flow(engine, notifier):
    values = dict(IT_BASE, **LAPTOP, docking_issued="on", docking_model="WD19S", docking_vendor="Dell",
                  docking_serial="D-1", monitor_issued="on",
                  monitors=[{"monitor_model": "P2423", "monitor_vendor": "Dell", "monitor_serial": "M1"},
                            {"monitor_model": "U2724D", "monitor_vendor": "Dell", "monitor_serial": "M2"}],
                  smartphone_issued="on", smartphone_model="Galaxy A55", smartphone_vendor="Samsung",
                  imei="356938035643809", pin="1111", puk="22223333", unlock_pin="4444")
    req = engine.start("IT Assets Checklist", actor("it1@co.de"), values)
    assert len(req["form"]["monitors"]) == 2 and req["form"]["puk"] == "22223333"
    req = engine.decide(open_tasks(req)[0]["id"], "signed", actor("emp@co.de"), signature_image=PNG)
    assert req["status"] == "completed"
    assert notifier.hooks("step_notify")[0]["to"] == ["emp@co.de", "it1@co.de", "it2@co.de"]
