"""Phase 7 — validation differentielle du reset en libre-service (1.1.1.1) contre le source R.

Rejoue la sequence de oracle_reset_password.R sur le backend SQLite Python et compare les
observables (codes reason, envoi du mail, mot de passe effectif, must_change, expiration, logs).
"""

import re
from datetime import datetime, timezone

import pytest

from shinymanager import pwd_lifecycle, reset_password, settings
from shinymanager.db import create_db, read_db
from shinymanager.passwords import verify_pwd
from shinymanager.tokens import _tok


@pytest.fixture(autouse=True)
def _reset():
    settings.reset_options()
    _tok.__init__()
    yield
    settings.reset_options()
    _tok.__init__()


def _new_db(path):
    create_db(
        [
            {"user": "fanny", "password": "azerty12", "email": "fanny@mail.com"},
            {"user": "victor", "password": "12345A", "email": "victor@mail.com"},
            {"user": "bob", "password": "bobpwd1", "email": None},
        ],
        str(path),
    )
    _tok.set_sqlite_path(str(path))
    return str(path)


class _Mailer:
    def __init__(self, fail=False):
        self.fail = fail
        self.reset()

    def reset(self):
        self.called, self.email, self.pwd = 0, None, None

    def __call__(self, user, email, temp_password):
        if self.fail:
            raise RuntimeError("smtp down")
        self.called += 1
        self.email, self.pwd = email, temp_password


def _pwd_of(path, user):
    return next(r for r in read_db(path, "credentials") if r["user"] == user)["password"]


def _pm_of(path, user):
    return next(r for r in read_db(path, "pwd_mngt") if r["user"] == user)


@pytest.mark.oracle
def test_reset_password_sqlite_matches_r(require_r, tmp_path):
    r = require_r.run_r_script("oracle_reset_password.R", tmp_path)
    mail = _Mailer()

    def run(user, email=None):
        mail.reset()
        res = reset_password.reset_pwd_user_email(user, email)
        return {
            "result": res["result"],
            "reason": res["reason"],
            "mail_called": mail.called,
            "mail_email": mail.email,
        }

    settings.set_option("write_logs", False)
    path = _new_db(tmp_path / "a.sqlite")

    settings.set_option("reset_password", True)
    assert run("fanny", "fanny@mail.com") == r["no_mailer"]

    _tok.set_send_mail(mail)
    assert run("  ", "fanny@mail.com") == r["empty_user"]
    assert run("fanny", "") == r["empty_email"]

    assert run("fanny", "  FANNY@Mail.com ") == r["success"]
    assert verify_pwd(_pwd_of(path, "fanny"), mail.pwd) == r["success_verify"]
    assert pwd_lifecycle._is_true(_pm_of(path, "fanny")["must_change"]) == r["success_must_change"]
    assert ("temp_pwd_expire" in _pm_of(path, "fanny")) == r["success_has_expire_col"]
    fanny_pwd = mail.pwd

    assert run("fanny", "other@mail.com") == r["wrong_email"]
    assert run("zoe", "zoe@mail.com") == r["unknown_user"]

    _tok.set_send_mail(_Mailer(fail=True))
    assert run("fanny", "fanny@mail.com") == r["mail_failed"]
    assert verify_pwd(_pwd_of(path, "fanny"), fanny_pwd) == r["mail_failed_pwd_unchanged"]
    _tok.set_send_mail(mail)

    settings.set_option("reset_password", "username")
    assert run("bob") == r["username_no_email"]
    assert run("victor") == r["username_success"]
    assert verify_pwd(_pwd_of(path, "victor"), mail.pwd) == r["username_verify"]

    settings.set_option("reset_password", True)
    settings.set_option("reset_password_validity", 20)
    assert run("fanny", "fanny@mail.com") == r["validity_success"]
    exp = _pm_of(path, "fanny")["temp_pwd_expire"]
    assert bool(re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", exp)) == r["validity_format"]
    delta = (
        datetime.fromisoformat(exp).replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
    ).total_seconds() / 60
    assert (19 < delta <= 20) == r["validity_delta_ok"]
    others = [row["temp_pwd_expire"] for row in read_db(path, "pwd_mngt")][1:3]
    assert all(v == "" for v in others) == r["validity_other_users_empty"]
    assert pwd_lifecycle.is_temp_pwd_expired("fanny") == r["expired_now"]

    pwd_lifecycle.set_temp_pwd_expire("fanny", "2000-01-01 00:00:00")
    assert pwd_lifecycle.is_temp_pwd_expired("fanny") == r["expired_past"]

    assert pwd_lifecycle.update_pwd("fanny", "Newpass1")["result"] == r["update_result"]
    assert _pm_of(path, "fanny")["temp_pwd_expire"] == r["expire_after_update"]
    assert pwd_lifecycle.is_temp_pwd_expired("fanny") == r["expired_after_update"]

    settings.set_option("reset_password_validity", "abc")
    assert (settings.get_reset_password_validity() is None) == r["validity_abc_na"]
    settings.set_option("reset_password_validity", 0)
    assert (settings.get_reset_password_validity() is None) == r["validity_zero_na"]
    settings.set_option("reset_password_validity", "15")
    assert settings.get_reset_password_validity() == r["validity_str15"]
    settings.set_option("reset_password_validity", None)

    settings.set_option("email_column", "mail")
    assert run("fanny", "fanny@mail.com") == r["no_email_column"]
    settings.set_option("email_column", "email")

    path2 = _new_db(tmp_path / "b.sqlite")
    assert pwd_lifecycle.set_temp_pwd_expire("fanny", "") == r["clear_without_col"]
    assert ("temp_pwd_expire" in _pm_of(path2, "fanny")) == r["clear_created_col"]

    reset_password.save_reset_logs("u0", "success")
    assert len(read_db(path2, "logs")) == r["logs_when_disabled"]
    settings.set_option("write_logs", True)
    for reason in (
        "success",
        "unknown_user",
        "email_mismatch",
        "no_stored_email",
        "mail_failed",
        "empty_input",
        "no_mailer",
        "db_error",
    ):
        reset_password.save_reset_logs(f"u_{reason}", reason)
    logs = read_db(path2, "logs")
    assert [row["user"] for row in logs] == r["logs_users"]
    assert [row["status"] for row in logs] == r["logs_status"]
