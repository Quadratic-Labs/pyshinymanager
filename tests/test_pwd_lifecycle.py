"""Phase 8 — tests unitaires du cycle de vie du mot de passe (sans dependance R).

Couvre : le backend SQL (coherence avec la logique validee sur SQLite), les deviations
fail-closed D2 (chemins d'erreur), le cas sans backend, et l'expiration par pwd_validity.
"""

from datetime import date, timedelta

import pytest

from shinymanager import pwd_lifecycle, settings
from shinymanager.db import create_db, read_db, write_db
from shinymanager.db_sql import create_sql_db, read_table_sql
from shinymanager.passwords import verify_pwd
from shinymanager.tokens import _tok


@pytest.fixture(autouse=True)
def _reset():
    settings.reset_options()
    _tok.__init__()
    yield
    settings.reset_options()
    _tok.__init__()


@pytest.fixture
def sql_conf(tmp_path):
    conf = {"connection": {"url": f"sqlite:///{tmp_path / 'sql.sqlite'}"}}
    create_sql_db([{"user": "alice", "password": "azerty"}], conf)
    _tok.set_sql_config_db(conf)
    return conf


# --- Backend SQL : memes comportements que le backend SQLite (deja valide contre R) ---


def test_sql_force_and_update_cycle(sql_conf):
    _tok.add("t", {"user": "alice"})
    assert pwd_lifecycle.is_force_chg_pwd("t") is False
    pwd_lifecycle.force_chg_pwd("alice", True)
    assert pwd_lifecycle.is_force_chg_pwd("t") is True

    assert pwd_lifecycle.update_pwd("alice", "Newpass1")["result"] is True
    assert pwd_lifecycle.is_force_chg_pwd("t") is False
    cred = {r["user"]: r for r in read_table_sql(sql_conf, "credentials")}
    assert verify_pwd(cred["alice"]["password"], "Newpass1") is True


def test_sql_check_new_pwd_and_lock(sql_conf):
    pwd_lifecycle.update_pwd("alice", "Newpass1")
    assert pwd_lifecycle.check_new_pwd("alice", "Newpass1") is False
    assert pwd_lifecycle.check_new_pwd("alice", "Other123") is True

    assert pwd_lifecycle.check_locked_account("alice", 2) is False
    from shinymanager.db_sql import update_sql_db

    update_sql_db(sql_conf, "pwd_mngt", {"n_wrong_pwd": 2}, "user", "alice")
    assert pwd_lifecycle.check_locked_account("alice", 2) is True


# --- Deviations fail-closed (D2) sur chemin d'erreur ---


def test_check_new_pwd_fail_closed_on_error(tmp_path):
    # Backend pointe sur une base sans table credentials -> erreur de lecture.
    _tok.set_sqlite_path(str(tmp_path / "empty.sqlite"))
    assert pwd_lifecycle.check_new_pwd("alice", "whatever") is False


def test_check_locked_account_fail_closed_on_error(tmp_path):
    _tok.set_sqlite_path(str(tmp_path / "empty.sqlite"))
    assert pwd_lifecycle.check_locked_account("alice", 2) is True


# --- Sans backend : comportement permissif du source ---


def test_no_backend_defaults():
    assert pwd_lifecycle.is_force_chg_pwd("t") is False
    assert pwd_lifecycle.update_pwd("alice", "x") == {"result": False}
    assert pwd_lifecycle.check_new_pwd("alice", "x") is True
    assert pwd_lifecycle.check_locked_account("alice", 2) is False


# --- Expiration par pwd_validity ---


def test_pwd_validity_forces_change(tmp_path):
    path = str(tmp_path / "v.sqlite")
    create_db([{"user": "alice", "password": "azerty"}], path)
    _tok.set_sqlite_path(path)
    _tok.add("t", {"user": "alice"})

    settings.set_option("pwd_validity", 30)
    pwd = read_db(path, "pwd_mngt")
    for row in pwd:
        row["date_change"] = (date.today() - timedelta(days=31)).isoformat()
    write_db(path, pwd, "pwd_mngt")
    assert pwd_lifecycle.is_force_chg_pwd("t") is True

    # Exactement 30 jours : comparaison stricte (> validity) -> pas de forcage.
    pwd = read_db(path, "pwd_mngt")
    for row in pwd:
        row["date_change"] = (date.today() - timedelta(days=30)).isoformat()
    write_db(path, pwd, "pwd_mngt")
    assert pwd_lifecycle.is_force_chg_pwd("t") is False
