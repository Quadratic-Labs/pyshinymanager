"""Phase 7+8 — routage et round-trip cookie de secure_app.

Le round-trip cookie (login -> cookie httponly -> app affichee -> logout) est valide en reel via
`TestClient` (httpx) sur l'App ASGI assemblee, sans navigateur. Le routage pur est teste a part.
"""

import pytest
from shiny import ui
from starlette.testclient import TestClient

from shinymanager import settings
from shinymanager.check_credentials import check_credentials
from shinymanager.secure_app import COOKIE_NAME, _route, create_secure_app
from shinymanager.tokens import _tok


@pytest.fixture(autouse=True)
def _reset():
    settings.reset_options()
    _tok.__init__()
    yield
    settings.reset_options()
    _tok.__init__()


# --- Routage pur ---


def test_route_login_when_no_token():
    assert _route(None, enable_admin=False, admin_requested=False, has_backend=False) == "login"


def test_route_login_when_token_unknown():
    assert _route("ghost", enable_admin=False, admin_requested=False, has_backend=False) == "login"


def test_route_app_when_token_valid():
    _tok.add("t", {"user": "u"})
    assert _route("t", enable_admin=False, admin_requested=False, has_backend=False) == "app"


def test_route_admin_when_requested_and_admin_and_backend():
    _tok.add("t", {"user": "u", "admin": "TRUE"})
    assert _route("t", enable_admin=True, admin_requested=True, has_backend=True) == "admin"


def test_route_app_when_admin_requested_but_not_admin():
    _tok.add("t", {"user": "u", "admin": "FALSE"})
    assert _route("t", enable_admin=True, admin_requested=True, has_backend=True) == "app"


# --- Round-trip cookie reel (TestClient, sans navigateur) ---


@pytest.fixture
def client():
    check = check_credentials([{"user": "alice", "password": "azerty"}])
    app = create_secure_app(ui.div("SECRET-CONTENT", id="protected"), check)
    return TestClient(app)


def test_login_page_shown_without_cookie(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "sm-login-form" in r.text
    assert "SECRET-CONTENT" not in r.text


def test_login_sets_httponly_cookie_and_grants_access(client):
    r = client.post(
        "/sm-login", data={"user": "alice", "password": "azerty"}, follow_redirects=False
    )
    assert r.status_code == 303
    set_cookie = r.headers["set-cookie"].lower()
    assert COOKIE_NAME in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie

    # Le cookie est memorise par le client -> la page protegee s'affiche.
    r2 = client.get("/")
    assert "SECRET-CONTENT" in r2.text
    assert "sm-login-form" not in r2.text


def test_wrong_password_redirects_to_error(client):
    r = client.post("/sm-login", data={"user": "alice", "password": "bad"}, follow_redirects=False)
    assert r.status_code == 303
    assert "error=credentials" in r.headers["location"]
    # Sans cookie valide, la page reste le login.
    assert "sm-login-form" in client.get("/").text


def test_logout_clears_cookie(client):
    client.post("/sm-login", data={"user": "alice", "password": "azerty"})
    assert "SECRET-CONTENT" in client.get("/").text  # authentifie

    client.get("/sm-logout")
    # Apres logout, retour a la page de login.
    assert "sm-login-form" in client.get("/").text


# --- Page admin : assemblage navbar + options (cf. surface/admin-panel.md §1) ---


@pytest.fixture
def sqlite_admin_backend(tmp_path):
    from shinymanager.db import create_db

    path = str(tmp_path / "c.sqlite")
    create_db([{"user": "alice", "password": "Secret1", "admin": "TRUE"}], path)
    _tok.set_sqlite_path(path)
    return path


def _admin_html(**options):
    import importlib

    sa = importlib.import_module("shinymanager.secure_app")
    for name, value in options.items():
        settings.set_option(name, value)
    return sa._admin_page("en").tagify().get_html_string()


def test_admin_page_is_a_navbar_with_home_and_logs(sqlite_admin_backend):
    html = _admin_html()
    assert 'id="sm_admin_nv"' in html  # navbarPage(id = "sm_admin_nv") du source
    assert "Admin" in html
    assert "admin-add_user" in html  # onglet Home
    assert "logs-graph_conn_users" in html  # onglet Logs
    # Marqueurs caches + style navbar du source.
    assert 'id="shinymanager_where"' in html
    assert 'id="shinymanager_language"' in html
    assert ".navbar-header {margin-left: 16.66% !important;}" in html


def test_admin_page_fab_has_logout_and_go_to_app(sqlite_admin_backend):
    # Bloquant P0 : sans FAB, la page admin est un piege (aucune sortie).
    html = _admin_html()
    assert 'id="sm_logout_link"' in html
    assert 'id="sm_app_link"' in html


def test_show_logs_option_gates_the_logs_tab(sqlite_admin_backend):
    # Option auparavant MORTE : elle doit reellement conditionner l'onglet Logs.
    assert "logs-graph_conn_users" in _admin_html(show_logs=True)
    html = _admin_html(show_logs=False)
    assert "logs-graph_conn_users" not in html
    assert "admin-add_user" in html  # l'onglet Home reste


def test_download_option_gates_every_download_button(sqlite_admin_backend):
    # Option auparavant MORTE : elle doit reellement conditionner les 3 telechargements.
    html = _admin_html(download=[])
    for button in ("download_users_database", "download_sql_database", "download_logs"):
        assert button not in html, button
    full = _admin_html(download=["db", "logs", "users"])
    for button in ("download_users_database", "download_sql_database", "download_logs"):
        assert button in full, button


# --- user_info : valeur de retour de secure_server (source secure-app.R:318-338) ---


def test_parse_user_info_splits_applications_on_semicolon():
    from shinymanager.secure_app import parse_user_info

    out = parse_user_info({"user": "alice", "applications": "app1;app2"})
    assert out["applications"] == ["app1", "app2"]
    assert out["user"] == "alice"


def test_parse_user_info_splits_multiple_columns_from_inputs_list():
    from shiny import ui

    from shinymanager.secure_app import parse_user_info

    inputs_list = {"teams": {"fun": ui.input_select, "args": {"multiple": True}}}
    out = parse_user_info({"teams": "a;b", "note": "x;y"}, inputs_list)
    assert out["teams"] == ["a", "b"]
    assert out["note"] == "x;y"  # non declaree multiple -> laissee telle quelle


def test_parse_user_info_leaves_none_and_lists_alone():
    from shinymanager.secure_app import parse_user_info

    out = parse_user_info({"applications": None, "other": ["deja", "liste"]})
    assert out["applications"] is None
    assert out["other"] == ["deja", "liste"]


def _fake_session(cookies):
    """Session minimale : `user_info` lit le cookie via root_scope().http_conn."""
    from types import SimpleNamespace

    session = SimpleNamespace(http_conn=SimpleNamespace(cookies=cookies))
    session.root_scope = lambda: session
    return session


def test_user_info_returns_empty_without_session_cookie():
    from shinymanager.secure_app import user_info

    assert user_info(_fake_session({})) == {}


def test_user_info_reads_the_token_from_the_root_session_cookie():
    from shinymanager.secure_app import COOKIE_NAME, user_info

    _tok.add("tok", {"user": "alice", "admin": "TRUE", "applications": "a;b"})
    out = user_info(_fake_session({COOKIE_NAME: "tok"}))
    assert out["user"] == "alice"
    assert out["applications"] == ["a", "b"]


# --- Non-regression des ecarts trouves par le controle adversarial (2026-07-15) -------------


def test_logout_stamps_the_logout_column_before_forgetting_the_token(tmp_path):
    # P0 : logout_logs n'etait jamais appele -> colonne `logout` structurellement vide.
    from shinymanager import logs as logs_mod
    from shinymanager.db import create_db
    from shinymanager.secure_app import _logout_handler

    path = str(tmp_path / "c.sqlite")
    create_db([{"user": "alice", "password": "azerty"}], path)
    _tok.set_sqlite_path(path)
    _tok.add("tok", {"user": "alice"})
    logs_mod.save_logs("tok")
    assert all(r["logout"] is None for r in logs_mod.read_logs())

    import asyncio
    from types import SimpleNamespace

    asyncio.run(_logout_handler(SimpleNamespace(cookies={COOKIE_NAME: "tok"})))
    rows = [r for r in logs_mod.read_logs() if r["token"] == "tok"]
    assert rows and all(r["logout"] is not None for r in rows)


def test_login_errors_are_distinct_and_translated():
    # P1 : les 4 refus du source etaient ecrases en "Username or password are incorrect".
    from shinymanager.secure_app import _ERROR_CODES, LOGIN_ERRORS

    assert LOGIN_ERRORS["locked"] == "Your account is locked"
    assert LOGIN_ERRORS["expired"] == "Your account has expired"
    assert LOGIN_ERRORS["unauthorized"] == "You are not authorized for this application"
    assert LOGIN_ERRORS["credentials"] == "Username or password are incorrect"
    # Chaque cle produite par _evaluate_login doit avoir un code (sinon le message est perdu).
    for key in LOGIN_ERRORS.values():
        assert key in _ERROR_CODES


def test_locked_account_shows_its_own_message(tmp_path):
    from shinymanager import settings as settings_mod
    from shinymanager.db import create_db
    from shinymanager.i18n import use_language
    from shinymanager.secure_app import _login_page

    path = str(tmp_path / "c.sqlite")
    create_db([{"user": "alice", "password": "azerty"}], path)
    _tok.set_sqlite_path(path)
    settings_mod.set_option("pwd_failure_limit", 1)
    check = check_credentials(path)
    app = create_secure_app(ui.div("SECRET"), check)
    client = TestClient(app)
    client.post("/sm-login", data={"user": "alice", "password": "bad"})  # 1 echec -> lock
    r = client.post(
        "/sm-login", data={"user": "alice", "password": "azerty"}, follow_redirects=False
    )
    assert "error=locked" in r.headers["location"]
    html = _login_page("fr", error="locked").get_html_string()
    assert use_language("fr").get("Your account is locked") in html


def test_unknown_error_code_shows_no_message():
    # Liste blanche : ?error=<n'importe quoi> ne doit rien injecter dans la page.
    from shinymanager.secure_app import _login_page

    html = _login_page("en", error="<b>pwned</b>").get_html_string()
    assert "pwned" not in html
    assert "auth-msg_auth" not in html


def test_language_selector_uses_the_source_labels():
    # P2 : un _REGISTERED recopie a la main avait introduit "Francais"/"Espanol".
    from shinymanager.secure_app import _REGISTERED

    assert _REGISTERED["fr"] == "Français"
    assert _REGISTERED["es"] == "Español"


def test_choose_language_accepts_a_plain_string():
    # P2 : choose_language="fr" etait silencieusement ignore (une str est iterable).
    from shinymanager.secure_app import _language_selector

    html = _language_selector("en", "fr").get_html_string()
    assert 'value="fr"' in html
    assert "display:none" not in html  # 2 langues -> selecteur visible


def test_hidden_markers_are_present_on_every_screen(sqlite_admin_backend):
    # API publique documentee du source : input$shinymanager_where a CHAQUE etape.
    import importlib

    sa = importlib.import_module("shinymanager.secure_app")
    login = sa._login_page("en").get_html_string()
    assert 'id="shinymanager_where"' in login and "authentication" in login
    pwd = sa._pwd_page("en").get_html_string()
    assert 'id="shinymanager_where"' in pwd and "password" in pwd
    assert 'id="shinymanager_language"' in pwd
    app = sa._app_page(ui.div("X"), False, "en", "bottom-right").get_html_string()
    assert 'id="shinymanager_where"' in app and "application" in app


def test_bindenter_is_actually_loaded_on_login_and_pwd():
    # P2 : l'asset etait embarque mais aucune page ne le chargeait (touche Entree morte).
    import importlib

    sa = importlib.import_module("shinymanager.secure_app")
    assert "bindEnter.js" in sa._login_page("en").get_html_string()
    assert "bindEnter.js" in sa._pwd_page("en").get_html_string()


def test_timeout_script_is_not_on_the_password_screen():
    # Le source ne charge timeout.js QUE sur admin_ui et la branche application
    # (module-admin.R:18, secure-app.R:144) : l'ajouter a l'ecran pwd etait une invention.
    import importlib

    sa = importlib.import_module("shinymanager.secure_app")
    assert "shinymanager_timeout" not in sa._pwd_page("en").get_html_string()
    app_html = sa._app_page(ui.div("X"), False, "en", "none").get_html_string()
    assert "shinymanager_timeout" in app_html


def test_theme_reaches_login_and_pwd(tmp_path):
    # Le source thematise login/pwd/admin (fluidPage/navbarPage theme=) ; `theme` n'etait
    # transmis qu'a la navbar admin. py-shiny attache le theme en dependance HTML : on verifie
    # donc que le passer CHANGE les dependances de la page.
    import importlib

    css = tmp_path / "my-theme.css"
    css.write_text("body{}", encoding="utf-8")
    sa = importlib.import_module("shinymanager.secure_app")

    def deps(page):
        return {dep.name for dep in page.tagify().get_dependencies()}

    assert deps(sa._login_page("en", theme=str(css))) - deps(sa._login_page("en"))
    assert deps(sa._pwd_page("en", theme=str(css))) - deps(sa._pwd_page("en"))
