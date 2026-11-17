"""Reinitialisation de mot de passe en libre-service (source R/reset-password.R).

Deux modes, selon l'option `reset_password` (module settings) :
  - True       : l'utilisateur fournit son nom ET l'email associe (verifie contre credentials) ;
  - "username" : l'utilisateur ne fournit que son nom ; le mot de passe temporaire part vers
                 l'email stocke pour lui (s'il y en a un).

Un mot de passe temporaire est genere puis envoye par la fonction `send_mail` configuree dans
`secure_server`. MAIL D'ABORD : le nouveau mot de passe n'est ecrit que si l'envoi a reussi (un
serveur mail en panne ne verrouille jamais l'utilisateur). L'ecriture (credentials + pwd_mngt) se
fait en UNE transaction, abandonnee si le mot de passe ou l'email ont change pendant l'envoi.
L'utilisateur doit ensuite le changer au prochain login (must_change), et le mot de passe peut
avoir une validite limitee (`reset_password_validity`, cf. pwd_lifecycle.set_temp_pwd_expire).

Pas de reset pour un compte expire ou non autorise pour l'application ; options
`reset_password_max` (resets d'affilee), `reset_password_unlock` (deverrouillage reserve a
l'admin) et `reset_password_cooldown` (delai entre deux mails, cf. `handle_reset_request`).

Backends SQLite et SQL seulement (le backend data.frame n'est pas persistant). Le visiteur voit
un message generique, sauf pour les raisons de `RESET_REASON_BLOCKED` (message dedie) et de
`RESET_REASON_ERROR` (message d'erreur), qui revelent que le compte existe (comme le source).
"""

from __future__ import annotations

import math
import warnings
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update

from shinymanager import db, db_sql, settings
from shinymanager.logs import save_logs_failed
from shinymanager.passwords import generate_pwd, hash_pwd
from shinymanager.pwd_lifecycle import (
    N_USER_RESET,
    TEMP_PWD_EXPIRE,
    _active,
    _read,
    check_locked_account,
    get_n_user_reset,
    read_pwd_mngt_user,
)
from shinymanager.tokens import _tok

#: Code interne -> statut ecrit dans les logs admin. Seule une saisie vide n'est pas journalisee.
_RESET_LOG_STATUS: dict[str, str] = {
    "success": "Reset password",
    "unknown_user": "Reset password: unknown user",
    "email_mismatch": "Reset password: wrong email",
    "no_stored_email": "Reset password: no email",
    "account_expired": "Reset password: account expired",
    "unauthorized": "Reset password: unauthorized",
    "locked_account": "Reset password: locked account",
    "too_many_resets": "Reset password: too many resets",
    "too_soon": "Reset password: too soon",
    "mail_failed": "Reset password: mail failed",
    "db_error": "Reset password: db error",
    "no_mailer": "Reset password: config error",
    "no_email_column": "Reset password: config error",
    "backend": "Reset password: config error",
}

#: Raisons montrees au visiteur par un message dedie : le compte existe mais ne peut pas etre
#: reinitialise, il faut un administrateur.
RESET_REASON_BLOCKED = ("locked_account", "too_many_resets")
#: Raisons montrees comme une erreur : envoi impossible, sauvegarde impossible, mauvaise config.
RESET_REASON_ERROR = ("mail_failed", "db_error", "no_mailer", "no_email_column", "backend")
#: Delai minimal (secondes) avant de pouvoir refaire une demande, quelle que soit l'issue : le
#: temps de reponse ne revele pas quels comptes existent.
RESET_BTN_DELAY = 5


def _warn(message: str) -> None:
    warnings.warn(f"shinymanager: {message}", stacklevel=3)


def email_reset_reason(stored_email: Any, email: str, require_email: bool) -> str:
    """Decide, d'apres l'email stocke, si le reset est permis.

    Returns:
        "ok" | "no_stored_email" | "email_mismatch" (comparaison sans espaces de bord et
        insensible a la casse).
    """
    stored = "" if stored_email is None else str(stored_email).strip()
    if not stored:
        return "no_stored_email"
    if require_email and stored.lower() != email.lower():
        return "email_mismatch"
    return "ok"


def _to_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def account_reset_reason(user: str, user_info: dict[str, Any]) -> str:
    """Controles du compte, une fois l'utilisateur et l'email valides.

    Memes regles que `check_credentials` : pas de reset pour un compte qui ne peut pas se
    connecter. `user_info` est la ligne credentials de l'utilisateur.

    Returns:
        "ok" | "account_expired" | "unauthorized" | "locked_account" | "too_many_resets".
    """
    start, expire = _to_date(user_info.get("start")), _to_date(user_info.get("expire"))
    today = date.today()
    if (start is not None and start > today) or (expire is not None and expire < today):
        return "account_expired"
    if "applications" in user_info:
        apps = str(user_info.get("applications") or "").split(";")
        if settings.get_option("application") not in apps:
            return "unauthorized"
    if not settings.reset_password_unlock_enabled():
        limit = settings.get_pwd_failure_limit()
        if settings.is_finite_limit(limit) and check_locked_account(user, float(limit)):
            return "locked_account"
    if get_n_user_reset(user) >= settings.get_reset_password_max():
        return "too_many_resets"
    return "ok"


def save_reset_logs(user: str, reason: str) -> None:
    """Journalise une tentative de reset dans les logs admin (statut selon `reason`)."""
    if not settings.get_option("write_logs"):
        return
    status = _RESET_LOG_STATUS.get(reason)
    if status is not None:
        save_logs_failed(user, status=status)


def temp_pwd_expire_value() -> str:
    """Expiration a stocker pour un mot de passe temporaire tout juste envoye ("" sans validite)."""
    validity = settings.get_reset_password_validity()
    if validity is None:
        return ""
    try:
        expire = datetime.now(timezone.utc) + timedelta(minutes=validity)
    except OverflowError:
        # validite hors des dates representables : pas d'expiration (comme Inf)
        return ""
    return expire.strftime("%Y-%m-%d %H:%M:%S")


def check_reset_pwd_mngt(user: str) -> None:
    """Leve une erreur si la ligne pwd_mngt de `user` ne peut pas porter un mot de passe temporaire.

    Pas de ligne (le mot de passe ne serait pas temporaire), ou colonne SQL `temp_pwd_expire`
    non texte (ecrire "" dans un timestamp echouerait apres l'envoi du mail).
    """
    if len(read_pwd_mngt_user(user)) != 1:
        raise RuntimeError("user not found in pwd_mngt")
    kind, handle = _active()
    if kind == "sql":
        col_type = db_sql.column_types(handle, "pwd_mngt").get(TEMP_PWD_EXPIRE)
        try:
            python_type = col_type.python_type if col_type is not None else str
        except NotImplementedError:
            python_type = str
        if issubclass(python_type, date):  # date et datetime
            raise RuntimeError("the 'temp_pwd_expire' column of pwd_mngt must be a text column")


def reset_pwd_user_email(user: str | None, email: str | None = None) -> dict[str, Any]:
    """Reinitialise le mot de passe de `user` et l'envoie par mail.

    Returns:
        ``{"result": bool, "reason": str}`` ; `reason` parmi success, no_mailer, empty_input,
        backend, no_email_column, unknown_user, no_stored_email, email_mismatch,
        account_expired, unauthorized, locked_account, too_many_resets, mail_failed, db_error.
    """
    send_mail = _tok.get_send_mail()
    if not callable(send_mail):
        _warn("password reset enabled but no 'send_mail' function given to secure_server()")
        return {"result": False, "reason": "no_mailer"}
    if user is None or not user.strip():
        return {"result": False, "reason": "empty_input"}

    require_email = not settings.reset_password_username_only()
    if require_email and (email is None or not email.strip()):
        return {"result": False, "reason": "empty_input"}

    user = user.strip()
    email = "" if email is None else email.strip()
    email_col = settings.get_email_column()

    kind, handle = _active()
    if kind is None:
        _warn("password reset needs a SQLite or SQL database")
        return {"result": False, "reason": "backend"}
    users = _read(kind, handle, "credentials")
    # controle avant l'utilisateur : meme reponse pour un compte connu ou inconnu
    if kind == "sql":
        has_email_col = email_col in db_sql.column_types(handle, "credentials")
    else:
        has_email_col = any(email_col in r for r in users)
    if not has_email_col:
        _warn(f"password reset needs a '{email_col}' column in the credentials")
        return {"result": False, "reason": "no_email_column"}
    matches = [r for r in users if r.get("user") == user]
    if len(matches) != 1:
        return {"result": False, "reason": "unknown_user"}
    user_info = matches[0]
    stored_email = user_info.get(email_col)
    reason = email_reset_reason(stored_email, email, require_email)
    if reason != "ok":
        return {"result": False, "reason": reason}
    reason = account_reset_reason(user, user_info)
    if reason != "ok":
        return {"result": False, "reason": reason}
    # controle avant l'envoi du mail, le mot de passe ne pourrait pas etre enregistre
    try:
        check_reset_pwd_mngt(user)
    except Exception as exc:
        _warn(f"unable to reset the password | error: {exc}")
        return {"result": False, "reason": "db_error"}

    temp_pwd = generate_pwd()
    assert isinstance(temp_pwd, str)
    # mail d'abord : le nouveau mot de passe n'est ecrit que si l'envoi a reussi
    try:
        send_mail(user, str(stored_email).strip(), temp_pwd)
    except Exception as exc:
        _warn(f"unable to send the reset password email | error: {exc}")
        return {"result": False, "reason": "mail_failed"}

    try:
        if kind == "sqlite":
            _save_reset_pwd_sqlite(handle, user, temp_pwd, user_info, email_col)
        else:
            _save_reset_pwd_sql(handle, user, temp_pwd, user_info, email_col)
    except Exception as exc:
        _warn(f"unable to save the reset password | error: {exc}")
        return {"result": False, "reason": "db_error"}
    return {"result": True, "reason": "success"}


def handle_reset_request(user: str | None, email: str | None = None) -> dict[str, Any]:
    """Demande de reset du visiteur : delai serveur, reset, journalisation (module-auth.R).

    Un utilisateur qui vient de recevoir un mail n'en recoit pas d'autre avant
    `reset_password_cooldown` secondes (raison "too_soon", rien n'est envoye). Une erreur
    inattendue (base injoignable, dechiffrement) devient "db_error" avec un warning, sans faire
    planter l'appelant. Une saisie vide n'est ni traitee ni journalisee.
    """
    if user is None or not user.strip():
        return {"result": False, "reason": "empty_input"}
    reset_user = user.strip()
    if not _tok.claim_reset(reset_user, settings.get_reset_password_cooldown()):
        res = {"result": False, "reason": "too_soon"}
    else:
        try:
            res = reset_pwd_user_email(user, email)
        except Exception as exc:
            _warn(f"password reset failed | error: {exc}")
            res = {"result": False, "reason": "db_error"}
        if not res["result"]:
            _tok.release_reset(reset_user)
    save_reset_logs(user, res["reason"])
    return res


def reset_result_code(reason: str) -> str:
    """Message a montrer au visiteur : "blocked" | "db_error" | "mail_error" | "sent"."""
    if reason in RESET_REASON_BLOCKED:
        return "blocked"
    if reason == "db_error":
        return "db_error"
    if reason in RESET_REASON_ERROR:
        return "mail_error"
    return "sent"


#: Code de resultat -> (cle i18n, classe d'alerte bootstrap).
RESET_MESSAGES: dict[str, tuple[str, str]] = {
    "sent": (
        "If the account exists and an email address is associated with it, an email "
        "containing a temporary password will be sent.",
        "info",
    ),
    "empty": ("Please fill in all fields.", "warning"),
    "blocked": (
        "Password reset is not available for this account, please contact your administrator.",
        "danger",
    ),
    "mail_error": (
        "An error occurred while sending the email. Please try again later or contact your "
        "administrator.",
        "danger",
    ),
    "db_error": (
        "An error occurred while saving the new password. Please try again later or contact "
        "your administrator.",
        "danger",
    ),
}


# Enregistrement du mot de passe temporaire envoye
# --------------------------------------------------
# Tout est ecrit dans UNE transaction : le mot de passe (credentials) et, dans pwd_mngt, ce
# qu'ecriraient force_chg_pwd(user, True), set_temp_pwd_expire() et le compteur de resets. Un
# echec laisse donc l'ancien mot de passe et l'ancien etat (pas de temporaire sans must_change
# ni expiration).


def _check_unchanged(current: dict[str, Any], user_info: dict[str, Any], email_col: str) -> None:
    """Le mot de passe et l'email ne doivent pas avoir change pendant l'envoi du mail."""
    for col in ("password", email_col):
        if str(current.get(col)) != str(user_info.get(col)):
            raise RuntimeError("account modified while sending the email")


def _next_n_user_reset(value: Any) -> int:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 1
    return 1 if math.isnan(n) else int(n) + 1


def _save_reset_pwd_sqlite(
    path: str, user: str, temp_pwd: str, user_info: dict[str, Any], email_col: str
) -> None:
    # hache avant la transaction, pour la garder courte
    hashed = hash_pwd(temp_pwd)
    expire = temp_pwd_expire_value()
    limited = not math.isinf(settings.get_reset_password_max())
    passphrase = _tok.get_passphrase()
    # les deux tables sont relues dans la transaction : elles ont pu changer pendant l'envoi
    with db.transaction(path) as cx:
        users = db.read_db(cx, "credentials", passphrase)
        matches = [r for r in users if r.get("user") == user]
        if len(matches) != 1:
            raise RuntimeError("user not found in credentials")
        _check_unchanged(matches[0], user_info, email_col)
        matches[0]["password"] = hashed
        matches[0]["is_hashed_password"] = "TRUE"

        pwd = db.read_db(cx, "pwd_mngt", passphrase)
        rows = [r for r in pwd if r.get("user") == user]
        # sans sa ligne pwd_mngt, le mot de passe temporaire ne serait pas temporaire
        if len(rows) != 1:
            raise RuntimeError("user not found in pwd_mngt")
        for r in pwd:
            r.setdefault("n_wrong_pwd", 0)
        rows[0]["must_change"] = "TRUE"
        rows[0]["n_wrong_pwd"] = 0
        # set_temp_pwd_expire() : la colonne n'est creee que pour stocker une valeur
        if expire and not any(TEMP_PWD_EXPIRE in r for r in pwd):
            for r in pwd:
                r[TEMP_PWD_EXPIRE] = ""
        if any(TEMP_PWD_EXPIRE in r for r in pwd):
            rows[0][TEMP_PWD_EXPIRE] = expire
        # compteur de resets, seulement si une limite est posee
        if limited:
            for r in pwd:
                r.setdefault(N_USER_RESET, 0)
            rows[0][N_USER_RESET] = _next_n_user_reset(rows[0].get(N_USER_RESET))

        db.write_in_transaction(cx, users, "credentials", passphrase)
        db.write_in_transaction(cx, pwd, "pwd_mngt", passphrase)


def _save_reset_pwd_sql(
    conf: dict[str, Any], user: str, temp_pwd: str, user_info: dict[str, Any], email_col: str
) -> None:
    hashed = hash_pwd(temp_pwd)
    expire = temp_pwd_expire_value()
    rows = read_pwd_mngt_user(user)
    # sans sa ligne pwd_mngt, le mot de passe temporaire ne serait pas temporaire
    if len(rows) != 1:
        raise RuntimeError("user not found in pwd_mngt")
    columns = db_sql.column_types(conf, "pwd_mngt")
    values: dict[str, Any] = {"must_change": "TRUE"}
    if "n_wrong_pwd" in columns:
        values["n_wrong_pwd"] = 0
    if TEMP_PWD_EXPIRE in columns:
        values[TEMP_PWD_EXPIRE] = expire
    if not math.isinf(settings.get_reset_password_max()) and N_USER_RESET in columns:
        values[N_USER_RESET] = _next_n_user_reset(rows[0].get(N_USER_RESET))

    engine = db_sql.connect_sql_db(conf)
    with engine.begin() as cx:
        cred = db_sql.table(conf, "credentials", cx)
        current = [
            dict(r) for r in cx.execute(select(cred).where(cred.c["user"] == user)).mappings()
        ]
        if len(current) != 1:
            raise RuntimeError("user not found in credentials")
        _check_unchanged(current[0], user_info, email_col)
        cx.execute(update(cred).where(cred.c["user"] == user).values(password=hashed))
        pwd = db_sql.table(conf, "pwd_mngt", cx)
        cx.execute(update(pwd).where(pwd.c["user"] == user).values(**values))


# Disponibilite du lien "Forgot password?" (port de reset-password.R)
# ------------------------------------------------------------------
#: Raisons deja signalees : un warning par process et par raison (comme le source).
_reset_link_warned: set[str] = set()


def reset_link_unavailable_reason() -> str | None:
    """Pourquoi le reset ne peut pas marcher avec la configuration courante, None s'il peut.

    Port de `reset_link_unavailable_reason()` : pas de `send_mail`, pas de base SQLite / SQL,
    ou pas de colonne email dans les credentials. Si les colonnes ne peuvent pas etre lues, le
    lien est garde (l'erreur se verra au login / au reset), comme le source.
    """
    if not callable(_tok.get_send_mail()):
        return "no 'send_mail' function given to secure_server()"
    kind, handle = _active()
    if kind is None:
        return "a SQLite or SQL database is needed (credentials given in memory)"
    email_col = settings.get_email_column()
    try:
        if kind == "sqlite":
            conn, must_close = db._connect(handle)
            try:
                cols = db._read_table(conn, "credentials", _tok.get_passphrase())[0]
            finally:
                if must_close:
                    conn.close()
        else:
            cols = list(db_sql.column_types(handle, "credentials"))
    except Exception:
        return None
    if email_col not in cols:
        return f"no '{email_col}' column in the credentials (see option 'email_column')"
    return None


def warn_reset_link_hidden(reason: str) -> None:
    """Warning (une fois par process et par raison) quand le lien est masque."""
    if reason not in _reset_link_warned:
        _reset_link_warned.add(reason)
        warnings.warn(
            "shinymanager: option 'reset_password' is set but the \"Forgot password?\" "
            f"link is hidden: {reason}",
            stacklevel=2,
        )
