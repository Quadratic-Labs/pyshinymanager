"""Phase 8 — tests du module de changement de mot de passe (approche DU1)."""

import pytest

from shinymanager import settings
from shinymanager.check_credentials import check_credentials
from shinymanager.db import create_db
from shinymanager.pwd_module import process_pwd_change, pwd_ui
from shinymanager.tokens import _tok


@pytest.fixture(autouse=True)
def _reset():
    settings.reset_options()
    _tok.__init__()
    yield
    settings.reset_options()
    _tok.__init__()


@pytest.fixture
def sqlite_backend(tmp_path):
    path = str(tmp_path / "c.sqlite")
    create_db([{"user": "alice", "password": "Secret1"}], path)
    # check_credentials cable _tok sur le backend SQLite (pour check_new_pwd / update_pwd).
    check_credentials(path)
    return path


def test_passwords_mismatch(sqlite_backend):
    out = process_pwd_change("alice", "Newpass1", "Different1")
    assert out.success is False
    assert out.message_key == "The two passwords are different"


def test_same_as_old(sqlite_backend):
    out = process_pwd_change("alice", "Secret1", "Secret1")
    assert out.success is False
    assert out.message_key == "New password cannot be the same as old"


def test_weak_password_rejected(sqlite_backend):
    # Different de l'ancien mais ne respecte pas la politique.
    out = process_pwd_change("alice", "abc", "abc")
    assert out.success is False
    assert out.message_key == "Password does not respect safety requirements"


def test_successful_change_updates_password(sqlite_backend):
    path = sqlite_backend
    out = process_pwd_change("alice", "Newpass1", "Newpass1")
    assert out.success is True
    assert out.message_key == "Password successfully updated! Please re-login"
    # Le nouveau mot de passe authentifie, l'ancien non.
    check = check_credentials(path)
    assert check("alice", "Newpass1")["result"] is True
    assert check("alice", "Secret1")["result"] is False


def test_custom_validator_used(sqlite_backend):
    # Un validateur qui refuse tout -> message politique meme sur un mdp fort.
    out = process_pwd_change("alice", "Newpass1", "Newpass1", validate_pwd=lambda _p: False)
    assert out.success is False
    assert out.message_key == "Password does not respect safety requirements"


def test_pwd_ui_contains_namespaced_inputs():
    html = pwd_ui("pwd").get_html_string()
    assert "pwd-pwd_one" in html
    assert "pwd-pwd_two" in html
    assert "pwd-update_pwd" in html
