"""Compatibilite des bases SQLite avec le package R (chiffrees et en clair), contre R reel.

Une base creee et modifiee par R doit s'ouvrir en Python (contenu identique, mots de passe R
valides), et une base ecrite par Python doit s'ouvrir, s'utiliser et se modifier sous R.
"""

import pytest

from shinymanager import logs, pwd_lifecycle, settings
from shinymanager.check_credentials import check_credentials
from shinymanager.db import create_db, read_db
from shinymanager.tokens import _tok

TABLES = ("credentials", "pwd_mngt", "logs")


@pytest.fixture(autouse=True)
def _reset():
    settings.reset_options()
    _tok.__init__()
    yield
    settings.reset_options()
    _tok.__init__()


def _as_text(rows):
    return [{k: None if v is None else str(v) for k, v in row.items()} for row in rows]


def _assert_same(py_path, passphrase, r_dump):
    for table in TABLES:
        expected = r_dump[table]
        assert _as_text(read_db(py_path, table, passphrase)) == expected["rows"], table


@pytest.mark.oracle
def test_python_opens_databases_created_by_r(require_r, tmp_path):
    r = require_r.run_r_script("oracle_db_compat.R", tmp_path, ["r_write", str(tmp_path)])
    enc, plain = str(tmp_path / "r_enc.sqlite"), str(tmp_path / "r_plain.sqlite")

    _assert_same(enc, "secret", r["enc"])
    _assert_same(plain, None, r["plain"])

    # mots de passe haches par R : valides cote Python, chiffre ou non
    assert check_credentials(enc, passphrase="secret")("fanny", "azerty12")["result"] is True
    assert check_credentials(enc, passphrase="secret")("fanny", "bad")["result"] is False
    assert check_credentials(plain)("victor", "12345A")["result"] is True

    with pytest.raises(ValueError, match="wrong passphrase"):
        read_db(enc, "credentials", "not-the-secret")
    with pytest.raises(ValueError, match="passphrase is required"):
        read_db(enc, "credentials")


@pytest.mark.oracle
def test_r_opens_and_modifies_a_database_written_by_python(require_r, tmp_path):
    path = str(tmp_path / "py_enc.sqlite")
    create_db(
        [
            {"user": "fanny", "password": "azerty12", "email": "fanny@mail.com"},
            {"user": "victor", "password": "12345A", "admin": "TRUE", "email": "é@mail.com"},
        ],
        path,
        passphrase="secret",
    )
    # activite Python sur la base chiffree : echec de login (log + compteur), forcage
    check_credentials(path, passphrase="secret")
    logs.save_logs_failed("fanny", "Wrong pwd")
    pwd_lifecycle.force_chg_pwd("fanny", True)

    before = {t: _as_text(read_db(path, t, "secret")) for t in TABLES}
    r = require_r.run_r_script("oracle_db_compat.R", tmp_path, ["r_read", path])
    for table in TABLES:
        assert before[table] == r["before"][table]["rows"], table
    assert r["login_ok"] is True
    assert r["login_bad"] is False

    # modification ecrite par R, relue par Python
    pm = {row["user"]: row for row in read_db(path, "pwd_mngt", "secret")}
    assert pm["victor"]["must_change"] == "TRUE"
    assert pm["fanny"]["must_change"] == "TRUE"  # ecrit par Python, conserve par R
    assert [row["status"] for row in read_db(path, "logs", "secret")] == ["Wrong pwd"]
