"""Fabrique de verification des credentials (source R/check_credentials.R).

Module 6 du plan. Fidelite PORTAGE_1_1 : `check_credentials(db)` retourne une closure
`(user, password) -> dict` dont le comportement depend du type de `db` :
  - liste de dicts / dict de colonnes -> verification en memoire ;
  - chemin ``.sqlite`` -> lecture du backend SQLite (module db) ;
  - chemin ``.yml``/``.yaml`` ou dict de config -> backend SQL (module db_sql, config declarative
    DS1-A ; `!expr` non evalue, D1).
La fabrique renseigne aussi `_tok` (backend actif) pour les modules pwd_lifecycle / logs.

Contrat de retour reproduit a l'identique : ``result``, ``expired``, ``authorized``, ``user_info``.
Ecart assume (decision Jeremy 2026-07-16) : les backends SQLite et SQL RELISENT la base a chaque
appel. Le source lit une fois a la creation de la closure, mais la recree une fois par session
(closure dans la fonction server R) ; notre architecture ASGI (DA1) ne la recree pas -> une lecture
unique casserait le changement de mot de passe (l'ancien resterait valide). Voir migration.md.
Quirk du source reproduit : un utilisateur avec application non autorisee recoit result=False ET
authorized=False.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from shinymanager import db as db_module
from shinymanager import db_sql, settings
from shinymanager.passwords import verify_pwd
from shinymanager.tokens import _tok

_TRUE_STRINGS = {"T", "TRUE", "True", "true"}


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value in _TRUE_STRINGS


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        cols = list(value.keys())
        n = len(next(iter(value.values()))) if value else 0
        return [{c: value[c][i] for c in cols} for i in range(n)]
    return list(value)


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def check_credentials_df(
    user: str, password: str, credentials_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Verifie un couple (user, password) contre une table de credentials en memoire.

    Returns:
        dict avec ``result`` (bool), ``expired`` (bool), ``authorized`` (bool), ``user_info``
        (dict des colonnes hors password/is_hashed_password, ou None si user inconnu).
    """
    columns = {c for row in credentials_rows for c in row}
    row = next((r for r in credentials_rows if r.get("user") == user), None)
    if row is None:
        return {"result": False, "expired": False, "authorized": False, "user_info": None}

    user_info = {k: v for k, v in row.items() if k not in ("password", "is_hashed_password")}
    stored = row.get("password")
    is_hashed = "is_hashed_password" in columns and _is_true(row.get("is_hashed_password"))
    good_password = verify_pwd(str(stored), password) if is_hashed else (stored == password)

    if "expire" in columns or "start" in columns:
        # DEVIATION assumee (Jeremy 2026-07-16) vs le source. Le source, quand start/expire est
        # vide, fixe le defaut (today-1 / today+1) ET le REINJECTE dans user_info
        # (check_credentials.R:131-135) : consequence -> l'app affiche "expire demain" pour un
        # compte SANS expiration, alors que l'admin affiche vide. On calcule le defaut LOCALEMENT
        # pour le controle de validite (inchange), mais on ne le reinjecte PAS : user_info garde
        # la valeur brute de la base (vide reste vide), coherent avec l'admin.
        start = _parse_date(user_info.get("start")) or (date.today() - timedelta(days=1))
        expire = _parse_date(user_info.get("expire")) or (date.today() + timedelta(days=1))
        good_time = start <= date.today() <= expire
    else:
        good_time = True

    authorized = True
    if "applications" in columns:
        appname = settings.get_option("application")
        apps = str(row.get("applications") or "").split(";")
        if appname not in apps:
            good_password = False
            authorized = False

    if good_password and good_time:
        return {"result": True, "expired": False, "authorized": authorized, "user_info": user_info}
    if good_password and not good_time:
        return {"result": False, "expired": True, "authorized": authorized, "user_info": user_info}
    return {"result": False, "expired": False, "authorized": authorized, "user_info": user_info}


def _is_sql_conf(db: Any) -> bool:
    return isinstance(db, dict) and "connection" in db


def check_credentials(db: Any, passphrase: str | None = None) -> Callable[[str, str], dict]:
    """Retourne une fonction `(user, password) -> dict` de verification des credentials.

    Args:
        db: liste de dicts / dict de colonnes, chemin ``.sqlite``, chemin ``.yml`` ou dict de
            config SQL declaratif.
        passphrase: ignore (pas de chiffrement, D7) ; accepte pour compat d'appel.

    Raises:
        ValueError: si `db` n'est pas d'un type reconnu.
    """
    if isinstance(db, str) and db.endswith(".sqlite"):
        _tok.set_sqlite_path(db)
        _tok.set_passphrase(passphrase)
        _tok.set_sql_config_db(None)

        # RELIT A CHAQUE APPEL (comme le backend SQL ci-dessous). Le source R lit la base UNE
        # fois a la creation de la closure, mais il appelle `check_credentials(...)` DANS la
        # fonction server, executee une fois PAR SESSION -> chaque nouvelle connexion relit.
        # Notre architecture (login par route ASGI, DA1) cree la closure une fois par PROCESSUS ;
        # une lecture unique rendrait invisible tout changement de mot de passe jusqu'au
        # redemarrage (l'ancien mdp continuerait a marcher). On relit donc a chaque tentative.
        def _sqlite_check(user: str, password: str) -> dict[str, Any]:
            rows = db_module.read_db(db, "credentials")
            return check_credentials_df(user, password, rows)

        return _sqlite_check

    if (isinstance(db, str) and (db.endswith(".yml") or db.endswith(".yaml"))) or _is_sql_conf(db):
        conf = db_sql.load_config(db)  # chargement YAML unique (plus de duplication)
        _tok.set_sqlite_path(None)
        _tok.set_sql_config_db(conf)

        def _sql_check(user: str, password: str) -> dict[str, Any]:
            rows = db_sql.read_table_sql(conf, "credentials")
            for r in rows:
                r["is_hashed_password"] = "TRUE"  # SQL : mots de passe toujours haches
            return check_credentials_df(user, password, rows)

        return _sql_check

    if isinstance(db, (list, dict)):
        _tok.set_sqlite_path(None)
        _tok.set_sql_config_db(None)
        rows = _rows(db)
        return lambda user, password: check_credentials_df(user, password, rows)

    raise ValueError("'db' must be a list/dict of credentials, a .sqlite path or a .yml SQL config")
