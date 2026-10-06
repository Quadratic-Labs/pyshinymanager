"""Primitives de mots de passe : generation, validation, hachage scrypt (source R/utils.R).

Module 3 du plan (reduit aux primitives pures ; la logique de cycle de vie dependante du backend
- check_new_pwd, update_pwd, verrouillage - est reportee a un module post-persistance, cf. gate).

Compatibilite scrypt (D4) : le package R `scrypt` produit le format standard de C. Percival
(magic "scrypt", logN/r/p, salt, checksum, HMAC ; 96 octets base64). Ce module lit et ecrit ce
MEME format via la stdlib (hashlib.scrypt + hmac), sans dependance tierce : un hash produit par
R est verifiable ici, et reciproquement. Le repli "re-hash au premier login" (D4) n'est donc pas
necessaire.

Fidelite PORTAGE_1_1 pour generate_pwd et validate_pwd. Quirk reproduit (ZF-5) : generate_pwd ne
garantit pas de satisfaire validate_pwd (mot de passe temporaire, must_change).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

# Parametres scrypt du package R (releves sur la sortie de scrypt::hashPassword).
_LOGN = 18
_R = 8
_P = 1
_MAGIC = b"scrypt"
# N=2^18, r=8 -> ~256 Mo ; laisser de la marge a hashlib.scrypt.
_MAXMEM = 1024**3

# Regex de la politique par defaut (source utils.R:600), toutes requises.
_PWD_RULES = (re.compile("[0-9]+"), re.compile("[a-z]+"), re.compile("[A-Z]+"), re.compile(".{6,}"))


def generate_pwd(n: int = 1) -> str | list[str]:
    """Genere `n` mot(s) de passe aleatoire(s) : base64 de 6 octets (8 caracteres).

    Args:
        n: nombre de mots de passe (defaut 1).

    Returns:
        Une chaine si `n == 1`, sinon une liste de `n` chaines (comme le vecteur R).
    """
    pwds = [base64.b64encode(secrets.token_bytes(6)).decode("ascii") for _ in range(n)]
    return pwds[0] if n == 1 else pwds


def validate_pwd(pwd: str) -> bool:
    """Valide un mot de passe : >= 1 chiffre, 1 minuscule, 1 majuscule et >= 6 caracteres."""
    return all(rule.search(pwd) is not None for rule in _PWD_RULES)


def hash_pwd(pwd: str) -> str:
    """Hache un mot de passe au format scrypt de C. Percival (compatible R `scrypt`).

    Args:
        pwd: mot de passe en clair.

    Returns:
        Le hash encode en base64 (96 octets), lisible par R `scrypt::verifyPassword`.
    """
    salt = secrets.token_bytes(32)
    params = bytes([_LOGN]) + _R.to_bytes(4, "big") + _P.to_bytes(4, "big")
    header = _MAGIC + b"\x00" + params + salt
    checksum = hashlib.sha256(header).digest()[:16]
    block = header + checksum
    dk = hashlib.scrypt(pwd.encode(), salt=salt, n=1 << _LOGN, r=_R, p=_P, dklen=64, maxmem=_MAXMEM)
    signature = hmac.new(dk[32:64], block, hashlib.sha256).digest()
    return base64.b64encode(block + signature).decode("ascii")


def verify_pwd(hashed: str, pwd: str) -> bool:
    """Verifie un mot de passe contre un hash scrypt (format C. Percival, produit par R ou ici).

    Args:
        hashed: hash base64 (comme produit par `hash_pwd` ou R `scrypt::hashPassword`).
        pwd: mot de passe en clair a verifier.

    Returns:
        True si le mot de passe correspond, False sinon (y compris hash malforme).
    """
    try:
        raw = base64.b64decode(hashed)
    except (ValueError, TypeError):
        return False
    if len(raw) != 96 or raw[:6] != _MAGIC:
        return False
    log_n = raw[7]
    r = int.from_bytes(raw[8:12], "big")
    p = int.from_bytes(raw[12:16], "big")
    salt = raw[16:48]
    block = raw[:64]
    dk = hashlib.scrypt(pwd.encode(), salt=salt, n=1 << log_n, r=r, p=p, dklen=64, maxmem=_MAXMEM)
    expected = hmac.new(dk[32:64], block, hashlib.sha256).digest()
    return hmac.compare_digest(expected, raw[64:96])
