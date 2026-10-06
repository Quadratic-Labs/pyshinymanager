"""Phase 8 — tests du module de login (approche DU1 : logique + structure, sans navigateur)."""

import pytest

from shinymanager import settings
from shinymanager.auth_module import _evaluate_login, auth_ui, process_login
from shinymanager.check_credentials import check_credentials
from shinymanager.db import create_db, read_db
from shinymanager.tokens import _tok


@pytest.fixture(autouse=True)
def _reset():
    settings.reset_options()
    _tok.__init__()
    yield
    settings.reset_options()
    _tok.__init__()


# --- Arbre de decision (pur) : transcription fidele de l'observeEvent du source ---


def test_evaluate_login_success():
    out = _evaluate_login({"result": True, "user_info": {}}, locked=False)
    assert out.authenticated is True
    assert out.log_status is None


def test_evaluate_login_locked():
    out = _evaluate_login({"result": True, "user_info": {}}, locked=True)
    assert out.authenticated is False
    assert out.log_status == "Locked Account"
    assert out.message_key == "Your account is locked"


def test_evaluate_login_unknown_user():
    out = _evaluate_login({"result": False, "user_info": None}, locked=False)
    assert out.log_status == "Unknown user"
    assert out.message_key == "Username or password are incorrect"


def test_evaluate_login_expired():
    out = _evaluate_login(
        {"result": False, "user_info": {"user": "u"}, "expired": True}, locked=False
    )
    assert out.log_status == "Expired"
    assert out.message_key == "Your account has expired"


def test_evaluate_login_unauthorized():
    out = _evaluate_login(
        {"result": False, "user_info": {"user": "u"}, "expired": False, "authorized": False},
        locked=False,
    )
    assert out.log_status == "Unauthorized"
    assert out.message_key == "You are not authorized for this application"


def test_evaluate_login_wrong_pwd():
    out = _evaluate_login(
        {"result": False, "user_info": {"user": "u"}, "expired": False, "authorized": True},
        locked=False,
    )
    assert out.log_status == "Wrong pwd"
    assert out.message_key == "Username or password are incorrect"


# --- process_login contre un vrai backend SQLite (integration, sans navigateur) ---


@pytest.fixture
def sqlite_check(tmp_path):
    path = str(tmp_path / "c.sqlite")
    create_db([{"user": "alice", "password": "Secret1"}], path)
    return check_credentials(path), path


def test_process_login_success_generates_token(sqlite_check):
    check, _path = sqlite_check
    outcome, token, _user_info = process_login("alice", "Secret1", check)
    assert outcome.authenticated is True
    assert token is not None
    assert _tok.is_valid_server(token) is True
    assert _tok.get_user(token) == "alice"


def test_process_login_wrong_pwd_increments_and_no_token(sqlite_check):
    check, path = sqlite_check
    outcome, token, _ = process_login("alice", "bad", check)
    assert outcome.authenticated is False
    assert token is None
    assert outcome.message_key == "Username or password are incorrect"
    nwp = next(r["n_wrong_pwd"] for r in read_db(path, "pwd_mngt") if r["user"] == "alice")
    assert nwp == 1


def test_process_login_locked_blocks_even_with_good_pwd(sqlite_check):
    from shinymanager.db import write_db

    check, path = sqlite_check
    settings.set_option("pwd_failure_limit", 2)
    pwd = read_db(path, "pwd_mngt")
    for row in pwd:
        row["n_wrong_pwd"] = 2
    write_db(path, pwd, "pwd_mngt")

    outcome, token, _ = process_login("alice", "Secret1", check)
    assert outcome.authenticated is False
    assert outcome.message_key == "Your account is locked"
    assert token is None


# --- Structure de l'UI (sans navigateur) ---


def test_auth_ui_contains_namespaced_inputs():
    html = auth_ui("auth").get_html_string()
    assert "auth-user_id" in html
    assert "auth-user_pwd" in html
    assert "auth-go_auth" in html
