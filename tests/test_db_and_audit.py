from wftool import audit
from wftool.db import Database

from conftest import PNG, actor


def test_migrations_create_schema_once(tmp_path):
    path = tmp_path / "x.db"
    db = Database(path)
    assert db.schema_version() == 2
    assert db.one("PRAGMA journal_mode")[0] == "wal"
    db.close()
    db = Database(path)  # second start: nothing re-applied, no error
    assert db.schema_version() == 2
    tables = {r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for t in ("accounts", "ad_users", "groups", "group_members", "lists", "list_items", "workflows",
              "workflow_versions", "requests", "form_versions", "tasks", "links", "events", "outbox",
              "settings", "imports"):
        assert t in tables
    db.close()


def test_failed_transaction_rolls_back(db):
    try:
        with db.tx() as c:
            c.execute("INSERT INTO settings (key, value) VALUES ('a', '1')")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert db.one("SELECT * FROM settings WHERE key = 'a'") is None


def _run_flow(engine):
    req = engine.start("IT Assets Checklist", actor("it1@co.de"), {
        "user": "emp@co.de", "smartphone_issued": True, "smartphone_model": "iPhone 15",
        "smartphone_vendor": "Apple", "imei": "356938035643809", "pin": "Zx9!pin"})
    task = req["tasks"][0]
    engine.decide(task["id"], "signed", actor("emp@co.de"), signature_image=PNG)
    return req


def test_chain_verifies_after_a_full_flow(engine):
    _run_flow(engine)
    result = audit.verify(engine.db)
    assert result["ok"], result
    assert result["checked"] > 5


def test_changed_entry_is_detected(engine):
    _run_flow(engine)
    victim = engine.db.one("SELECT id FROM events WHERE type = 'task.decided'")["id"]
    engine.db.execute("UPDATE events SET data_json = replace(data_json, 'signed', 'rejected') WHERE id = ?",
                      (victim,))
    result = audit.verify(engine.db)
    assert not result["ok"]
    assert result["first_bad_id"] == victim
    assert result["problem"] == "entry content changed"


def test_removed_entry_is_detected(engine):
    _run_flow(engine)
    victim = engine.db.one("SELECT id FROM events WHERE type = 'step.started'")["id"]
    engine.db.execute("DELETE FROM events WHERE id = ?", (victim,))
    result = audit.verify(engine.db)
    assert not result["ok"]
    assert result["first_bad_id"] == victim + 1


def test_latest_hash_is_the_anchor_value(engine):
    _run_flow(engine)
    last_id, last_hash = audit.latest_hash(engine.db)
    row = engine.db.one("SELECT id, hash FROM events ORDER BY id DESC LIMIT 1")
    assert (last_id, last_hash) == (row["id"], row["hash"])


def test_secret_values_never_reach_the_log_or_the_database_file(engine):
    req = _run_flow(engine)
    all_events = " ".join(r["data_json"] for r in engine.db.all("SELECT data_json FROM events"))
    stored = " ".join(r["data_json"] for r in engine.db.all("SELECT data_json FROM form_versions"))
    assert "Zx9!pin" not in all_events
    assert "Zx9!pin" not in stored
    assert engine.get_request(req["id"])["form"]["pin"] == "Zx9!pin"
