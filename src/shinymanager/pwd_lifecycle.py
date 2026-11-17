"""Cycle de vie des mots de passe (source R/utils.R).

Orchestre les backends SQLite (module db) et
SQL (module db_sql) au-dessus de l'etat de session `_tok` (backend actif) et des options
(module settings). Reproduit les deux branches backend du source.

Deviations assumees (fail-closed sur securite) vs le source fail-open :
  - `check_new_pwd` : sur ERREUR de lecture, rejette le nouveau mot de passe (False) au lieu de
    l'accepter ;
  - `check_locked_account` : sur ERREUR de lecture, considere le compte VERROUILLE (True) au lieu
    de le laisser passer.
Les cas "pas de backend" et "utilisateur absent" gardent le comportement permissif du source
(pas de fonctionnalite de mot de passe sans backend).

`set_temp_pwd_expire` / `is_temp_pwd_expired` (source R/reset-password.R) vivent ici et
non dans `reset_password` : ils sont appeles par `update_pwd` (import circulaire sinon).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any

from shinymanager import db, db_sql, settings
from shinymanager.passwords import hash_pwd, verify_pwd
from shinymanager.tokens import _tok

_TRUE_STRINGS = {"T", "TRUE", "True", "true"}


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == value and value != 0  # as.logical(1) = TRUE ; NaN -> NA
    return isinstance(value, str) and value in _TRUE_STRINGS


def _active() -> tuple[str | None, Any]:
    """Backend actif d'apres `_tok` : ('sqlite', path) | ('sql', conf) | (None, None)."""
    path = _tok.get_sqlite_path()
    if path is not None:
        return "sqlite", path
    conf = _tok.get_sql_config_db()
    if conf is not None:
        return "sql", conf
    return None, None


def _read(kind: str, handle: Any, name: str) -> list[dict[str, Any]]:
    return (
        db.read_db(handle, name, _tok.get_passphrase())
        if kind == "sqlite"
        else db_sql.read_table_sql(handle, name)
    )


def is_force_chg_pwd(token: str) -> bool:
    """Indique si l'utilisateur du token doit changer son mot de passe.

    True si `must_change` est actif, ou si le mot de passe depasse `pwd_validity` jours.
    Fail-open sur date illisible (comportement source, hors deviations fail-closed).
    """
    info = _tok.get(token)
    user = info.get("user") if info else None
    kind, handle = _active()
    if kind is None:
        return False
    rows = _read(kind, handle, "pwd_mngt")
    row = next((r for r in rows if r.get("user") == user), None)
    if row is None:
        return False
    if _is_true(row.get("must_change")):
        return True
    validity = settings.get_pwd_validity()
    if math.isfinite(validity) and row.get("date_change"):
        try:
            changed = date.fromisoformat(str(row["date_change"]))
            return (date.today() - changed).days > validity
        except (ValueError, TypeError):
            return False
    return False


def force_chg_pwd(user: str, change: bool = True) -> None:
    """Positionne `must_change`, remet n_wrong_pwd a 0 ; si change=False, marque have_changed."""
    kind, handle = _active()
    must = "TRUE" if change else "FALSE"
    if kind == "sqlite":
        rows = _read(kind, handle, "pwd_mngt")
        for row in rows:
            if row.get("user") == user:
                row["must_change"] = must
                row["n_wrong_pwd"] = 0
                if not change:
                    row["have_changed"] = "TRUE"
                    row["date_change"] = date.today().isoformat()
        db.write_db(handle, rows, "pwd_mngt", _tok.get_passphrase())
    elif kind == "sql":
        values: dict[str, Any] = {"must_change": must, "n_wrong_pwd": 0}
        if not change:
            values["have_changed"] = "TRUE"
            values["date_change"] = date.today().isoformat()
        db_sql.update_sql_db(handle, "pwd_mngt", values, "user", user)


def update_pwd(user: str, pwd: str) -> dict[str, bool]:
    """Remplace le mot de passe de l'utilisateur (hache) et clot le cycle 'doit changer'.

    Returns:
        ``{"result": True}`` en cas de succes, ``{"result": False}`` sinon (ou sans backend).
    """
    kind, handle = _active()
    if kind is None:
        return {"result": False}
    try:
        if kind == "sqlite":
            rows = _read(kind, handle, "credentials")
            for row in rows:
                if row.get("user") == user:
                    row["password"] = pwd
                    row["is_hashed_password"] = "FALSE"  # write_db re-hachera
            db.write_db(handle, rows, "credentials", _tok.get_passphrase())
        else:
            db_sql.update_sql_db(handle, "credentials", {"password": hash_pwd(pwd)}, "user", user)
        force_chg_pwd(user, False)
        # Le nouveau mot de passe est definitif, l'expiration d'un mot de passe
        # temporaire envoye par mail est effacee.
        set_temp_pwd_expire(user, "")
        return {"result": True}
    except Exception:
        return {"result": False}


def check_new_pwd(user: str, pwd: str) -> bool:
    """Indique si `pwd` est ACCEPTABLE comme nouveau mot de passe (different de l'actuel).

    Fail-closed : sur erreur de lecture, retourne False (rejette). Sans backend : True.
    """
    kind, handle = _active()
    if kind is None:
        return True
    try:
        rows = _read(kind, handle, "credentials")
        row = next((r for r in rows if r.get("user") == user), None)
        if row is None:
            return True
        # Divergence backend (source) : SQLite peut stocker un mot de passe en clair (legacy,
        # is_hashed_password absent/faux) ; le backend SQL hache TOUJOURS (pas de flag persiste).
        if kind == "sqlite" and not _is_true(row.get("is_hashed_password")):
            return row.get("password") != pwd
        return not verify_pwd(str(row["password"]), pwd)
    except Exception:
        return False


def check_locked_account(user: str, pwd_failure_limit: float | None = None) -> bool:
    """Indique si le compte est verrouille (n_wrong_pwd >= limite).

    Fail-closed : sur erreur de lecture, considere le compte verrouille (True). Sans
    backend : False.
    """
    limit = settings.get_pwd_failure_limit() if pwd_failure_limit is None else pwd_failure_limit
    kind, handle = _active()
    if kind is None:
        return False
    try:
        rows = _read(kind, handle, "pwd_mngt")
        row = next((r for r in rows if r.get("user") == user), None)
        if row is None or row.get("n_wrong_pwd") is None:
            return False
        return int(row["n_wrong_pwd"]) >= limit
    except Exception:
        return True


# Expiration du mot de passe temporaire envoye par mail (source R/reset-password.R)
# -----------------------------------------------------------------------------------
# Colonne `temp_pwd_expire` de pwd_mngt, texte "YYYY-MM-DD HH:MM:SS" en UTC ; vide = pas
# d'expiration. SQLite : colonne creee a la volee a la premiere pose (jamais pour un effacement).
# SQL : colonne a ajouter a la main (texte), sinon rien n'est stocke et rien n'expire.

TEMP_PWD_EXPIRE = "temp_pwd_expire"
#: Compteur de resets d'affilee (option `reset_password_max`) ; meme regles que
#: `temp_pwd_expire` : creee a la volee en SQLite, a ajouter a la main (entier) en SQL.
N_USER_RESET = "n_user_reset"


def set_temp_pwd_expire(user: str, expire: str = "") -> bool:
    """Pose (`expire` = "YYYY-MM-DD HH:MM:SS") ou efface (`expire` = "") l'expiration.

    Returns:
        True si une valeur a ete ecrite, False sinon (pas de backend, colonne absente).
    """
    return set_pwd_mngt_optional(user, TEMP_PWD_EXPIRE, expire, empty="")


def set_pwd_mngt_optional(user: str, name: str, value: Any, empty: Any) -> bool:
    """Ecrit une colonne optionnelle de pwd_mngt (`temp_pwd_expire`, `n_user_reset`).

    SQLite : la colonne est creee a la volee (remplie avec `empty`), sauf si `value` vaut `empty`.
    SQL : la colonne n'est jamais creee, rien n'est fait si elle manque.

    Returns:
        True si une valeur a ete ecrite.
    """
    kind, handle = _active()
    if kind == "sqlite":
        rows = _read(kind, handle, "pwd_mngt")
        if not any(name in r for r in rows):
            if value == empty:
                return False
            for row in rows:
                row[name] = empty
        for row in rows:
            if row.get("user") == user:
                row[name] = value
        db.write_db(handle, rows, "pwd_mngt", _tok.get_passphrase())
        return True
    if kind == "sql":
        # colonne optionnelle, jamais creee par shinymanager sur un backend SQL
        if name not in db_sql.column_types(handle, "pwd_mngt"):
            return False
        db_sql.update_sql_db(handle, "pwd_mngt", {name: value}, "user", user)
        return True
    return False


def read_pwd_mngt_user(user: str) -> list[dict[str, Any]]:
    """Ligne(s) de pwd_mngt de `user` (aucune si inconnu ou sans backend)."""
    kind, handle = _active()
    if kind is None:
        return []
    return [r for r in _read(kind, handle, "pwd_mngt") if r.get("user") == user]


def get_n_user_reset(user: str) -> float:
    """Nombre de resets d'affilee de `user` ; 0 sans limite, compte inconnu ou colonne absente."""
    if math.isinf(settings.get_reset_password_max()):
        return 0
    rows = read_pwd_mngt_user(user)
    if not rows or N_USER_RESET not in rows[0]:
        return 0
    try:
        n = float(rows[0][N_USER_RESET])
    except (TypeError, ValueError):
        return 0
    return 0 if math.isnan(n) else n


def reset_login_counters(user: str) -> None:
    """Connexion reussie avec un mot de passe definitif : remet a 0 n_wrong_pwd et n_user_reset.

    N'ecrit que si besoin (appele a chaque chargement de page quand les logs sont desactives).
    """
    rows = read_pwd_mngt_user(user)
    for name in ("n_wrong_pwd", N_USER_RESET):
        if rows and name in rows[0] and not all(r.get(name) == 0 for r in rows):
            set_pwd_mngt_optional(user, name, 0, empty=0)


def _parse_expire(value: Any) -> datetime | None:
    """Lit une expiration stockee (texte UTC attendu, ou timestamp si la colonne SQL est typee)."""
    if isinstance(value, datetime):
        expire = value
    else:
        try:
            expire = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return expire.replace(tzinfo=timezone.utc) if expire.tzinfo is None else expire


def is_temp_pwd_expired(user: str) -> bool:
    """True si l'utilisateur detient un mot de passe temporaire mail dont la validite est passee.

    Colonne absente, valeur vide ou illisible : pas d'expiration (False), ce qui est le cas de
    tout mot de passe genere par un admin.
    """
    kind, handle = _active()
    if kind is None:
        return False
    rows = [r for r in _read(kind, handle, "pwd_mngt") if r.get("user") == user]
    if not rows or TEMP_PWD_EXPIRE not in rows[0]:
        return False
    expire = _parse_expire(rows[0][TEMP_PWD_EXPIRE])
    return expire is not None and datetime.now(timezone.utc) > expire
