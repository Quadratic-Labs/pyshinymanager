"""Bases SQLite chiffrees (format du package R) : format, round-trip et usage complet."""

import sqlite3
from pathlib import Path

import pytest
from shiny import ui
from starlette.testclient import TestClient

from shinymanager import admin, pwd_lifecycle, reset_password, settings
from shinymanager.check_credentials import check_credentials
from shinymanager.db import create_db, read_db, write_db
from shinymanager.passwords import verify_pwd
from shinymanager.rserialize import (
    RSerializeError,
    serialize_data_frame,
    unserialize_data_frame,
)
from shinymanager.secure_app import create_secure_app
from shinymanager.tokens import _tok


@pytest.fixture(autouse=True)
def _reset():
    settings.reset_options()
    _tok.__init__()
    yield
    settings.reset_options()
    _tok.__init__()


@pytest.fixture
def enc_path(tmp_path):
    path = str(tmp_path / "enc.sqlite")
    create_db(
        [
            {"user": "admin", "password": "Admin123", "admin": "TRUE", "email": "a@x.com"},
            {"user": "fanny", "password": "azerty12", "email": "fanny@mail.com"},
        ],
        path,
        passphrase="secret",
    )
    return path


def test_r_serialization_round_trip():
    cols = ["user", "n", "flag", "note"]
    rows = [
        {"user": "élodie", "n": 3, "flag": True, "note": None},
        {"user": None, "n": 2.5, "flag": None, "note": "x"},
    ]
    names, back = unserialize_data_frame(serialize_data_frame(cols, rows))
    assert names == cols
    assert back == [
        {"user": "élodie", "n": 3, "flag": "TRUE", "note": None},
        {"user": None, "n": 2.5, "flag": None, "note": "x"},
    ]
    assert unserialize_data_frame(serialize_data_frame(["a", "b"], [])) == (["a", "b"], [])


def test_unserialize_rejects_non_r_data():
    with pytest.raises(RSerializeError):
        unserialize_data_frame(b"not R data")


def test_tables_are_really_encrypted(enc_path):
    con = sqlite3.connect(enc_path)
    cols = [d[0] for d in con.execute('SELECT * FROM "credentials"').description]
    blob = con.execute('SELECT "value" FROM "credentials"').fetchone()[0]
    con.close()
    assert cols == ["value", "iv"]
    assert b"fanny" not in blob and b"mail.com" not in blob


def test_wrong_or_missing_passphrase(enc_path):
    with pytest.raises(ValueError, match="wrong passphrase"):
        read_db(enc_path, "credentials", "bad")
    with pytest.raises(ValueError, match="passphrase is required"):
        read_db(enc_path, "credentials")
    plain = enc_path.replace("enc.sqlite", "plain.sqlite")
    create_db([{"user": "u", "password": "p"}], plain)
    with pytest.raises(ValueError, match="not encrypted"):
        read_db(plain, "credentials", "secret")


def test_empty_table_keeps_its_columns(enc_path):
    write_db(enc_path, [], "logs", "secret")
    con = sqlite3.connect(enc_path)
    blob, iv = con.execute('SELECT "value", "iv" FROM "logs"').fetchone()
    con.close()
    from shinymanager.db import _decrypt

    names, rows = unserialize_data_frame(_decrypt(blob, iv, "secret"))
    assert names == ["user", "server_connected", "token", "logout", "app"] and rows == []


def test_whole_app_on_encrypted_database(enc_path):
    settings.set_option("pwd_failure_limit", 3)
    app = create_secure_app(ui.div("SECRET"), check_credentials(enc_path, passphrase="secret"))
    client = TestClient(app)

    assert "error=credentials" in str(
        client.post("/sm-login", data={"user": "fanny", "password": "x"}).url
    )
    pm = {r["user"]: r for r in read_db(enc_path, "pwd_mngt", "secret")}
    assert pm["fanny"]["n_wrong_pwd"] == 1
    assert read_db(enc_path, "logs", "secret")[-1]["status"] == "Wrong pwd"

    assert "SECRET" in client.post("/sm-login", data={"user": "fanny", "password": "azerty12"}).text

    admin.add_user({"user": "zoe", "password": "Zoe12345"})
    temp = admin.reset_password("zoe")
    creds = {r["user"]: r for r in read_db(enc_path, "credentials", "secret")}
    assert verify_pwd(creds["zoe"]["password"], temp)
    assert pwd_lifecycle.update_pwd("zoe", "Newpass1")["result"] is True

    settings.set_option("reset_password", "username")
    sent = []
    _tok.set_send_mail(lambda user, email, pwd: sent.append(pwd))
    assert reset_password.reset_pwd_user_email("fanny")["result"] is True
    creds = {r["user"]: r for r in read_db(enc_path, "credentials", "secret")}
    assert verify_pwd(creds["fanny"]["password"], sent[0])


def test_unserialize_r_made_fixture_with_all_column_types():
    # Fichier produit par R : serialize(data.frame(...)) avec texte accentue, numerique, logique,
    # entier, facteur, Date, POSIXct (UTC), as.character(1:3) et 1:3 (representations compactes).
    data = (Path(__file__).parent / "data" / "r_data_frame.bin").read_bytes()
    names, rows = unserialize_data_frame(data)
    assert names == ["user", "n", "ok", "i", "f", "d", "t", "s", "q"]
    assert rows[0] == {
        "user": "a",
        "n": 0,
        "ok": "TRUE",
        "i": 1,
        "f": "x",
        "d": "2024-01-02",
        "t": "2026-10-06 10:00:00",
        "s": "1",
        "q": 1,
    }
    assert rows[1]["user"] == "é" and rows[1]["n"] == 2.5 and rows[1]["i"] is None
    assert rows[2] == {
        "user": None,
        "n": None,
        "ok": None,
        "i": 3,
        "f": "x",
        "d": "2020-02-29",
        "t": "2000-01-01 00:00:00",
        "s": "3",
        "q": 3,
    }
