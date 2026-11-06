from datetime import timedelta

import pytest

from wftool import accounts, clock
from wftool.errors import StateError, ValidationError


@pytest.fixture(autouse=True)
def _reset_clock():
    yield
    clock.set_now(None)


def test_default_admin_must_change_password(db):
    assert accounts.ensure_default_admin(db) is True
    assert accounts.ensure_default_admin(db) is False  # only on an empty installation
    acc = accounts.authenticate(db, "admin", "admin")
    assert acc["role"] == "admin" and acc["must_change_password"] == 1

    with pytest.raises(ValidationError):
        accounts.change_password(db, acc["id"], "admin", "short")
    accounts.change_password(db, acc["id"], "admin", "a-much-longer-secret")
    acc = accounts.authenticate(db, "admin", "a-much-longer-secret")
    assert acc["must_change_password"] == 0


def test_lockout_after_five_wrong_attempts_for_fifteen_minutes(db):
    accounts.ensure_default_admin(db)
    start = clock.now()
    clock.set_now(start)
    for _ in range(4):
        with pytest.raises(accounts.LoginError) as exc:
            accounts.authenticate(db, "admin", "wrong")
        assert exc.value.reason == "invalid"
    with pytest.raises(accounts.LoginError) as exc:
        accounts.authenticate(db, "admin", "wrong")
    assert exc.value.reason == "locked"
    with pytest.raises(accounts.LoginError) as exc:
        accounts.authenticate(db, "admin", "admin")  # right password, still locked
    assert exc.value.reason == "locked"

    clock.set_now(start + timedelta(minutes=14))
    with pytest.raises(accounts.LoginError):
        accounts.authenticate(db, "admin", "admin")
    clock.set_now(start + timedelta(minutes=16))
    assert accounts.authenticate(db, "admin", "admin")["username"] == "admin"


def test_accounts_roles_and_last_admin(db):
    accounts.ensure_default_admin(db)
    with pytest.raises(ValidationError):
        accounts.create_account(db, "hanna", "twelve-chars-ok", "starter", by="admin")  # starter needs email
    hanna = accounts.create_account(db, "hanna", "twelve-chars-ok", "starter", email="Hanna.HR@company.de",
                                    by="admin")
    assert hanna["email"] == "hanna.hr@company.de" and hanna["must_change_password"] == 1
    with pytest.raises(ValidationError):
        accounts.create_account(db, "HANNA", "twelve-chars-ok", "auditor", by="admin")  # usernames ignore case

    admin = accounts.authenticate(db, "admin", "admin")
    with pytest.raises(StateError):
        accounts.update_account(db, admin["id"], active=False, by="admin")
    accounts.update_account(db, hanna["id"], role="admin", by="admin")
    accounts.update_account(db, admin["id"], active=False, by="admin")  # now allowed: another admin exists
    with pytest.raises(accounts.LoginError) as exc:
        accounts.authenticate(db, "admin", "admin")
    assert exc.value.reason == "inactive"


def test_reset_password_forces_change(db):
    accounts.ensure_default_admin(db)
    ivo = accounts.create_account(db, "ivo", "twelve-chars-ok", "auditor", by="admin")
    accounts.change_password(db, ivo["id"], "twelve-chars-ok", "ivo-own-password")
    accounts.reset_password(db, ivo["id"], "temporary-pass-1", by="admin")
    acc = accounts.authenticate(db, "ivo", "temporary-pass-1")
    assert acc["must_change_password"] == 1
