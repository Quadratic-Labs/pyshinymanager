"""Journalisation des connexions (source R/utils.R : save_logs / save_logs_failed / logout_logs).

Module 7 du plan (partie logique). Orchestre les backends SQLite (module db, lecture-
modification-ecriture) et SQL (module db_sql, append + update) via `_tok` et les options
(module settings). La vue graphique des logs (modules-logs.R, billboarder) releve de la couche
UI (approche legere DU1) et est traitee avec l'admin.

Quirks reproduits (source fait foi) :
  - `save_logs` (succes) et `logout_logs` sont gardes par l'option write_logs ; `save_logs_failed`
    garde aussi l'insertion du log par write_logs dans LES DEUX backends (ECART ASSUME, gate
    module 7 : alignement de SQLite sur SQL vs l'asymetrie ZF-1 du source), l'increment
    n_wrong_pwd ayant TOUJOURS lieu (le verrouillage doit marcher meme sans logs) ;
  - `save_logs` est idempotent par (user, jour, token) et remet n_wrong_pwd a 0 ;
  - `save_logs_failed(status="Wrong pwd")` incremente n_wrong_pwd (verrouillage progressif).
Les erreurs de base sont degradees en avertissement (comme le try(silent=TRUE) du source).
"""

from __future__ import annotations

import warnings
from datetime import datetime
from typing import Any

from shinymanager import db, db_sql, settings
from shinymanager.tokens import _tok


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _active() -> tuple[str | None, Any]:
    path = _tok.get_sqlite_path()
    if path is not None:
        return "sqlite", path
    conf = _tok.get_sql_config_db()
    if conf is not None:
        return "sql", conf
    return None, None


def _warn(exc: Exception) -> None:
    warnings.warn(f"shinymanager: unable to save logs | error: {exc}", stacklevel=2)


def _reset_wrong_pwd(kind: str, handle: Any, user: str) -> None:
    if kind == "sqlite":
        pwd = db.read_db(handle, "pwd_mngt")
        for row in pwd:
            if row.get("user") == user:
                row["n_wrong_pwd"] = 0
        db.write_db(handle, pwd, "pwd_mngt")
    else:
        db_sql.update_sql_db(handle, "pwd_mngt", {"n_wrong_pwd": 0}, "user", user)


def save_logs(token: str) -> None:
    """Enregistre une connexion reussie (idempotente par user/jour/token), remet n_wrong_pwd a 0."""
    if not settings.get_option("write_logs"):
        return
    kind, handle = _active()
    if kind is None:
        return
    user = _tok.get_user(token)
    now = _now()
    app = settings.get_option("application")
    try:
        if kind == "sqlite":
            logs = db.read_db(handle, "logs")
            already = any(
                r.get("user") == user
                and str(r.get("server_connected") or "")[:10] == now[:10]
                and r.get("token") == token
                for r in logs
            )
            if not already:
                logs.append(
                    {
                        "user": user,
                        "server_connected": now,
                        "token": token,
                        "logout": None,
                        "app": app,
                        "status": "Success",
                    }
                )
                db.write_db(handle, logs, "logs")
                _reset_wrong_pwd(kind, handle, user)
        else:
            logs = db_sql.read_table_sql(handle, "logs")
            if not any(r.get("token") == token for r in logs):
                db_sql.write_sql_db(
                    handle,
                    [
                        {
                            "user": user,
                            "server_connected": now,
                            "token": token,
                            "logout": None,
                            "app": app,
                            "status": "Success",
                        }
                    ],
                    "logs",
                )
                _reset_wrong_pwd(kind, handle, user)
    except Exception as exc:  # degrade en warning comme le source
        _warn(exc)


def save_logs_failed(user: str, status: str = "Failed") -> None:
    """Enregistre un echec de connexion ; incremente n_wrong_pwd si status == 'Wrong pwd'.

    L'insertion du log est gardee par write_logs dans les deux backends (ecart assume vs ZF-1,
    gate module 7) ; l'increment n_wrong_pwd a TOUJOURS lieu (verrouillage sans logs possible).
    """
    kind, handle = _active()
    if kind is None:
        return
    now = _now()
    app = settings.get_option("application")
    row = {
        "user": user,
        "server_connected": now,
        "token": None,
        "logout": None,
        "app": app,
        "status": status,
    }
    write_enabled = settings.get_option("write_logs")
    try:
        if kind == "sqlite":
            if write_enabled:
                logs = db.read_db(handle, "logs")
                logs.append(row)
                db.write_db(handle, logs, "logs")
        elif write_enabled:
            db_sql.write_sql_db(handle, [row], "logs")
        if status == "Wrong pwd":
            _increment_wrong_pwd(kind, handle, user)
    except Exception as exc:
        _warn(exc)


def _increment_wrong_pwd(kind: str, handle: Any, user: str) -> None:
    if kind == "sqlite":
        pwd = db.read_db(handle, "pwd_mngt")
        for row in pwd:
            if row.get("user") == user:
                row["n_wrong_pwd"] = int(row.get("n_wrong_pwd") or 0) + 1
        db.write_db(handle, pwd, "pwd_mngt")
    else:
        pwd = db_sql.read_table_sql(handle, "pwd_mngt")
        current = next((r for r in pwd if r.get("user") == user), None)
        if current is not None:
            new = int(current.get("n_wrong_pwd") or 0) + 1
            db_sql.update_sql_db(handle, "pwd_mngt", {"n_wrong_pwd": new}, "user", user)


def read_logs(
    status: str | None = None, user: str | None = None, keep_untokened: bool = False
) -> list[dict[str, Any]]:
    """Lit les logs du backend actif, optionnellement filtres par status et/ou user.

    Deduplication FIDELE au source, appliquee UNIQUEMENT si des tokens sont dupliques, sur cle
    (user, token, jour de server_connected), 1re occurrence gardee. Le source a DEUX variantes :
      - graphes (modules-logs.R:138-143) : dedup sur TOUTES les lignes (`keep_untokened=False`) ;
      - export CSV (modules-logs.R:299-305) : conserve TOUTES les lignes sans token
        (`is.na(token) | ...`), ne dedupliquant que les tokens non nuls (`keep_untokened=True`).
    Les echecs de login ont `token=None` : sans `keep_untokened`, plusieurs echecs meme
    user/jour seraient fusionnes (sous-comptage dans l'export d'audit).
    """
    kind, handle = _active()
    if kind is None:
        return []
    rows = db.read_db(handle, "logs") if kind == "sqlite" else db_sql.read_table_sql(handle, "logs")
    tokens = [r.get("token") for r in rows]
    if len(tokens) != len(set(tokens)):  # any(duplicated(token)) cote R (NA compris)
        seen_keys: set[tuple] = set()
        deduped: list[dict[str, Any]] = []
        for r in rows:
            token = r.get("token")
            if keep_untokened and token is None:  # is.na(token) -> toujours conservee (CSV)
                deduped.append(r)
                continue
            day = str(r.get("server_connected") or "")[:10]
            key = (r.get("user"), token, day)
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(r)
        rows = deduped
    if status is not None:
        rows = [r for r in rows if r.get("status") == status]
    if user is not None:
        rows = [r for r in rows if r.get("user") == user]
    return rows


def logout_logs(token: str) -> None:
    """Tamponne l'horodatage de deconnexion des lignes de log du token (garde par write_logs)."""
    if not settings.get_option("write_logs"):
        return
    kind, handle = _active()
    if kind is None:
        return
    now = _now()
    try:
        if kind == "sqlite":
            logs = db.read_db(handle, "logs")
            for row in logs:
                if row.get("token") == token:
                    row["logout"] = now
            db.write_db(handle, logs, "logs")
        else:
            db_sql.update_sql_db(handle, "logs", {"logout": now}, "token", token)
    except Exception as exc:
        _warn(exc)
