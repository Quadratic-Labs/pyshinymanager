"""Phase 8 — tests unitaires de check_credentials (comportement, sans dependance R)."""

import pytest

from shinymanager import settings
from shinymanager.check_credentials import check_credentials
from shinymanager.db import create_db
from shinymanager.db_sql import create_sql_db
from shinymanager.tokens import _tok


@pytest.fixture(autouse=True)
def _reset():
    settings.reset_options()
    _tok.__init__()
    yield
    settings.reset_options()
    _tok.__init__()


def test_df_result_and_user_info():
    creds = [{"user": "fanny", "password": "azerty", "admin": "TRUE", "role": "x"}]
    auth = check_credentials(creds)("fanny", "azerty")
    assert auth["result"] is True
    assert auth["expired"] is False
    assert auth["authorized"] is True
    # user_info expose toutes les colonnes SAUF password/is_hashed_password.
    assert auth["user_info"] == {"user": "fanny", "admin": "TRUE", "role": "x"}


def test_df_unknown_user():
    auth = check_credentials([{"user": "a", "password": "b"}])("ghost", "x")
    assert auth == {"result": False, "expired": False, "authorized": False, "user_info": None}


def test_user_info_excludes_password_columns():
    creds = [{"user": "fanny", "password": "azerty", "is_hashed_password": "FALSE"}]
    auth = check_credentials(creds)("fanny", "azerty")
    assert "password" not in auth["user_info"]
    assert "is_hashed_password" not in auth["user_info"]


def test_sqlite_backend_sets_tok_and_authenticates(tmp_path):
    path = str(tmp_path / "c.sqlite")
    create_db([{"user": "alice", "password": "Secret1"}], path)
    check = check_credentials(path)
    assert _tok.get_sqlite_path() == path
    assert check("alice", "Secret1")["result"] is True
    assert check("alice", "bad")["result"] is False


def test_sql_backend_sets_tok_and_authenticates(tmp_path):
    conf = {"connection": {"url": f"sqlite:///{tmp_path / 'sql.sqlite'}"}}
    create_sql_db([{"user": "alice", "password": "Secret1"}], conf)
    check = check_credentials(conf)
    assert _tok.get_sql_config_db() == conf
    assert check("alice", "Secret1")["result"] is True
    assert check("alice", "bad")["result"] is False


def test_applications_unauthorized_blocks_login():
    settings.set_option("application", "myapp")
    creds = [{"user": "u", "password": "p", "applications": "other"}]
    auth = check_credentials(creds)("u", "p")
    assert auth["result"] is False
    assert auth["authorized"] is False


def test_invalid_db_type_raises():
    with pytest.raises(ValueError, match="must be"):
        check_credentials(42)


def test_sqlite_closure_sees_password_change(tmp_path):
    # #5 (P0) : la closure SQLite doit RELIRE la base a chaque appel. Le changement de mot de
    # passe (via update_pwd / reset) doit prendre effet immediatement pour la meme closure,
    # sinon l'ancien mot de passe reste valide jusqu'au redemarrage.
    from shinymanager.pwd_lifecycle import update_pwd

    path = str(tmp_path / "c.sqlite")
    create_db([{"user": "alice", "password": "OldPass1"}], path)
    check = check_credentials(path)  # closure creee AVANT le changement
    assert check("alice", "OldPass1")["result"] is True

    update_pwd("alice", "NewPass1")  # ecrit + re-hache en base
    assert check("alice", "OldPass1")["result"] is False  # l'ancien ne marche plus
    assert check("alice", "NewPass1")["result"] is True  # le nouveau marche, meme closure


# --- Q4 : deviation sur start/expire (Jeremy 2026-07-16) ---


def test_empty_expire_is_not_reinjected_into_user_info():
    # DEVIATION : un compte SANS expiration ne doit PAS se voir attribuer une expiration dans
    # user_info (le source reinjectait today+1) -> l'app affiche vide, comme l'admin.
    creds = [{"user": "u", "password": "p", "start": None, "expire": None}]
    auth = check_credentials(creds)("u", "p")
    assert auth["result"] is True
    assert auth["user_info"].get("expire") is None
    assert auth["user_info"].get("start") is None


def test_account_without_expiry_can_still_log_in():
    # Le controle de validite reste correct : sans expiration, l'acces est accorde.
    creds = [{"user": "u", "password": "p", "expire": None}]
    assert check_credentials(creds)("u", "p")["result"] is True


def test_expired_account_is_still_blocked():
    # Non-regression : un compte reellement expire reste refuse (expired=True).
    creds = [{"user": "u", "password": "p", "expire": "2000-01-01"}]
    auth = check_credentials(creds)("u", "p")
    assert auth["result"] is False
    assert auth["expired"] is True


def test_future_expiry_is_preserved_in_user_info():
    # Une vraie date d'expiration reste visible dans user_info (non ecrasee).
    creds = [{"user": "u", "password": "p", "expire": "2999-12-31"}]
    auth = check_credentials(creds)("u", "p")
    assert auth["result"] is True
    assert auth["user_info"]["expire"] == "2999-12-31"
