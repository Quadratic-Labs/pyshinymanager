"""Phase 7 — validation CONTRAT du module db contre le package R source.

Le stockage physique differe volontairement (R = blob chiffre, Python = SQLite en clair, D4/D7),
donc on ne compare pas les octets mais le CONTENU LOGIQUE : schema, ordre des colonnes,
initialisation de pwd_mngt/logs, mots de passe haches verifiables. R (create_db sans passphrase)
fait office de reference.
"""

import json
import sqlite3

import pytest

from shinymanager.db import create_db, read_db
from shinymanager.passwords import verify_pwd

CREDENTIALS = [
    {"user": "alice", "password": "azerty", "admin": True},
    {"user": "bob", "password": "Bob12345", "admin": False, "applications": "app1;app2"},
]


@pytest.mark.oracle
def test_db_contract_matches_r(require_r, tmp_path):
    cred_file = tmp_path / "cred.json"
    cred_file.write_text(json.dumps(CREDENTIALS), encoding="utf-8")
    r = require_r.run_r_script("oracle_db.R", tmp_path, extra_args=[str(cred_file)])

    db_path = str(tmp_path / "py.sqlite")
    create_db(CREDENTIALS, db_path)

    # 1. credentials : meme ordre de colonnes que R.
    py_cred = read_db(db_path, "credentials")
    assert list(py_cred[0].keys()) == r["credentials"]["columns"]
    assert [row["user"] for row in py_cred] == [row["user"] for row in r["credentials"]["rows"]]

    # 2. mots de passe : haches (jamais en clair) et verifiables cote Python.
    by_user = {row["user"]: row for row in py_cred}
    assert verify_pwd(by_user["alice"]["password"], "azerty") is True
    assert verify_pwd(by_user["bob"]["password"], "Bob12345") is True
    assert by_user["alice"]["password"] != "azerty"
    # is_hashed_password pose (colonne presente, derniere, comme R).
    assert py_cred[0]["is_hashed_password"] == "TRUE"

    # 3. pwd_mngt : meme initialisation que R (colonnes + valeurs).
    py_pwd = read_db(db_path, "pwd_mngt")
    assert list(py_pwd[0].keys()) == r["pwd_mngt"]["columns"]
    for row in py_pwd:
        assert row["must_change"] == "FALSE"
        assert row["have_changed"] == "FALSE"
        assert row["date_change"] == r["today"]
        assert row["n_wrong_pwd"] == 0

    # 4. logs : vide, memes colonnes que R.
    py_logs = read_db(db_path, "logs")
    assert py_logs == []
    # colonnes logs verifiees via une re-lecture du schema.
    conn = sqlite3.connect(db_path)
    try:
        cols = [d[1] for d in conn.execute("PRAGMA table_info(logs)").fetchall()]
    finally:
        conn.close()
    assert cols == r["logs"]["columns"]
