"""Builder pages: create, edit, reorder, check, publish, export and import (phase 3)."""

import io
import json
import re
from datetime import timedelta

import pytest

from wftool import audit, clock, workflows
from wftool.crypto import Cipher
from wftool.engine import Engine

from conftest import actor, load_example, seed
from test_web import csrf, first_login


@pytest.fixture
def admin(app, client):
    seed(app.extensions["wftool"]["db"])
    token = first_login(client)
    return token


def _db(app):
    return app.extensions["wftool"]["db"]


def _data(client, wid):
    page = client.get(f"/admin/workflows/{wid}").get_data(as_text=True)
    return json.loads(re.search(r'id="builder-data">(.*?)</script>', page, re.S).group(1)), page


def _create(client, token, name="IT Assets Checklist"):
    resp = client.post("/admin/workflows", data={
        "_csrf": token, "name": name, "label_de": "IT-Ausstattung", "label_en": "IT Assets Checklist"})
    assert resp.status_code == 302
    return int(resp.headers["Location"].rsplit("/", 1)[1])


def _save(client, token, wid, definition, action="save", base=None):
    data = {"_csrf": token, "definition": json.dumps(definition), "action": action, "tab": "form"}
    if base is not None:
        data["base"] = base
    return client.post(f"/admin/workflows/{wid}", data=data)


def test_builder_pages_need_an_admin(app, client, admin):
    wid = _create(client, admin)
    client.post("/admin/accounts", data={"_csrf": admin, "username": "ina", "role": "starter", "email": "it1@co.de",
                                         "language": "de", "password": "temporary-pass-1"})
    client.post("/logout", data={"_csrf": admin})
    client.post("/login", data={"username": "ina", "password": "temporary-pass-1", "_csrf": csrf(client, "/login")})
    token = csrf(client, "/password")
    client.post("/password", data={"_csrf": token, "old_password": "temporary-pass-1",
                                   "new_password": "inas-new-password", "repeat_password": "inas-new-password"})
    assert client.get("/admin/workflows").status_code == 403
    assert client.get(f"/admin/workflows/{wid}").status_code == 403
    assert client.get(f"/admin/workflows/{wid}/export").status_code == 403


def test_create_empty_workflow_and_see_its_problems(app, client, admin):
    wid = _create(client, admin)
    data, page = _data(client, wid)
    assert data["definition"] == {"name": "IT Assets Checklist", "label": {"de": "IT-Ausstattung",
                                  "en": "IT Assets Checklist"}, "fields": [], "steps": []}
    assert [p["message"] for p in data["problems"]] == ["Der Workflow braucht mindestens einen Schritt."]
    assert {"laptop_models", "hardware_vendors", "software"} <= {lst["name"] for lst in data["lists"]}
    assert data["groups"] == ["HR", "IT"] and any(u["email"] == "emp@co.de" for u in data["users"])
    assert "builder.js" in page and "<script>" not in page  # CSP: no inline script
    assert 'id="builder-data"' in page and "1 Problem(e)" in page
    assert client.get("/admin/workflows").get_data(as_text=True).count("Nicht veröffentlicht") == 1
    resp = client.post("/admin/workflows", data={"_csrf": admin, "name": "it assets checklist"})
    assert "existiert bereits" in resp.get_data(as_text=True)


def test_build_the_example_save_reorder_and_publish(app, client, admin):
    wid = _create(client, admin)
    example = load_example("it_assets_checklist.json")
    base = workflows.get_workflow_by_id(_db(app), wid)["updated_at"]
    assert _save(client, admin, wid, example, base=base).status_code == 302
    data, page = _data(client, wid)
    assert data["problems"] == [] and "Bereit zum Veröffentlichen" in page

    # Reorder: move notes to the top and the docking section (with its checkbox) before the laptop
    d = json.loads(json.dumps(example))
    notes = next(f for f in d["fields"] if f["id"] == "notes")
    d["fields"].remove(notes)
    d["fields"].insert(0, notes)
    assert _save(client, admin, wid, d).status_code == 302
    saved = workflows.get_workflow_by_id(_db(app), wid)["draft"]
    assert [f["id"] for f in saved["fields"]][:2] == ["notes", "user"]

    resp = _save(client, admin, wid, saved, action="publish")
    assert resp.status_code == 302
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Als Version 1 veröffentlicht" in page and "Veröffentlicht v1" in page
    again = client.post(f"/admin/workflows/{wid}", data={"_csrf": admin, "definition": json.dumps(saved),
                                                         "action": "publish"}, follow_redirects=True)
    assert "keine neue Version" in again.get_data(as_text=True)

    # The published version runs: the start form with sections and a repeatable monitor list
    engine = Engine(_db(app), Cipher.generate())
    req = engine.start("IT Assets Checklist", actor("it1@co.de"), {
        "user": "emp@co.de", "monitor_issued": "on",
        "monitors": [{"monitor_model": "P2423", "monitor_vendor": "Dell", "monitor_serial": "M1"}]})
    assert req["form"]["monitors"][0]["monitor_serial"] == "M1"
    events = [r["type"] for r in _db(app).all("SELECT type FROM events WHERE type LIKE 'workflow.%'")]
    assert events.count("workflow.draft_saved") >= 3 and "workflow.published" in events
    assert audit.verify(_db(app))["ok"]


def test_publish_is_refused_and_problems_are_located(app, client, admin):
    wid = _create(client, admin)
    d = load_example("it_assets_checklist.json")
    next(f for f in d["fields"] if f["id"] == "laptop_model")["list"] = "laptops_typo"
    d["steps"][1]["who"].append({"kind": "group", "group": "Purchasing"})
    resp = _save(client, admin, wid, d, action="publish")
    assert resp.headers["Location"].endswith("#check")
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "nicht veröffentlicht: 2 Problem(e)" in page
    assert workflows.versions(_db(app), wid) == []
    data, _ = _data(client, wid)
    by_kind = {(p["kind"], p["ref"]): p["message"] for p in data["problems"]}
    assert by_kind[("field", "laptop_model")] == "Die Liste 'laptops_typo' gibt es nicht."
    assert by_kind[("step", "copy_to_user_and_it")] == "Die Gruppe 'Purchasing' gibt es nicht."
    assert all(p["index"] is not None for p in data["problems"])


def test_live_check_does_not_save(app, client, admin):
    wid = _create(client, admin)
    d = load_example("it_assets_checklist.json")
    d["steps"][0]["who"] = {"kind": "group", "group": "IT"}
    d["steps"][0]["email"] = {"body": {"de": "Hallo {employe}"}}
    resp = client.post(f"/admin/workflows/{wid}/check", data={"_csrf": admin, "definition": json.dumps(d)})
    body = resp.get_json()
    assert body["ok"] is False
    messages = [p["message"] for p in body["problems"] if p["kind"] == "step" and p["index"] == 0]
    assert "Genehmigungen und Unterschriften gehen an eine Person, nie an eine Gruppe." in messages
    assert "Unbekannter Platzhalter {employe} im E-Mail-Text." in messages
    assert workflows.get_workflow_by_id(_db(app), wid)["draft"]["fields"] == []  # nothing saved
    bad = client.post(f"/admin/workflows/{wid}/check", data={"_csrf": admin, "definition": "{oops"})
    assert bad.status_code == 400
    assert client.post(f"/admin/workflows/{wid}/check", data={"definition": "{}"}).status_code == 302  # no CSRF


def test_rename_and_concurrent_save(app, client, admin):
    wid = _create(client, admin)
    w = workflows.get_workflow_by_id(_db(app), wid)
    d = dict(w["draft"], name="IT Assets")
    clock.set_now(clock.now() + timedelta(seconds=5))  # someone saves a little later
    assert _save(client, admin, wid, d, base=w["updated_at"]).status_code == 302
    assert workflows.get_workflow_by_id(_db(app), wid)["name"] == "IT Assets"
    stale = _save(client, admin, wid, dict(d, label={"de": "Alt"}), base=w["updated_at"])
    page = client.get(stale.headers["Location"]).get_data(as_text=True)
    assert "Jemand anderes hat diesen Workflow gespeichert" in page
    assert workflows.get_workflow_by_id(_db(app), wid)["draft"]["label"]["de"] == "IT-Ausstattung"
    events = [json.loads(r["data_json"]) for r in _db(app).all("SELECT data_json FROM events WHERE type = 'workflow.renamed'")]
    assert events[0]["from"] == "IT Assets Checklist" and events[0]["to"] == "IT Assets"


def test_export_import_and_discard(app, client, admin):
    wid = _create(client, admin)
    _save(client, admin, wid, load_example("it_assets_checklist.json"), action="publish")
    resp = client.get(f"/admin/workflows/{wid}/export")
    assert resp.headers["Content-Disposition"] == 'attachment; filename="it_assets_checklist.json"'
    exported = json.loads(resp.get_data(as_text=True))
    assert exported == load_example("it_assets_checklist.json")

    # Import into the same system as a changed draft: replaces the draft, publishes nothing
    exported["fields"].append({"id": "asset_tag", "type": "text", "label": "Inventarnummer"})
    upload = {"_csrf": admin, "file": (io.BytesIO(json.dumps(exported).encode("utf-8")), "it.json")}
    page = client.post("/admin/workflows/import", data=upload, content_type="multipart/form-data",
                       follow_redirects=True).get_data(as_text=True)
    assert "der bisherige Entwurf wurde ersetzt" in page
    w = workflows.get_workflow_by_id(_db(app), wid)
    assert w["draft"]["fields"][-1]["id"] == "asset_tag" and len(workflows.versions(_db(app), wid)) == 1
    assert "Unveröffentlichte Änderungen" in client.get("/admin/workflows").get_data(as_text=True)

    # An unfinished draft from another system is accepted and shows its problems
    draft = {"name": "Purchase Request", "fields": [{"id": "item", "type": "dropdown", "label": "Artikel"}],
             "steps": []}
    upload = {"_csrf": admin, "file": (io.BytesIO(json.dumps(draft).encode()), "pr.json")}
    page = client.post("/admin/workflows/import", data=upload, content_type="multipart/form-data",
                       follow_redirects=True).get_data(as_text=True)
    assert "Als Entwurf importiert" in page and "Problem(e)" in page
    for bad in (b"not json", b"[1, 2]", "ÿ".encode("latin-1")):
        upload = {"_csrf": admin, "file": (io.BytesIO(bad), "x.json")}
        page = client.post("/admin/workflows/import", data=upload, content_type="multipart/form-data",
                           follow_redirects=True).get_data(as_text=True)
        assert "keine gültige Workflow-Datei" in page or "genau ein JSON-Objekt" in page

    # Discard goes back to the published version
    page = client.post(f"/admin/workflows/{wid}", data={"_csrf": admin, "action": "discard"},
                       follow_redirects=True).get_data(as_text=True)
    assert "Unveröffentlichte Änderungen verworfen" in page
    assert workflows.get_workflow_by_id(_db(app), wid)["draft"] == load_example("it_assets_checklist.json")
    types = [r["type"] for r in _db(app).all("SELECT type FROM events WHERE type LIKE 'workflow.%'")]
    assert "workflow.imported" in types and "workflow.draft_discarded" in types


def test_who_may_start_links_to_and_from_the_builder(app, client, admin):
    wid = _create(client, admin)
    _save(client, admin, wid, load_example("it_assets_checklist.json"), action="publish")
    builder = client.get(f"/admin/workflows/{wid}").get_data(as_text=True)
    assert f'href="/admin/start#wf-{wid}"' in builder
    start = client.get("/admin/start").get_data(as_text=True)
    assert f'id="wf-{wid}"' in start and f'href="/admin/workflows/{wid}"' in start


def test_secret_values_never_reach_the_builder(app, client, admin):
    """The builder only handles definitions; a started request's PIN is not on any builder page."""
    wid = _create(client, admin)
    _save(client, admin, wid, load_example("it_assets_checklist.json"), action="publish")
    engine = Engine(_db(app), app.extensions["wftool"]["cipher"])
    engine.start("IT Assets Checklist", actor("it1@co.de"), {
        "user": "emp@co.de", "smartphone_issued": "on", "smartphone_model": "iPhone 15", "smartphone_vendor": "Apple",
        "imei": "356938035643809", "pin": "Pin-4711!"})
    for path in (f"/admin/workflows/{wid}", f"/admin/workflows/{wid}/export", "/admin/workflows"):
        assert "Pin-4711!" not in client.get(path).get_data(as_text=True)
