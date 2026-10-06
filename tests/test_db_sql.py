"""Phase 7+8 — validation et tests du backend SQL (module db_sql).

Le contrat de config ayant ete redessine (DS1 option A), il n'y a pas d'oracle differentiel
contre le backend SQL du source (contrat YAML different). La validation repose sur :
  - un round-trip contre SQLite via SQLAlchemy (vrai moteur SQL, sans infra externe) ;
  - une coherence CROISEE avec le backend SQLite (module db), lui-meme valide contre R :
    memes users, meme init pwd_mngt, mots de passe verifiables -> les deux backends sont
    interchangeables pour la logique aval.
Postgres/MSSQL/MariaDB/Databricks : meme voie generique SQLAlchemy, non executes ici (infra).
"""

import sqlite3

import pytest

from shinymanager.db import create_db, read_db
from shinymanager.db_sql import create_sql_db, read_table_sql, write_sql_db
from shinymanager.passwords import verify_pwd

CREDENTIALS = [
    {"user": "alice", "password": "azerty", "admin": True},
    {"user": "bob", "password": "Bob12345", "applications": "app1;app2"},
]


@pytest.fixture
def conf(tmp_path):
    return {"connection": {"url": f"sqlite:///{tmp_path / 'sql.sqlite'}"}}


def test_create_sql_db_returns_true_and_hashes(conf):
    assert create_sql_db(CREDENTIALS, conf) is True
    cred = read_table_sql(conf, "credentials")
    by_user = {r["user"]: r for r in cred}
    assert verify_pwd(by_user["alice"]["password"], "azerty") is True
    assert by_user["alice"]["password"] != "azerty"
    # is_hashed_password non persistee cote SQL (comme le source).
    assert "is_hashed_password" not in cred[0]


def test_pwd_mngt_and_logs_initialised(conf):
    create_sql_db(CREDENTIALS, conf)
    pwd = read_table_sql(conf, "pwd_mngt")
    assert {r["user"] for r in pwd} == {"alice", "bob"}
    for r in pwd:
        assert r["must_change"] == "FALSE"
        assert r["have_changed"] == "FALSE"
        assert r["n_wrong_pwd"] == 0
    assert read_table_sql(conf, "logs") == []


def test_missing_password_raises(conf):
    with pytest.raises(ValueError, match="password"):
        create_sql_db([{"user": "u"}], conf)


def test_duplicated_users_raises(conf):
    with pytest.raises(ValueError, match="Duplicated"):
        create_sql_db([{"user": "u", "password": "a"}, {"user": "u", "password": "b"}], conf)


def test_create_sql_db_is_idempotent(conf):
    create_sql_db(CREDENTIALS, conf)
    create_sql_db(CREDENTIALS, conf)  # rejeu : ne doit pas dupliquer
    assert len(read_table_sql(conf, "credentials")) == 2
    assert len(read_table_sql(conf, "pwd_mngt")) == 2


def test_write_sql_db_appends_and_hashes(conf):
    create_sql_db(CREDENTIALS, conf)
    write_sql_db(conf, [{"user": "carol", "password": "Carol123"}], "credentials")
    cred = read_table_sql(conf, "credentials")
    assert len(cred) == 3
    carol = next(r for r in cred if r["user"] == "carol")
    assert verify_pwd(carol["password"], "Carol123") is True


def test_env_var_secret_interpolation(monkeypatch, tmp_path):
    monkeypatch.setenv("SM_TEST_DB", str(tmp_path / "env.sqlite"))
    conf = {"connection": {"url": "sqlite:///${SM_TEST_DB}"}}
    assert create_sql_db(CREDENTIALS, conf) is True
    assert len(read_table_sql(conf, "credentials")) == 2


def test_cross_backend_consistency_with_sqlite(conf, tmp_path):
    # Meme entree -> backend SQLite (module db, valide R) et backend SQL doivent produire le
    # meme contenu logique (les deux backends sont interchangeables pour la logique aval).
    sqlite_path = str(tmp_path / "creds.sqlite")
    create_db(CREDENTIALS, sqlite_path)
    create_sql_db(CREDENTIALS, conf)

    cred_file = read_db(sqlite_path, "credentials")
    cred_sql = read_table_sql(conf, "credentials")
    assert {r["user"] for r in cred_file} == {r["user"] for r in cred_sql}

    pwd_file = {r["user"]: r for r in read_db(sqlite_path, "pwd_mngt")}
    pwd_sql = {r["user"]: r for r in read_table_sql(conf, "pwd_mngt")}
    for user in pwd_file:
        assert pwd_file[user]["must_change"] == pwd_sql[user]["must_change"]
        assert pwd_file[user]["n_wrong_pwd"] == pwd_sql[user]["n_wrong_pwd"]

    # Les deux stockent un hash verifiable (jamais le clair).
    for rows in (cred_file, cred_sql):
        alice = next(r for r in rows if r["user"] == "alice")
        assert verify_pwd(alice["password"], "azerty") is True


def test_connection_by_components_and_dict_input(tmp_path):
    # URL construite par composants (pas de cle "url") + entree dict-de-colonnes.
    conf = {"connection": {"drivername": "sqlite", "database": str(tmp_path / "comp.sqlite")}}
    create_sql_db({"user": ["a", "b"], "password": ["Pwd12345", "Qwe12345"]}, conf)
    assert {r["user"] for r in read_table_sql(conf, "credentials")} == {"a", "b"}


def test_connect_every_request_uses_fresh_engine(tmp_path):
    conf = {
        "connection": {"url": f"sqlite:///{tmp_path / 'ev.sqlite'}"},
        "connect_every_request": True,
    }
    create_sql_db(CREDENTIALS, conf)
    assert len(read_table_sql(conf, "credentials")) == 2


def test_logs_table_schema(conf):
    create_sql_db(CREDENTIALS, conf)
    db_file = conf["connection"]["url"].replace("sqlite:///", "")
    conn = sqlite3.connect(db_file)
    try:
        cols = [d[1] for d in conn.execute("PRAGMA table_info(logs)").fetchall()]
    finally:
        conn.close()
    assert cols == ["id", "user", "server_connected", "token", "logout", "status", "app"]


def test_create_sql_db_accepts_a_yaml_config_path(tmp_path):
    # Le source R ne prend qu'un `config_path` : le chemin doit etre accepte.
    import yaml

    from shinymanager.db_sql import create_sql_db, read_table_sql

    conf = {"connection": {"url": f"sqlite:///{tmp_path / 'from_path.sqlite'}"}}
    config_path = tmp_path / "sql_config.yml"
    config_path.write_text(yaml.safe_dump(conf), encoding="utf-8")

    create_sql_db([{"user": "alice", "password": "Secret1"}], str(config_path))
    assert {r["user"] for r in read_table_sql(conf, "credentials")} == {"alice"}


def test_load_config_returns_a_dict_unchanged():
    from shinymanager.db_sql import load_config

    conf = {"connection": {"url": "sqlite://"}}
    assert load_config(conf) is conf
