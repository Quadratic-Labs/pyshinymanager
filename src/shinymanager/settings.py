"""Options globales de shinymanager (source R/utils.R : getOption('shinymanager.*')).

Equivalent Python des 11 options de processus du package R (8 + 3 du reset en 1.1.1.1). Etat
global mutable (comme les options R), lu par les modules pwd_lifecycle, logs et admin.
`application` est resolu paresseusement (defaut = nom du repertoire courant, comme
basename(getwd()) cote R).
"""

from __future__ import annotations

import math
import os
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "write_logs": True,
    "show_logs": True,
    "auto_sql_reader": math.inf,
    "auto_sqlite_reader": 1000,
    "application": None,  # None -> resolu au nom du repertoire courant
    "download": ["db", "logs", "users"],
    "pwd_validity": math.inf,
    "pwd_failure_limit": math.inf,
    # 1.1.1.1 : reinitialisation de mot de passe en libre-service (source R/utils.R).
    "reset_password": False,  # False | True (user + email) | "username" (user seul)
    "email_column": "email",
    "reset_password_validity": None,  # minutes ; None = NA du source (pas d'expiration)
}

_options: dict[str, Any] = dict(_DEFAULTS)


def get_option(name: str) -> Any:
    """Retourne la valeur d'une option shinymanager (defaut du source si non surchargee)."""
    if name == "application" and _options["application"] is None:
        return os.path.basename(os.getcwd())
    return _options[name]


def set_option(name: str, value: Any) -> None:
    """Surcharge une option (etat global de processus, comme options() en R)."""
    if name not in _DEFAULTS:
        raise KeyError(f"Unknown option: {name}")
    _options[name] = value


def reset_options() -> None:
    """Restaure toutes les options a leur valeur par defaut (utile pour les tests)."""
    _options.clear()
    _options.update(_DEFAULTS)


def get_pwd_validity() -> float:
    """Duree de validite d'un mot de passe en jours (defaut inf = jamais expire)."""
    return get_option("pwd_validity")


def get_pwd_failure_limit() -> float:
    """Nombre d'echecs avant verrouillage (defaut inf = jamais verrouille)."""
    return get_option("pwd_failure_limit")


def reset_password_enabled() -> bool:
    """Reset en libre-service actif ? (`isTRUE(opt) || identical(opt, "username")` du source)."""
    opt = get_option("reset_password")
    return opt is True or opt == "username"


def reset_password_username_only() -> bool:
    """True si le formulaire de reset ne demande que le nom d'utilisateur (option "username")."""
    return get_option("reset_password") == "username"


def get_email_column() -> str:
    """Nom de la colonne email des credentials (defaut "email")."""
    return get_option("email_column")


def get_reset_password_validity() -> float | None:
    """Validite en minutes du mot de passe temporaire envoye par mail ; None = pas d'expiration.

    Port de `get_reset_password_validity` : valeur non numerique, absente ou <= 0 -> NA (None).
    Une valeur infinie est aussi traitee comme "pas d'expiration" (cote R, `Sys.time() + Inf`
    donne une date NA, donc jamais expiree).
    """
    try:
        validity = float(get_option("reset_password_validity"))
    except (TypeError, ValueError):
        return None
    if math.isnan(validity) or validity <= 0 or math.isinf(validity):
        return None
    return validity
