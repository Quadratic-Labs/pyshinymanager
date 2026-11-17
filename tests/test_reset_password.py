"""Tests du reset en libre-service hors oracle.

Couvre : backend SQL (colonne d'expiration absente / presente, ordre des controles), admin
(creation, reset admin, table), decision de login (mot de passe temporaire expire), routes HTTP
de la page de login (TestClient), helper SMTP (serveur simule) et module reactif standalone.
"""

import importlib
import smtplib
import warnings
from typing import ClassVar

import pytest
from shiny import ui
from sqlalchemy import create_engine, text
from starlette.testclient import TestClient

from shinymanager import admin, pwd_lifecycle, reset_password, settings
from shinymanager.auth_module import _evaluate_login, auth_ui, process_login
from shinymanager.check_credentials import check_credentials
from shinymanager.db import create_db, read_db
from shinymanager.db_sql import create_sql_db, read_table_sql
from shinymanager.passwords import verify_pwd
from shinymanager.secure_app import create_secure_app
from shinymanager.send_mail import send_smtp_mail
from shinymanager.tokens import _tok

GENERIC = "If the account exists and an email address is associated with it"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    settings.reset_options()
    _tok.__init__()
    reset_password._reset_link_warned.clear()
    # delai minimal de la route (5 s) neutralise pour garder les tests rapides
    monkeypatch.setattr(importlib.import_module("shinymanager.secure_app"), "RESET_BTN_DELAY", 0)
    yield
    settings.reset_options()
    _tok.__init__()


class _Mailer:
    def __init__(self):
        self.sent = []

    def __call__(self, user, email, temp_password):
        self.sent.append((user, email, temp_password))


@pytest.fixture
def sqlite_path(tmp_path):
    path = str(tmp_path / "db.sqlite")
    create_db(
        [
            {"user": "admin", "password": "Admin123", "admin": "TRUE", "email": "a@x.com"},
            {"user": "fanny", "password": "azerty12", "email": "fanny@mail.com"},
        ],
        path,
    )
    _tok.set_sqlite_path(path)
    return path


@pytest.fixture
def sql_conf(tmp_path):
    conf = {"connection": {"url": f"sqlite:///{tmp_path / 'sql.sqlite'}"}}
    create_sql_db([{"user": "fanny", "password": "azerty12", "email": "fanny@mail.com"}], conf)
    _tok.set_sql_config_db(conf)
    return conf


def _add_expire_column(conf):
    engine = create_engine(conf["connection"]["url"])
    with engine.begin() as cx:
        cx.execute(text("ALTER TABLE pwd_mngt ADD COLUMN temp_pwd_expire VARCHAR(19)"))


# --- Options ---


def test_reset_password_enabled_modes():
    assert settings.reset_password_enabled() is False
    settings.set_option("reset_password", True)
    assert settings.reset_password_enabled() and not settings.reset_password_username_only()
    settings.set_option("reset_password", "username")
    assert settings.reset_password_enabled() and settings.reset_password_username_only()
    settings.set_option("reset_password", "TRUE")  # isTRUE("TRUE") est FALSE en R
    assert settings.reset_password_enabled() is False


def test_reset_password_validity_infinite_means_no_expiration():
    settings.set_option("reset_password_validity", float("inf"))
    assert settings.get_reset_password_validity() is None
    assert reset_password.temp_pwd_expire_value() == ""
    settings.set_option("reset_password_validity", 1e15)  # hors des dates representables
    assert reset_password.temp_pwd_expire_value() == ""


def test_changes_made_during_mail_sending_are_kept(sqlite_path):
    # Le reset tourne dans un thread ; une ecriture concurrente pendant l'envoi
    # du mail ne doit pas etre ecrasee par une lecture perimee.
    def mail_with_concurrent_change(user, email, temp_password):
        pwd_lifecycle.update_pwd("admin", "Changed99")
        admin.add_user({"user": "zoe", "password": "Zoe12345"})

    _tok.set_send_mail(mail_with_concurrent_change)
    assert reset_password.reset_pwd_user_email("fanny", "fanny@mail.com")["result"] is True
    creds = {r["user"]: r for r in read_db(sqlite_path, "credentials")}
    assert "zoe" in creds
    assert verify_pwd(creds["admin"]["password"], "Changed99")


# --- Backend SQL ---


def test_sql_reset_without_expire_column(sql_conf):
    mail = _Mailer()
    _tok.set_send_mail(mail)
    settings.set_option("reset_password_validity", 20)
    res = reset_password.reset_pwd_user_email("fanny", "fanny@mail.com")
    assert res == {"result": True, "reason": "success"}
    row = read_table_sql(sql_conf, "credentials")[0]
    assert verify_pwd(row["password"], mail.sent[0][2])
    pm = read_table_sql(sql_conf, "pwd_mngt")[0]
    assert pwd_lifecycle._is_true(pm["must_change"])
    # colonne jamais creee sur un backend SQL : pas d'expiration
    assert "temp_pwd_expire" not in pm
    assert pwd_lifecycle.is_temp_pwd_expired("fanny") is False


def test_sql_reset_with_expire_column(sql_conf):
    _add_expire_column(sql_conf)
    _tok.set_send_mail(_Mailer())
    settings.set_option("reset_password_validity", 20)
    reset_password.reset_pwd_user_email("fanny", "fanny@mail.com")
    assert read_table_sql(sql_conf, "pwd_mngt")[0]["temp_pwd_expire"]
    assert pwd_lifecycle.is_temp_pwd_expired("fanny") is False
    pwd_lifecycle.set_temp_pwd_expire("fanny", "2000-01-01 00:00:00")
    assert pwd_lifecycle.is_temp_pwd_expired("fanny") is True
    assert pwd_lifecycle.update_pwd("fanny", "Newpass1")["result"] is True
    assert read_table_sql(sql_conf, "pwd_mngt")[0]["temp_pwd_expire"] == ""


def test_sql_email_column_checked_before_user(sql_conf):
    # Comme le source : la colonne est controlee avant l'utilisateur, meme reponse connu / inconnu.
    _tok.set_send_mail(_Mailer())
    settings.set_option("email_column", "mail")
    for user in ("zoe", "fanny"):
        with pytest.warns(UserWarning, match="'mail' column"):
            res = reset_password.reset_pwd_user_email(user, "z@x.com")
        assert res["reason"] == "no_email_column"


def test_no_backend_reason():
    _tok.set_send_mail(_Mailer())
    assert reset_password.reset_pwd_user_email("fanny", "f@x.com")["reason"] == "backend"


def test_db_error_after_mail_rolls_everything_back(sqlite_path, monkeypatch):
    _tok.set_send_mail(_Mailer())
    real = reset_password.db.write_in_transaction

    def boom(cx, value, name, passphrase=None):
        if name == "pwd_mngt":
            raise OSError("disk full")
        real(cx, value, name, passphrase)

    monkeypatch.setattr(reset_password.db, "write_in_transaction", boom)
    with pytest.warns(UserWarning, match="unable to save the reset password"):
        res = reset_password.reset_pwd_user_email("fanny", "fanny@mail.com")
    assert res == {"result": False, "reason": "db_error"}
    # credentials ecrit avant l'erreur : annule par le rollback
    fanny = next(r for r in read_db(sqlite_path, "credentials") if r["user"] == "fanny")
    assert verify_pwd(fanny["password"], "azerty12")


# --- Admin ---


def test_admin_reset_clears_mailed_expiration(sqlite_path):
    pwd_lifecycle.set_temp_pwd_expire("fanny", "2000-01-01 00:00:00")
    assert pwd_lifecycle.is_temp_pwd_expired("fanny") is True
    admin.reset_password("fanny")
    assert pwd_lifecycle.is_temp_pwd_expired("fanny") is False


def test_admin_add_user_and_table_with_expire_column(sqlite_path):
    pwd_lifecycle.set_temp_pwd_expire("fanny", "2030-01-01 00:00:00")
    admin.add_user({"user": "zoe", "password": "Zoe12345"})
    zoe = next(r for r in read_db(sqlite_path, "pwd_mngt") if r["user"] == "zoe")
    assert zoe["temp_pwd_expire"] == ""
    assert all("temp_pwd_expire" not in r for r in admin.list_pwds())


# --- Decision de login ---


def test_evaluate_login_temp_expired_and_priority():
    ok = {"result": True, "user_info": {}}
    out = _evaluate_login(ok, locked=False, temp_pwd_expired=True)
    assert out.authenticated is False
    assert out.log_status == "Reset password: expired"
    assert out.message_key.startswith("Your temporary password has expired")
    # verrouille prime sur expire
    assert _evaluate_login(ok, locked=True, temp_pwd_expired=True).log_status == "Locked Account"


def test_process_login_refuses_expired_temp_password(sqlite_path):
    pwd_lifecycle.set_temp_pwd_expire("fanny", "2000-01-01 00:00:00")
    outcome, token, _ = process_login("fanny", "azerty12", check_credentials(sqlite_path))
    assert outcome.authenticated is False and token is None
    assert read_db(sqlite_path, "logs")[-1]["status"] == "Reset password: expired"
    # mauvais mot de passe : message classique, l'expiration n'est pas revelee
    outcome, _, _ = process_login("fanny", "wrong", check_credentials(sqlite_path))
    assert outcome.log_status == "Wrong pwd"


# --- Page de login et route HTTP ---


def _client(sqlite_path, mail=None):
    app = create_secure_app(ui.div("SECRET"), check_credentials(sqlite_path), send_mail=mail)
    return TestClient(app)


def test_no_reset_link_without_option(sqlite_path):
    page = _client(sqlite_path).get("/").text
    assert "Forgot password?" not in page


def test_reset_link_and_hidden_form_with_option(sqlite_path):
    settings.set_option("reset_password", True)
    page = _client(sqlite_path, _Mailer()).get("/").text
    assert "Forgot password?" in page
    assert 'id="auth-reset_pwd_form"' in page and "display:none;" in page
    assert 'id="auth-reset_email"' in page
    settings.set_option("reset_password", "username")
    assert 'id="auth-reset_email"' not in _client(sqlite_path, _Mailer()).get("/").text


def test_reset_link_hidden_when_reset_cannot_work(sqlite_path):
    # Sans send_mail ou sans base, le lien menait toujours a un echec.
    settings.set_option("reset_password", True)
    with pytest.warns(UserWarning):
        assert "Forgot password?" not in _client(sqlite_path).get("/").text
    users = [{"user": "fanny", "password": "azerty12"}]
    app = create_secure_app(ui.div("SECRET"), check_credentials(users), send_mail=_Mailer())
    assert "Forgot password?" not in TestClient(app).get("/").text


def test_reset_link_hidden_without_email_column(tmp_path):
    # Comme le source : sans colonne email, le lien est masque avec un warning, une fois par raison.
    path = str(tmp_path / "noemail.sqlite")
    create_db([{"user": "fanny", "password": "azerty12"}], path)
    settings.set_option("reset_password", True)
    client = TestClient(
        create_secure_app(ui.div("SECRET"), check_credentials(path), send_mail=_Mailer())
    )
    with pytest.warns(UserWarning, match="link is hidden: no 'email' column"):
        assert "Forgot password?" not in client.get("/").text
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert "Forgot password?" not in client.get("/").text


def test_reset_link_unavailable_reason(sqlite_path, sql_conf, monkeypatch):
    assert "send_mail" in reset_password.reset_link_unavailable_reason()
    _tok.set_send_mail(_Mailer())
    _tok.set_sqlite_path(None)
    assert reset_password.reset_link_unavailable_reason() is None  # SQL, colonne email
    settings.set_option("email_column", "mail")
    assert "'mail' column" in reset_password.reset_link_unavailable_reason()
    _tok.set_sqlite_path(sqlite_path)
    assert "'mail' column" in reset_password.reset_link_unavailable_reason()
    settings.set_option("email_column", "email")
    assert reset_password.reset_link_unavailable_reason() is None
    # colonnes illisibles : lien garde, l'erreur se verra au reset (source)
    monkeypatch.setattr(reset_password.db, "_read_table", lambda *a: 1 / 0)
    assert reset_password.reset_link_unavailable_reason() is None


def test_reset_route_same_message_whatever_the_outcome(sqlite_path):
    settings.set_option("reset_password", True)
    settings.set_option("reset_password_cooldown", 0)
    mail = _Mailer()
    client = _client(sqlite_path, mail)
    pages = []
    for user, email in (("fanny", "fanny@mail.com"), ("ghost", "g@x.com"), ("fanny", "bad@x.com")):
        resp = client.post(
            "/sm-reset-password", data={"user": user, "email": email, "language": "en"}
        )
        assert resp.url.query.decode() == "reset=sent&language=en"
        pages.append(resp.text)
    assert all(GENERIC in p for p in pages)
    assert len(mail.sent) == 1 and mail.sent[0][:2] == ("fanny", "fanny@mail.com")
    statuses = [r["status"] for r in read_db(sqlite_path, "logs")]
    assert statuses == [
        "Reset password",
        "Reset password: unknown user",
        "Reset password: wrong email",
    ]


def test_reset_message_is_translated(sqlite_path):
    settings.set_option("reset_password", True)
    resp = _client(sqlite_path, _Mailer()).post(
        "/sm-reset-password", data={"user": "fanny", "email": "fanny@mail.com", "language": "fr"}
    )
    assert "Mot de passe oubli" in resp.text
    assert "un mail contenant un mot de passe temporaire va" in resp.text


def test_reset_route_inert_without_option(sqlite_path):
    mail = _Mailer()
    resp = _client(sqlite_path, mail).post(
        "/sm-reset-password", data={"user": "fanny", "email": "fanny@mail.com"}
    )
    assert "reset=sent" not in str(resp.url)
    assert mail.sent == []


def test_expired_temp_password_login_shows_dedicated_message(sqlite_path):
    pwd_lifecycle.set_temp_pwd_expire("fanny", "2000-01-01 00:00:00")
    resp = _client(sqlite_path).post("/sm-login", data={"user": "fanny", "password": "azerty12"})
    assert "error=temp_expired" in str(resp.url)
    assert "Your temporary password has expired, please request a new one." in resp.text


def test_full_reset_cycle_forces_password_change(sqlite_path):
    settings.set_option("reset_password", "username")
    mail = _Mailer()
    client = _client(sqlite_path, mail)
    client.post("/sm-reset-password", data={"user": "fanny"})
    temp = mail.sent[0][2]
    resp = client.post("/sm-login", data={"user": "fanny", "password": temp})
    assert "Please change your password" in resp.text


# --- Helper SMTP ---


class _FakeSMTP:
    instances: ClassVar[list] = []

    def __init__(self, host, port, **kwargs):
        self.host, self.port, self.calls = host, port, []
        self.timeout = kwargs.get("timeout")
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        self.calls.append("ehlo")

    def has_extn(self, name):
        return name == "starttls"

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, pwd):
        self.calls.append(("login", user, pwd))

    def send_message(self, msg):
        self.calls.append(("send", msg["To"], msg["Subject"], msg.get_content_type()))


def test_send_smtp_mail_starttls_and_login(monkeypatch):
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    _FakeSMTP.instances.clear()
    assert send_smtp_mail(
        "to@x.com",
        "Reset",
        "<b>hi</b>",
        "from@x.com",
        "smtp.x.com",
        username="u",
        password="p",
        html=True,
    )
    calls = _FakeSMTP.instances[0].calls
    assert _FakeSMTP.instances[0].timeout == 30
    assert "starttls" in calls and ("login", "u", "p") in calls
    assert ("send", "to@x.com", "Reset", "text/html") in calls


def test_send_smtp_mail_port_465_uses_ssl(monkeypatch):
    monkeypatch.setattr("smtplib.SMTP_SSL", _FakeSMTP)
    _FakeSMTP.instances.clear()
    send_smtp_mail("to@x.com", "Reset", "hi", "from@x.com", "smtp.x.com", port=465)
    calls = _FakeSMTP.instances[0].calls
    assert "starttls" not in calls and not any(
        c[0] == "login" for c in calls if isinstance(c, tuple)
    )
    assert ("send", "to@x.com", "Reset", "text/plain") in calls


# --- Module reactif standalone ---


def test_auth_ui_reset_link_follows_option():
    assert "show_reset_pwd" not in str(auth_ui("auth"))
    settings.set_option("reset_password", True)
    html = str(auth_ui("auth"))
    assert 'id="auth-show_reset_pwd"' in html and 'id="auth-reset_pwd_panel"' in html


class _FlakySMTP(_FakeSMTP):
    """Leve `errors` successivement a la connexion, puis se comporte normalement."""

    errors: ClassVar[list] = []

    def __init__(self, host, port, **kwargs):
        if _FlakySMTP.errors:
            raise _FlakySMTP.errors.pop(0)
        super().__init__(host, port, **kwargs)


@pytest.fixture
def flaky(monkeypatch):
    sleeps = []
    monkeypatch.setattr("smtplib.SMTP", _FlakySMTP)
    monkeypatch.setattr("shinymanager.send_mail.time.sleep", sleeps.append)
    _FakeSMTP.instances.clear()
    return sleeps


def test_send_smtp_mail_retries_transient_errors_with_backoff(flaky):
    _FlakySMTP.errors = [TimeoutError("slow"), smtplib.SMTPConnectError(421, b"busy")]
    assert send_smtp_mail("to@x.com", "Reset", "hi", "from@x.com", "smtp.x.com", max_times=3)
    assert flaky == [1, 2]
    assert len(_FakeSMTP.instances) == 1


def test_send_smtp_mail_gives_up_after_max_times(flaky):
    _FlakySMTP.errors = [TimeoutError("slow")] * 3
    with pytest.raises(TimeoutError):
        send_smtp_mail("to@x.com", "Reset", "hi", "from@x.com", "smtp.x.com", max_times=2)
    assert flaky == [1]


def test_send_smtp_mail_single_attempt_by_default(flaky):
    _FlakySMTP.errors = [TimeoutError("slow")]
    with pytest.raises(TimeoutError):
        send_smtp_mail("to@x.com", "Reset", "hi", "from@x.com", "smtp.x.com")
    assert flaky == []


def test_send_smtp_mail_does_not_retry_permanent_errors(flaky):
    _FlakySMTP.errors = [smtplib.SMTPAuthenticationError(535, b"bad credentials")]
    with pytest.raises(smtplib.SMTPAuthenticationError):
        send_smtp_mail("to@x.com", "Reset", "hi", "from@x.com", "smtp.x.com", max_times=3)
    assert flaky == []


def test_warns_when_reset_enabled_without_send_mail(sqlite_path):
    settings.set_option("reset_password", True)
    with pytest.warns(UserWarning, match="no 'send_mail' function"):
        _client(sqlite_path)


def test_no_warning_when_send_mail_given_or_option_off(sqlite_path, recwarn):
    _client(sqlite_path)  # option off
    settings.set_option("reset_password", True)
    _client(sqlite_path, _Mailer())
    assert not [w for w in recwarn if "send_mail" in str(w.message)]


# --- Controles du compte, limites, messages, robustesse ---


def _post_reset(client, user, email=None):
    data = {"user": user, "language": "en"}
    if email is not None:
        data["email"] = email
    return client.post("/sm-reset-password", data=data)


def _statuses(path):
    return [r["status"] for r in read_db(path, "logs")]


def test_reset_route_empty_fields(sqlite_path):
    settings.set_option("reset_password", True)
    mail = _Mailer()
    resp = _post_reset(_client(sqlite_path, mail), "fanny", " ")
    assert "reset=empty" in str(resp.url)
    assert "Please fill in all fields." in resp.text
    assert mail.sent == [] and _statuses(sqlite_path) == []


def test_cooldown_blocks_a_second_mail(sqlite_path):
    settings.set_option("reset_password", "username")
    mail = _Mailer()
    client = _client(sqlite_path, mail)
    _post_reset(client, "fanny")
    resp = _post_reset(client, " fanny ")
    assert "reset=sent" in str(resp.url)
    assert len(mail.sent) == 1
    assert _statuses(sqlite_path) == ["Reset password", "Reset password: too soon"]


def test_failed_reset_does_not_start_the_cooldown(sqlite_path):
    settings.set_option("reset_password", True)
    mail = _Mailer()
    client = _client(sqlite_path, mail)
    _post_reset(client, "fanny", "bad@x.com")
    _post_reset(client, "fanny", "fanny@mail.com")
    assert len(mail.sent) == 1


def test_no_reset_for_expired_or_unauthorized_account(tmp_path):
    path = str(tmp_path / "db.sqlite")
    rows = [
        {"user": "old", "password": "azerty12", "email": "o@x.com", "expire": "2000-01-01"},
        {"user": "other", "password": "azerty12", "email": "x@x.com"},
    ]
    rows[0]["applications"], rows[1]["applications"] = "app", "b"
    create_db(rows, path)
    _tok.set_sqlite_path(path)
    settings.set_option("application", "app")
    mail = _Mailer()
    _tok.set_send_mail(mail)
    assert reset_password.reset_pwd_user_email("old", "o@x.com")["reason"] == "account_expired"
    assert reset_password.reset_pwd_user_email("other", "x@x.com")["reason"] == "unauthorized"
    assert mail.sent == []


def test_reset_password_max_blocks_and_counter_resets(sqlite_path):
    settings.set_option("reset_password", "username")
    settings.set_option("reset_password_cooldown", 0)
    settings.set_option("reset_password_max", 1)
    mail = _Mailer()
    client = _client(sqlite_path, mail)
    _post_reset(client, "fanny")
    assert pwd_lifecycle.get_n_user_reset("fanny") == 1
    resp = _post_reset(client, "fanny")
    assert "reset=blocked" in str(resp.url)
    assert "Password reset is not available for this account" in resp.text
    assert len(mail.sent) == 1
    assert _statuses(sqlite_path)[-1] == "Reset password: too many resets"
    # colonne visible dans l'admin quand une limite est posee
    assert "n_user_reset" in admin.list_pwds()[0]
    # remis a 0 par un reset admin
    admin.reset_password("fanny")
    assert pwd_lifecycle.get_n_user_reset("fanny") == 0


def test_successful_login_resets_counters_even_without_logs(sqlite_path):
    from shinymanager.logs import save_logs, save_logs_failed

    settings.set_option("reset_password_max", 3)
    pwd_lifecycle.set_pwd_mngt_optional("fanny", "n_user_reset", 2, empty=0)
    settings.set_option("write_logs", False)
    save_logs_failed("fanny", "Wrong pwd")
    _tok.add("tok", {"user": "fanny"})
    save_logs("tok")
    row = next(r for r in read_db(sqlite_path, "pwd_mngt") if r["user"] == "fanny")
    assert row["n_wrong_pwd"] == 0 and row["n_user_reset"] == 0


def test_reset_unlock_option(sqlite_path):
    settings.set_option("pwd_failure_limit", 2)
    for _ in range(2):
        process_login("fanny", "wrong", check_credentials(sqlite_path))
    mail = _Mailer()
    _tok.set_send_mail(mail)
    settings.set_option("reset_password_unlock", False)
    res = reset_password.reset_pwd_user_email("fanny", "fanny@mail.com")
    assert res["reason"] == "locked_account"
    settings.set_option("reset_password_unlock", True)
    assert reset_password.reset_pwd_user_email("fanny", "fanny@mail.com")["result"] is True
    assert pwd_lifecycle.check_locked_account("fanny", 2) is False


def test_mail_error_is_shown_and_logged(sqlite_path):
    settings.set_option("reset_password", True)

    def failing(*_a):
        raise ConnectionError("smtp down")

    with pytest.warns(UserWarning, match="unable to send the reset password email"):
        resp = _post_reset(_client(sqlite_path, failing), "fanny", "fanny@mail.com")
    assert "reset=mail_error" in str(resp.url)
    assert "An error occurred while sending the email" in resp.text
    assert _statuses(sqlite_path) == ["Reset password: mail failed"]


def test_unexpected_db_error_does_not_crash_the_route(sqlite_path, monkeypatch):
    settings.set_option("reset_password", True)

    def boom(*_a, **_k):
        raise OSError("database locked")

    monkeypatch.setattr(reset_password, "_read", boom)
    with pytest.warns(UserWarning, match="password reset failed"):
        resp = _post_reset(_client(sqlite_path, _Mailer()), "fanny", "fanny@mail.com")
    assert "reset=db_error" in str(resp.url)
    assert "An error occurred while saving the new password" in resp.text


def test_account_modified_while_sending_is_not_overwritten(sqlite_path):
    def mail_and_admin_change(user, email, temp_password):
        pwd_lifecycle.update_pwd("fanny", "ByAdmin99")

    _tok.set_send_mail(mail_and_admin_change)
    with pytest.warns(UserWarning, match="account modified while sending"):
        res = reset_password.reset_pwd_user_email("fanny", "fanny@mail.com")
    assert res["reason"] == "db_error"
    fanny = next(r for r in read_db(sqlite_path, "credentials") if r["user"] == "fanny")
    assert verify_pwd(fanny["password"], "ByAdmin99")


def test_sql_account_modified_while_sending_is_not_overwritten(sql_conf):
    def mail_and_admin_change(user, email, temp_password):
        pwd_lifecycle.update_pwd("fanny", "ByAdmin99")

    _tok.set_send_mail(mail_and_admin_change)
    with pytest.warns(UserWarning, match="account modified while sending"):
        res = reset_password.reset_pwd_user_email("fanny", "fanny@mail.com")
    assert res["reason"] == "db_error"
    assert verify_pwd(read_table_sql(sql_conf, "credentials")[0]["password"], "ByAdmin99")


def test_missing_pwd_mngt_row_stops_before_sending(sqlite_path):
    from shinymanager.db import write_db

    rows = [r for r in read_db(sqlite_path, "pwd_mngt") if r["user"] != "fanny"]
    write_db(sqlite_path, rows, "pwd_mngt")
    mail = _Mailer()
    _tok.set_send_mail(mail)
    with pytest.warns(UserWarning, match="user not found in pwd_mngt"):
        res = reset_password.reset_pwd_user_email("fanny", "fanny@mail.com")
    assert res["reason"] == "db_error"
    assert mail.sent == []


def test_sql_timestamp_expire_column_stops_before_sending(sql_conf):
    engine = create_engine(sql_conf["connection"]["url"])
    with engine.begin() as cx:
        cx.execute(text("ALTER TABLE pwd_mngt ADD COLUMN temp_pwd_expire TIMESTAMP"))
    mail = _Mailer()
    _tok.set_send_mail(mail)
    with pytest.warns(UserWarning, match="must be a text column"):
        res = reset_password.reset_pwd_user_email("fanny", "fanny@mail.com")
    assert res["reason"] == "db_error"
    assert mail.sent == []


def test_stored_email_is_trimmed_before_sending(tmp_path):
    path = str(tmp_path / "db.sqlite")
    create_db([{"user": "fanny", "password": "azerty12", "email": " fanny@mail.com "}], path)
    _tok.set_sqlite_path(path)
    mail = _Mailer()
    _tok.set_send_mail(mail)
    reset_password.reset_pwd_user_email("fanny", "fanny@mail.com")
    assert mail.sent[0][1] == "fanny@mail.com"


def test_unknown_reset_code_shows_no_message(sqlite_path):
    settings.set_option("reset_password", True)
    page = _client(sqlite_path, _Mailer()).get("/?reset=<script>").text
    assert 'id="auth-reset_pwd_msg"' not in page
