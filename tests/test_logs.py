"""Phase 8 — tests unitaires de la journalisation (sans dependance R).

Couvre le backend SQL (cross-backend), le cycle de verrouillage complet (increment ->
check_locked_account -> reset), et la garde write_logs.
"""

import pytest

from shinymanager import logs, pwd_lifecycle, settings
from shinymanager.db_sql import create_sql_db, read_table_sql
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
    create_sql_db([{"user": "alice", "password": "Secret1"}], conf)
    _tok.set_sql_config_db(conf)
    _tok.add("tok", {"user": "alice"})
    return conf


def test_read_logs_dedup_is_faithful_to_source(tmp_path):
    # Source (modules-logs.R:138-143) : dedup UNIQUEMENT si tokens dupliques, cle (user, token,
    # jour). D5 corrige : plus de dedup toutes-colonnes inconditionnelle.
    from shinymanager.db import create_db, write_db

    path = str(tmp_path / "c.sqlite")
    create_db([{"user": "alice", "password": "Secret1"}], path)
    _tok.set_sqlite_path(path)

    # Deux lignes de meme token, meme user, meme jour -> collapse a une (vieux log admin).
    # Une 3e ligne, token distinct, contenu par ailleurs identique -> conservee.
    write_db(
        path,
        [
            {
                "user": "a",
                "server_connected": "2026-07-15 10:00:00",
                "token": "t1",
                "logout": None,
                "app": "x",
                "status": "Success",
            },
            {
                "user": "a",
                "server_connected": "2026-07-15 11:00:00",
                "token": "t1",
                "logout": None,
                "app": "x",
                "status": "Success",
            },
            {
                "user": "a",
                "server_connected": "2026-07-15 12:00:00",
                "token": "t2",
                "logout": None,
                "app": "x",
                "status": "Success",
            },
        ],
        "logs",
    )
    rows = logs.read_logs()
    tokens = sorted(r["token"] for r in rows)
    assert tokens == ["t1", "t2"]  # t1 dedupe (meme user/jour), t2 garde

    # Sans token duplique : aucune dedup, meme si des lignes sont par ailleurs identiques.
    write_db(
        path,
        [
            {
                "user": "b",
                "server_connected": "2026-07-15 10:00:00",
                "token": "u1",
                "logout": None,
                "app": "x",
                "status": "Success",
            },
            {
                "user": "b",
                "server_connected": "2026-07-15 10:00:00",
                "token": "u2",
                "logout": None,
                "app": "x",
                "status": "Success",
            },
        ],
        "logs",
    )
    assert len(logs.read_logs()) == 2


def _nwp_sql(conf):
    return next(r["n_wrong_pwd"] for r in read_table_sql(conf, "pwd_mngt") if r["user"] == "alice")


def test_sql_full_lock_cycle(sql_conf):
    # increment via echecs, verrouillage detecte, reset via login reussi.
    logs.save_logs_failed("alice", "Wrong pwd")
    logs.save_logs_failed("alice", "Wrong pwd")
    assert _nwp_sql(sql_conf) == 2
    assert pwd_lifecycle.check_locked_account("alice", 2) is True

    logs.save_logs("tok")
    assert _nwp_sql(sql_conf) == 0
    assert pwd_lifecycle.check_locked_account("alice", 2) is False


def test_sql_failed_log_written_and_success_logged(sql_conf):
    logs.save_logs_failed("alice", "Wrong pwd")
    logs.save_logs("tok")
    rows = read_table_sql(sql_conf, "logs")
    assert any(r["status"] == "Wrong pwd" for r in rows)
    assert any(r["status"] == "Success" for r in rows)


def test_write_logs_disabled_skips_all_log_writes_but_still_increments(tmp_path):
    # ECART ASSUME (gate module 7) : write_logs=False bloque l'ecriture du log dans les DEUX
    # backends (SQLite aligne sur SQL), MAIS l'increment n_wrong_pwd a toujours lieu.
    from shinymanager.db import create_db, read_db

    path = str(tmp_path / "c.sqlite")
    create_db([{"user": "alice", "password": "Secret1"}], path)
    _tok.set_sqlite_path(path)
    _tok.add("tok", {"user": "alice"})

    settings.set_option("write_logs", False)
    logs.save_logs("tok")  # succes : garde -> aucune ligne
    assert read_db(path, "logs") == []

    logs.save_logs_failed("alice", "Wrong pwd")
    assert read_db(path, "logs") == []  # aucun log ecrit (aligne sur SQL)
    # mais le compteur d'echecs est bien incremente (verrouillage sans logs).
    nwp = next(r["n_wrong_pwd"] for r in read_db(path, "pwd_mngt") if r["user"] == "alice")
    assert nwp == 1


def test_no_backend_is_noop():
    # Aucune exception sans backend configure.
    logs.save_logs("t")
    logs.save_logs_failed("u", "Wrong pwd")
    logs.logout_logs("t")
