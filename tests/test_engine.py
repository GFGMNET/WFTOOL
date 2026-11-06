import copy

import pytest

from wftool import audit, masterdata, workflows
from wftool.errors import StateError, ValidationError

from conftest import PNG, actor, load_example, open_tasks

HR_START = {"employee": "Tom Neu", "start_date": "2026-12-01", "department": "Sales", "manager": "mgr@co.de"}


def event_types(engine, rid):
    return [e["type"] for e in audit.events_for(engine.db, rid)]


# ---------------------------------------------------------------- HR Onboard

def test_hr_onboard_full_path(engine, notifier):
    req = engine.start("HR Onboard", actor("hr1@co.de"), HR_START)
    assert req["number"].endswith("-0001")
    [mgr_task] = open_tasks(req)
    assert (mgr_task["assignee_email"], mgr_task["type"]) == ("mgr@co.de", "fill_approve")

    req = engine.decide(mgr_task["id"], "approved", actor("mgr@co.de"),
                        values={"software": ["ERP", "AutoCAD", "Visio"]})
    assert req["form"]["software"] == ["ERP", "AutoCAD", "Visio"]
    items = {t["item_value"]: t for t in open_tasks(req, "software_approval")}
    assert set(items) == {"AutoCAD", "Visio"}  # ERP needs no extra approval
    assert items["AutoCAD"]["assignee_email"] == "cad@co.de"

    req = engine.decide(items["AutoCAD"]["id"], "approved", actor("cad@co.de"))
    assert req["status"] == "open" and req["current_step"] == 1  # Visio still open
    req = engine.decide(items["Visio"]["id"], "approved", actor("vis@co.de"))

    hr_copy = notifier.hooks("step_notify")[0]
    assert hr_copy["to"] == ["hr1@co.de", "hr2@co.de"] and hr_copy["attach_pdf"] is True
    confirm = open_tasks(req, "forward_to_it")
    assert {t["assignee_email"] for t in confirm} == {"hr1@co.de", "hr2@co.de"}

    by_hr2 = next(t for t in confirm if t["assignee_email"] == "hr2@co.de")
    req = engine.decide(by_hr2["id"], "confirmed", actor("hr2@co.de"))
    other = next(t for t in req["tasks"] if t["step_id"] == "forward_to_it" and t["assignee_email"] == "hr1@co.de")
    assert other["status"] == "cancelled" and other["cancel_reason"] == "done by hr2@co.de"

    assert req["status"] == "completed" and req["closed_at"]
    assert notifier.hooks("step_notify")[1]["to"] == ["it1@co.de", "it2@co.de"]
    assert notifier.hooks("request_completed") == [{"request": req["number"]}]

    decided = next(e for e in audit.events_for(engine.db, req["id"])
                   if e["type"] == "task.decided" and e["data"]["item"] == "AutoCAD")
    assert decided["actor"] == "cad@co.de" and decided["ip"] == "10.0.0.5"
    assert decided["user_agent"] == "pytest-browser" and decided["data"]["form_fingerprint"]


def test_software_step_skipped_when_nothing_needs_approval(engine, notifier):
    req = engine.start("HR Onboard", actor("hr1@co.de"), HR_START)
    req = engine.decide(open_tasks(req)[0]["id"], "approved", actor("mgr@co.de"), values={"software": ["ERP"]})
    assert req["definition"]["steps"][req["current_step"]]["id"] == "forward_to_it"
    assert "step.skipped" in event_types(engine, req["id"])
    assert len(notifier.hooks("step_notify")) == 1


def test_item_rejection_cancels_other_items_and_returns_request(engine, notifier):
    req = engine.start("HR Onboard", actor("hr1@co.de"), HR_START)
    req = engine.decide(open_tasks(req)[0]["id"], "approved", actor("mgr@co.de"),
                        values={"software": ["AutoCAD", "Visio"]})
    cad = next(t for t in open_tasks(req) if t["item_value"] == "AutoCAD")
    req = engine.decide(cad["id"], "rejected", actor("cad@co.de"), reason="No licence left")

    assert req["status"] == "returned"
    visio = next(t for t in req["tasks"] if t["item_value"] == "Visio")
    assert visio["status"] == "cancelled"
    assert notifier.hooks("request_returned") == [{"request": req["number"], "reason": "No licence left"}]
    assert any(h["task"] == visio["id"] for h in notifier.hooks("task_closed"))
    with pytest.raises(StateError):
        engine.decide(visio["id"], "approved", actor("vis@co.de"))


def test_resubmit_keeps_old_form_version_and_runs_all_steps_again(engine):
    req = engine.start("HR Onboard", actor("hr1@co.de"), HR_START)
    req = engine.decide(open_tasks(req)[0]["id"], "rejected", actor("mgr@co.de"), reason="Wrong start date")
    assert req["status"] == "returned"
    with pytest.raises(StateError):
        engine.resubmit(req["id"], actor("hr2@co.de"), HR_START)  # only the starter

    req = engine.resubmit(req["id"], actor("hr1@co.de"), dict(HR_START, start_date="2027-01-04"))
    assert req["status"] == "open" and req["round"] == 2
    [task] = open_tasks(req)
    assert task["assignee_email"] == "mgr@co.de" and task["round"] == 2
    versions = engine.form_versions(req["id"])
    assert [v["data"]["start_date"] for v in versions] == ["2026-12-01", "2027-01-04"]
    assert versions[0]["fingerprint"] != versions[1]["fingerprint"]


def test_rejection_needs_reason_and_only_assignee_decides(engine):
    req = engine.start("HR Onboard", actor("hr1@co.de"), HR_START)
    task = open_tasks(req)[0]
    with pytest.raises(ValidationError):
        engine.decide(task["id"], "rejected", actor("mgr@co.de"), reason="  ")
    with pytest.raises(StateError):
        engine.decide(task["id"], "approved", actor("cad@co.de"), values={"software": ["ERP"]})
    with pytest.raises(StateError):
        engine.decide(task["id"], "signed", actor("mgr@co.de"))
    engine.decide(task["id"], "approved", actor("mgr@co.de"), values={"software": ["ERP"]})
    with pytest.raises(StateError):
        engine.decide(task["id"], "approved", actor("mgr@co.de"), values={"software": ["ERP"]})


def test_starter_never_approves_own_request(engine):
    req = engine.start("HR Onboard", actor("mgr@co.de"), dict(HR_START, manager="mgr@co.de"))
    [task] = open_tasks(req)
    assert task["assignee_email"] == "admin@co.de"
    assert "recipient.self_approval_redirected" in event_types(engine, req["id"])


def test_inactive_item_approver_falls_back_to_admin(engine):
    masterdata.upsert_user(engine.db, "cad@co.de", "Carl CAD", active=False)
    req = engine.start("HR Onboard", actor("hr1@co.de"), HR_START)
    req = engine.decide(open_tasks(req)[0]["id"], "approved", actor("mgr@co.de"), values={"software": ["AutoCAD"]})
    [task] = open_tasks(req)
    assert task["assignee_email"] == "admin@co.de"
    assert "recipient.fallback_admin" in event_types(engine, req["id"])


def test_on_reject_previous_sends_back_one_step(engine):
    d = copy.deepcopy(load_example("hr_onboard.json"))
    d["steps"][1]["on_reject"] = "previous"
    workflows.save_draft(engine.db, d, by="admin@co.de")
    workflows.publish(engine.db, "HR Onboard", by="admin@co.de")

    req = engine.start("HR Onboard", actor("hr1@co.de"), HR_START)
    req = engine.decide(open_tasks(req)[0]["id"], "approved", actor("mgr@co.de"), values={"software": ["AutoCAD"]})
    req = engine.decide(open_tasks(req)[0]["id"], "rejected", actor("cad@co.de"), reason="Take Visio instead")
    assert req["status"] == "open"
    [task] = open_tasks(req)
    assert (task["step_id"], task["assignee_email"]) == ("manager_approval", "mgr@co.de")
    req = engine.decide(task["id"], "approved", actor("mgr@co.de"), values={"software": ["Visio"]})
    assert open_tasks(req)[0]["assignee_email"] == "vis@co.de"


def test_form_values_are_checked(engine):
    with pytest.raises(ValidationError) as exc:
        engine.start("HR Onboard", actor("hr1@co.de"),
                     {"employee": "", "start_date": "01.12.2026", "manager": "nobody@co.de"})
    details = " ".join(exc.value.details)
    assert "employee: required" in details
    assert "start_date: not a date" in details
    assert "manager: not an active AD user" in details

    req = engine.start("HR Onboard", actor("hr1@co.de"), HR_START)
    with pytest.raises(ValidationError) as exc:
        engine.decide(open_tasks(req)[0]["id"], "approved", actor("mgr@co.de"), values={"software": ["Photoshop"]})
    assert "not in the list: Photoshop" in exc.value.details[0]


def test_reassign_and_cancel(engine, notifier):
    req = engine.start("HR Onboard", actor("hr1@co.de"), HR_START)
    old = open_tasks(req)[0]
    with pytest.raises(StateError):
        engine.reassign(old["id"], "hr1@co.de", actor("admin@co.de"), "starter is not allowed")
    req = engine.reassign(old["id"], "emp@co.de", actor("admin@co.de"), "Max on holiday")
    [new] = open_tasks(req)
    assert new["assignee_email"] == "emp@co.de" and new["step_id"] == old["step_id"]
    assert engine.get_task(old["id"])["status"] == "cancelled"

    req = engine.cancel(req["id"], actor("hr1@co.de"), "Candidate withdrew")
    assert req["status"] == "cancelled"
    assert not open_tasks(req)
    assert notifier.hooks("request_cancelled")[0]["reason"] == "Candidate withdrew"


# ------------------------------------------------------- IT Assets Checklist

IT_START = {"user": "emp@co.de", "pc_model": "Latitude 5450", "pc_vendor": "Dell", "pc_serial": "ABC123",
            "imei": "356938035643809", "pin": "Zx9!pin", "puk": "88776655", "unlock_pin": "0000"}


def test_it_assets_sign_on_screen(engine, notifier):
    req = engine.start("IT Assets Checklist", actor("it1@co.de"), IT_START)
    [task] = open_tasks(req)
    assert (task["type"], task["assignee_email"]) == ("sign", "emp@co.de")
    seen_fp = req["form_fingerprint"]

    with pytest.raises(ValidationError):
        engine.decide(task["id"], "signed", actor("emp@co.de"))  # no signature image
    req = engine.decide(task["id"], "signed", actor("emp@co.de"), signature_image=PNG)

    signed = engine.get_task(task["id"])
    assert signed["form_fingerprint"] == seen_fp  # the signature covers the document the user saw
    assert req["form"]["signature"]["fingerprint"] == signed["signature_fingerprint"]
    assert req["form"]["signature"]["paper"] is False
    assert req["status"] == "completed"
    assert notifier.hooks("step_notify")[0]["to"] == ["emp@co.de", "it1@co.de", "it2@co.de"]


def test_it_assets_refused_signature_goes_back_to_it(engine):
    req = engine.start("IT Assets Checklist", actor("it1@co.de"), IT_START)
    req = engine.decide(open_tasks(req)[0]["id"], "rejected", actor("emp@co.de"), reason="Wrong serial number")
    assert req["status"] == "returned" and req["starter_email"] == "it1@co.de"


def test_paper_signature_chosen_by_signer(engine, notifier):
    req = engine.start("IT Assets Checklist", actor("it1@co.de"), IT_START)
    task = open_tasks(req)[0]
    req = engine.decide(task["id"], "paper", actor("emp@co.de"))
    assert engine.get_task(task["id"])["status"] == "paper_pending"
    assert notifier.hooks("paper_requested")[0]["task"] == task["id"]
    assert notifier.hooks("task_closed")[0]["reason"] == "signature on paper"  # the link stops working

    with pytest.raises(StateError):
        engine.complete_paper(task["id"], actor("emp@co.de"), b"%PDF scan")  # only the starter uploads
    req = engine.complete_paper(task["id"], actor("it1@co.de", ip="10.0.0.9"), b"%PDF scan")
    done = engine.get_task(task["id"])
    assert done["decision"] == "signed_paper" and done["decided_by"] == "it1@co.de" and done["ip"] == "10.0.0.9"
    assert req["form"]["signature"]["paper"] is True
    assert req["status"] == "completed"
    assert "task.paper_uploaded" in event_types(engine, req["id"])


def test_paper_marked_by_starter_and_can_be_disabled(engine):
    req = engine.start("IT Assets Checklist", actor("it1@co.de"), IT_START)
    task = open_tasks(req)[0]
    with pytest.raises(StateError):
        engine.mark_paper(task["id"], actor("it2@co.de"))
    engine.mark_paper(task["id"], actor("it1@co.de"))
    assert engine.get_task(task["id"])["status"] == "paper_pending"

    d = load_example("it_assets_checklist.json")
    d["steps"][0]["allow_paper"] = False
    workflows.save_draft(engine.db, d, by="admin@co.de")
    workflows.publish(engine.db, "IT Assets Checklist", by="admin@co.de")
    req = engine.start("IT Assets Checklist", actor("it1@co.de"), IT_START)
    with pytest.raises(StateError):
        engine.decide(open_tasks(req)[0]["id"], "paper", actor("emp@co.de"))


# ------------------------------------------------------------- conditions

@pytest.mark.parametrize("cond,form,expected", [
    ({"field": "x", "op": "equals", "value": "Yes"}, {"x": "yes"}, True),
    ({"field": "x", "op": "not_equals", "value": "a"}, {"x": "b"}, True),
    ({"field": "x", "op": "equals", "value": "true"}, {"x": True}, True),
    ({"field": "x", "op": "contains", "value": "ERP"}, {"x": ["ERP", "Visio"]}, True),
    ({"field": "x", "op": "not_contains", "value": "ERP"}, {"x": ["Visio"]}, True),
    ({"field": "x", "op": "is_set"}, {"x": []}, False),
    ({"field": "x", "op": "is_empty"}, {}, True),
])
def test_conditions(engine, cond, form, expected):
    assert engine._condition(cond, form, {"fields": []}) is expected


def test_needs_approval_condition_uses_the_list(engine):
    d = load_example("hr_onboard.json")
    cond = {"field": "software", "op": "any_needs_approval"}
    assert engine._condition(cond, {"software": ["ERP"]}, d) is False
    assert engine._condition(cond, {"software": ["ERP", "AutoCAD"]}, d) is True


def test_simultaneous_group_confirm_counts_once(engine):
    import threading

    req = engine.start("HR Onboard", actor("hr1@co.de"), HR_START)
    req = engine.decide(open_tasks(req)[0]["id"], "approved", actor("mgr@co.de"), values={"software": ["ERP"]})
    tasks = open_tasks(req, "forward_to_it")
    results, barrier = [], threading.Barrier(len(tasks))

    def press(task):
        barrier.wait()
        try:
            engine.decide(task["id"], "confirmed", actor(task["assignee_email"]))
            results.append("ok")
        except StateError:
            results.append("refused")

    threads = [threading.Thread(target=press, args=(t,)) for t in tasks]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["ok", "refused"]
    req = engine.get_request(req["id"])
    assert req["status"] == "completed"
    assert sum(1 for t in req["tasks"] if t["decision"] == "confirmed") == 1
    assert audit.verify(engine.db)["ok"]
