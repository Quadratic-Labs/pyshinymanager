"""Phase 7 — validation differentielle de la journalisation (backend SQLite) contre R."""

import pytest

from shinymanager import logs, settings
from shinymanager.db import create_db, read_db
from shinymanager.tokens import _tok


@pytest.fixture(autouse=True)
def _reset():
    settings.reset_options()
    _tok.__init__()
    yield
    settings.reset_options()
    _tok.__init__()


def _nwp(path):
    return next(r["n_wrong_pwd"] for r in read_db(path, "pwd_mngt") if r["user"] == "alice")


@pytest.mark.oracle
def test_logs_sqlite_matches_r(require_r, tmp_path):
    r = require_r.run_r_script("oracle_logs.R", tmp_path)

    settings.set_option("application", "myapp")
    path = str(tmp_path / "py.sqlite")
    create_db([{"user": "alice", "password": "azerty"}], path)
    _tok.set_sqlite_path(path)
    _tok.add("tok-alice", {"user": "alice"})

    logs.save_logs_failed("alice", "Wrong pwd")
    logs.save_logs_failed("alice", "Wrong pwd")
    assert _nwp(path) == r["nwp_after_two_fail"]

    logs.save_logs_failed("alice", "Unknown user")
    assert _nwp(path) == r["nwp_after_unknown"]

    logs.save_logs("tok-alice")
    assert _nwp(path) == r["nwp_after_success"]
    rows = read_db(path, "logs")
    assert sum(1 for x in rows if x["status"] == "Success") == r["n_success"]
    assert sum(1 for x in rows if x["status"] == "Wrong pwd") == r["n_wrong"]

    logs.save_logs("tok-alice")
    rows2 = read_db(path, "logs")
    assert sum(1 for x in rows2 if x["status"] == "Success") == r["n_success_after_replay"]

    logs.logout_logs("tok-alice")
    rows3 = read_db(path, "logs")
    logout_set = any(x["logout"] is not None for x in rows3 if x["token"] == "tok-alice")
    assert logout_set == r["logout_set"]
