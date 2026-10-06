"""Phase 8 — tests unitaires du module db (comportement, sans dependance R)."""

import sqlite3

import pytest

from shinymanager.db import create_db, read_db, write_db
from shinymanager.passwords import verify_pwd


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "creds.sqlite")


def test_create_db_creates_three_tables(db_path):
    create_db([{"user": "u", "password": "Secret1"}], db_path)
    conn = sqlite3.connect(db_path)
    try:
        names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert {"credentials", "pwd_mngt", "logs"}.issubset(names)


def test_create_db_column_order_and_defaults(db_path):
    create_db([{"user": "u", "password": "Secret1", "role": "x"}], db_path)
    cred = read_db(db_path, "credentials")
    assert list(cred[0].keys()) == [
        "user",
        "password",
        "start",
        "expire",
        "admin",
        "role",
        "is_hashed_password",
    ]
    assert cred[0]["start"] is None
    assert cred[0]["expire"] is None


def test_password_is_hashed_and_verifiable(db_path):
    create_db([{"user": "u", "password": "Secret1"}], db_path)
    cred = read_db(db_path, "credentials")
    assert cred[0]["password"] != "Secret1"
    assert cred[0]["is_hashed_password"] == "TRUE"
    assert verify_pwd(cred[0]["password"], "Secret1") is True


def test_na_stays_none_not_string(db_path):
    create_db([{"user": "u", "password": "Secret1", "start": None}], db_path)
    cred = read_db(db_path, "credentials")
    assert cred[0]["start"] is None  # pas la chaine "NA"/"None"


def test_missing_required_columns_raises(db_path):
    with pytest.raises(ValueError, match="user"):
        create_db([{"user": "u"}], db_path)


def test_duplicated_users_raises(db_path):
    with pytest.raises(ValueError, match="Duplicated"):
        create_db([{"user": "u", "password": "a"}, {"user": "u", "password": "b"}], db_path)


def test_pwd_mngt_initialised(db_path):
    create_db([{"user": "u", "password": "Secret1"}], db_path)
    pwd = read_db(db_path, "pwd_mngt")
    assert pwd[0]["must_change"] == "FALSE"
    assert pwd[0]["have_changed"] == "FALSE"
    assert pwd[0]["n_wrong_pwd"] == 0  # int preserve (colonne non typee)


def test_write_db_idempotent_hash(db_path):
    create_db([{"user": "u", "password": "Secret1"}], db_path)
    cred = read_db(db_path, "credentials")
    first_hash = cred[0]["password"]
    # Reecrire la table lue (is_hashed_password = "TRUE") ne doit pas re-hasher.
    write_db(db_path, cred, "credentials")
    cred2 = read_db(db_path, "credentials")
    assert cred2[0]["password"] == first_hash


def test_dict_of_columns_input(db_path):
    create_db({"user": ["a", "b"], "password": ["Pwd12345", "Qwe12345"]}, db_path)
    cred = read_db(db_path, "credentials")
    assert [r["user"] for r in cred] == ["a", "b"]
