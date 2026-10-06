"""Reinitialisation de mot de passe en libre-service (source R/reset-password.R, 1.1.1.1).

Deux modes, selon l'option `reset_password` (module settings) :
  - True       : l'utilisateur fournit son nom ET l'email associe (verifie contre credentials) ;
  - "username" : l'utilisateur ne fournit que son nom ; le mot de passe temporaire part vers
                 l'email stocke pour lui (s'il y en a un).

Un mot de passe temporaire est genere puis envoye par la fonction `send_mail` configuree dans
`secure_server`. MAIL D'ABORD : le nouveau mot de passe n'est ecrit que si l'envoi a reussi (un
serveur mail en panne ne verrouille jamais l'utilisateur). L'utilisateur doit ensuite le changer
au prochain login (must_change), et le mot de passe peut avoir une validite limitee
(`reset_password_validity`, cf. pwd_lifecycle.set_temp_pwd_expire).

Backends SQLite et SQL seulement (le backend data.frame n'est pas persistant). Le `reason`
retourne sert UNIQUEMENT a la journalisation cote admin (`save_reset_logs`) : il ne doit jamais
etre montre au visiteur, qui voit toujours le meme message generique (anti-enumeration).

Quirk du source reproduit : l'ordre des controles differe entre backends (SQLite teste la
colonne email avant l'utilisateur, SQL l'utilisateur avant la colonne).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from shinymanager import db, db_sql, settings
from shinymanager.logs import save_logs_failed
from shinymanager.passwords import generate_pwd, hash_pwd
from shinymanager.pwd_lifecycle import _active, _read, force_chg_pwd, set_temp_pwd_expire
from shinymanager.tokens import _tok

#: Code interne -> statut ecrit dans les logs admin. Les autres codes (config, saisie vide,
#: echec d'envoi, erreur base) ne sont pas journalises.
_RESET_LOG_STATUS: dict[str, str] = {
    "success": "Reset password",
    "unknown_user": "Reset password: unknown user",
    "email_mismatch": "Reset password: wrong email",
    "no_stored_email": "Reset password: no email",
}


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


def _send(send_mail: Any, user: str, stored_email: Any, temp_pwd: str) -> bool:
    """Appelle `send_mail` ; toute exception = echec d'envoi (le mot de passe n'est pas change)."""
    try:
        send_mail(user, str(stored_email), temp_pwd)
    except Exception:
        return False
    return True


def reset_pwd_user_email(user: str | None, email: str | None = None) -> dict[str, Any]:
    """Reinitialise le mot de passe de `user` et l'envoie par mail.

    Returns:
        ``{"result": bool, "reason": str}`` ; `reason` parmi success, no_mailer, empty_input,
        no_email_column, unknown_user, no_stored_email, email_mismatch, mail_failed, db_error,
        backend.
    """
    send_mail = _tok.get_send_mail()
    if not callable(send_mail):
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
    if kind == "sqlite":
        users = db.read_db(handle, "credentials")
        if not any(email_col in r for r in users):
            return {"result": False, "reason": "no_email_column"}
        matches = [r for r in users if r.get("user") == user]
        if len(matches) != 1:
            return {"result": False, "reason": "unknown_user"}
        stored_email = matches[0].get(email_col)
    elif kind == "sql":
        matches = [r for r in _read(kind, handle, "credentials") if r.get("user") == user]
        if len(matches) != 1:
            return {"result": False, "reason": "unknown_user"}
        if email_col not in matches[0]:
            return {"result": False, "reason": "no_email_column"}
        stored_email = matches[0].get(email_col)
    else:
        return {"result": False, "reason": "backend"}

    reason = email_reset_reason(stored_email, email, require_email)
    if reason != "ok":
        return {"result": False, "reason": reason}

    temp_pwd = generate_pwd()
    assert isinstance(temp_pwd, str)
    # mail d'abord : le nouveau mot de passe n'est ecrit que si l'envoi a reussi
    if not _send(send_mail, user, stored_email, temp_pwd):
        return {"result": False, "reason": "mail_failed"}

    try:
        if kind == "sqlite":
            # Relecture juste avant l'ecriture : la table a pu changer pendant l'envoi du mail
            # (le reset tourne dans un thread, cf. /sm-reset-password) ; reecrire la lecture
            # d'avant le mail ecraserait ces changements.
            users = db.read_db(handle, "credentials")
            for row in users:
                if row.get("user") == user:
                    row["password"] = temp_pwd
                    row["is_hashed_password"] = "FALSE"  # write_db re-hachera
            db.write_db(handle, users, "credentials")
        else:
            db_sql.update_sql_db(
                handle, "credentials", {"password": hash_pwd(temp_pwd)}, "user", user
            )
        force_chg_pwd(user, True)
        # validite limitee du mot de passe envoye (remplace aussi une expiration precedente)
        set_temp_pwd_expire(user, temp_pwd_expire_value())
    except Exception:
        return {"result": False, "reason": "db_error"}
    return {"result": True, "reason": "success"}
