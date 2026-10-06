"""Phase 7 — validation differentielle du cycle de vie (backend SQLite) contre le source R.

Rejoue la meme sequence que oracle_pwd_lifecycle.R sur le backend SQLite Python et compare les
valeurs observables. Les deviations D2 (fail-closed) sont testees a part (test_pwd_lifecycle.py).
"""

import pytest

from shinymanager import pwd_lifecycle, settings
from shinymanager.db import create_db, read_db, write_db
from shinymanager.passwords import verify_pwd
from shinymanager.tokens import _tok


@pytest.fixture(autouse=True)
def _reset(tmp_path):
    settings.reset_options()
    _tok.__init__()  # store vierge
    yield
    settings.reset_options()
    _tok.__init__()


@pytest.mark.oracle
def test_pwd_lifecycle_sqlite_matches_r(require_r, tmp_path):
    r = require_r.run_r_script("oracle_pwd_lifecycle.R", tmp_path)

    path = str(tmp_path / "py.sqlite")
    create_db([{"user": "alice", "password": "azerty"}], path)
    _tok.set_sqlite_path(path)
    _tok.add("tok-alice", {"user": "alice"})

    assert pwd_lifecycle.is_force_chg_pwd("tok-alice") == r["force_initial"]

    pwd_lifecycle.force_chg_pwd("alice", True)
    assert pwd_lifecycle.is_force_chg_pwd("tok-alice") == r["force_after_true"]

    assert pwd_lifecycle.update_pwd("alice", "Newpass1")["result"] == r["update_result"]
    assert pwd_lifecycle.is_force_chg_pwd("tok-alice") == r["force_after_update"]
    cred = {row["user"]: row for row in read_db(path, "credentials")}
    assert verify_pwd(cred["alice"]["password"], "Newpass1") == r["verify_new"]

    assert pwd_lifecycle.check_new_pwd("alice", "Newpass1") == r["check_same"]
    assert pwd_lifecycle.check_new_pwd("alice", "Other123") == r["check_diff"]

    assert pwd_lifecycle.check_locked_account("alice", 2) == r["locked_zero"]
    # Force n_wrong_pwd a 2 comme l'oracle, puis re-teste.
    pwd = read_db(path, "pwd_mngt")
    for row in pwd:
        if row["user"] == "alice":
            row["n_wrong_pwd"] = 2
    write_db(path, pwd, "pwd_mngt")
    assert pwd_lifecycle.check_locked_account("alice", 2) == r["locked_two"]
