"""Store de tokens d'authentification en memoire (source R/tokens.R, singleton `.tok`).

Module 2 du plan. Fidelite PORTAGE_1_1. Le store est un objet a etat de processus (comme le
singleton R6 `.tok`). Limite multi-workers documentee (plan §5, zone de flou tokens ZF-3) :
l'implementation par defaut reste en memoire de processus, sans sur-conception.

Quirks du source reproduits (decision "source fait foi") :
  - `is_valid` a une semantique anti-rejeu a usage unique (ZF-1) : valide au premier appel,
    False ensuite jusqu'a `reset_count`. Ce mecanisme est lie au token dans l'URL ; son usage
    reel sous transport par cookie (D3) sera tranche au module 11 (secure_app).
  - `is_admin` suit la coercition R `as.logical` (ZF via idiomes "Vecteurs et types") : seules
    les chaines "T"/"TRUE"/"True"/"true" (ou le booleen True) sont admin ; tout le reste, y
    compris "FALSE", "1", "yes", None, est non-admin.

ECART ASSUME (decision DT1, gate module 2) : `remove` purge desormais TOUT (tokens, infos
utilisateur, compteur), la ou le source ne retirait que la liste des tokens valides (fuite
memoire + retention d'infos apres logout, ZF-2). get()/get_user() renvoient donc None apres
remove, contrairement au source.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any

# Chaines que R `as.logical` interprete comme TRUE / FALSE ; toute autre valeur -> NA.
_R_TRUE = {"T", "TRUE", "True", "true"}
_R_FALSE = {"F", "FALSE", "False", "false"}


def _r_as_logical_is_true(value: Any) -> bool:
    """Reproduit `isTRUE(as.logical(value))` de R (piege : bool('FALSE') vaut True en Python)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value in _R_TRUE
    return False


class TokenStore:
    """Registre des tokens actifs et de la configuration globale (equivalent R6 `.tokens`)."""

    def __init__(self) -> None:
        """Initialise un store vide (timeout desactive, config non renseignee)."""
        self._tokens: list[str] = []
        self._tokens_count: list[str] = []
        self._tokens_user: dict[str, dict[str, Any]] = {}
        self._sqlite_path: str | None = None
        self._sql_config_db: Any = None
        self._passphrase: str | None = None
        self._timeout: float = 0
        self._send_mail: Any = None

    def generate(self, user: str) -> str:
        """Genere un token opaque (HMAC-SHA256 de user+horodatage, cle aleatoire jetee)."""
        key = secrets.token_bytes(32)
        msg = f"{user}{time.time()}".encode()
        return hmac.new(key, msg, hashlib.sha256).hexdigest()

    def add(self, token: str, user_info: dict[str, Any] | None = None) -> None:
        """Enregistre un token et ses infos utilisateur (horodatage d'activite injecte)."""
        info = dict(user_info) if user_info else {}
        info["shinymanager_datetime"] = time.time()
        if token not in self._tokens:
            self._tokens.append(token)
        self._tokens_user[token] = info

    def is_valid(self, token: str | None) -> bool:
        """Valide un token AVEC anti-rejeu : True au premier appel, False ensuite (ZF-1)."""
        valid = token in self._tokens
        count = self._tokens_count.count(token)
        self._tokens_count.append(token)
        return valid and count < 1

    def is_valid_server(self, token: str | None) -> bool:
        """Valide un token SANS compteur (simple appartenance au registre)."""
        return token in self._tokens

    def is_valid_timeout(self, token: str, update: bool = True) -> bool:
        """Indique si le token n'a pas expire par inactivite ; rafraichit l'horodatage si valide.

        Timeout <= 0 desactive l'expiration. Un token inconnu (pas d'horodatage) est considere
        valide par ce chemin (ZF-4, comportement source).
        """
        info = self._tokens_user.get(token)
        datetime = info.get("shinymanager_datetime") if info else None
        if datetime is not None and self._timeout > 0:
            valid = (time.time() - datetime) <= self._timeout * 60
        else:
            valid = True
        if valid and update and info is not None:
            info["shinymanager_datetime"] = time.time()
        return valid

    def reset_count(self, token: str) -> None:
        """Retire toutes les occurrences du token du compteur (redonne un credit is_valid)."""
        self._tokens_count = [t for t in self._tokens_count if t != token]

    def get(self, token: str) -> dict[str, Any] | None:
        """Retourne les infos utilisateur sans l'horodatage, ou None si le token est inconnu."""
        info = self._tokens_user.get(token)
        if info is None:
            return None
        return {k: v for k, v in info.items() if k != "shinymanager_datetime"}

    def get_user(self, token: str) -> Any:
        """Retourne le champ `user` des infos du token, ou None."""
        info = self._tokens_user.get(token)
        return info.get("user") if info else None

    def is_admin(self, token: str) -> bool:
        """Indique si le token est admin (coercition R as.logical du champ `admin`)."""
        info = self._tokens_user.get(token)
        return _r_as_logical_is_true(info.get("admin")) if info else False

    def remove(self, token: str) -> None:
        """Invalide un token et purge ses infos (ECART ASSUME vs source, DT1/ZF-2)."""
        if not self._tokens:
            return
        self._tokens = [t for t in self._tokens if t != token]
        self._tokens_user.pop(token, None)
        self._tokens_count = [t for t in self._tokens_count if t != token]

    def set_sqlite_path(self, path: str) -> None:
        """Enregistre le chemin de la base SQLite de credentials."""
        self._sqlite_path = path

    def get_sqlite_path(self) -> str | None:
        """Retourne le chemin de la base SQLite."""
        return self._sqlite_path

    def set_sql_config_db(self, config: Any) -> None:
        """Enregistre la configuration du backend SQL."""
        self._sql_config_db = config

    def get_sql_config_db(self) -> Any:
        """Retourne la configuration du backend SQL."""
        return self._sql_config_db

    def set_passphrase(self, passphrase: str) -> None:
        """Enregistre la passphrase de la base."""
        self._passphrase = passphrase

    def get_passphrase(self) -> str | None:
        """Retourne la passphrase de la base."""
        return self._passphrase

    def set_timeout(self, timeout: float) -> None:
        """Enregistre le timeout d'inactivite (en minutes ; <= 0 desactive)."""
        self._timeout = timeout

    def get_timeout(self) -> float:
        """Retourne le timeout d'inactivite courant."""
        return self._timeout

    def set_send_mail(self, send_mail: Any) -> None:
        """Enregistre la fonction d'envoi de mail du reset (1.1.1.1 ; None = pas d'envoi)."""
        self._send_mail = send_mail

    def get_send_mail(self) -> Any:
        """Retourne la fonction d'envoi de mail configuree (ou None)."""
        return self._send_mail


#: Instance de processus (equivalent du singleton `.tok` de R).
_tok = TokenStore()
