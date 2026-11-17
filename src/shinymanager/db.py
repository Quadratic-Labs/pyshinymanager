"""Backend de credentials SQLite (source R/credentials-db.R).

Format COMPATIBLE avec le package R : une
base creee par R s'ouvre en Python et inversement.
  - Avec `passphrase` : comme `write_db_encrypt` / `read_db_decrypt` du source, chaque table est
    un data.frame R serialise (`serialize()`, cf. module rserialize), chiffre en AES-256-CBC avec
    la cle SHA-256 de la passphrase, et stocke en une ligne (`value`, `iv`). Necessite le paquet
    `cryptography` (extra `shinymanager[encryption]`).
  - Sans `passphrase` : tables SQLite en clair, colonnes reelles (comme le source).
  - Fonctions renommees `write_db` / `read_db` (plus de "encrypt"/"decrypt").

En clair, les colonnes sont creees sans type declare (affinite NONE) : les types Python sont
preserves au round-trip (n_wrong_pwd reste int, une valeur manquante reste None). Une base en
clair ecrite par R stocke les logiques en entiers 0/1 : lus comme tels (as.logical(1) = TRUE).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

from shinymanager.passwords import hash_pwd
from shinymanager.rserialize import serialize_data_frame, unserialize_data_frame

_DEFAULT_COLS = ["user", "password", "start", "expire", "admin"]
# Valeurs que R `as.logical` considere comme TRUE (cf. tokens._r_as_logical_is_true).
_TRUE_STRINGS = {"T", "TRUE", "True", "true"}


def _is_hashed(value: Any) -> bool:
    """Reproduit `as.logical(is_hashed_password)` : True seulement pour True / 'TRUE'..."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value in _TRUE_STRINGS
    if isinstance(value, (int, float)):
        return value == value and value != 0  # base en clair R : logique en 0/1 ; NaN -> NA
    return False


# --- Chiffrement (format du package R : openssl aes_cbc_encrypt, cle sha256(passphrase)) ---


def _cipher(passphrase: str, iv: bytes) -> Any:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover (depend de l'environnement)
        raise ImportError(
            "Encrypted databases (passphrase) require the 'cryptography' package: "
            "pip install 'shinymanager[encryption]'"
        ) from exc
    key = hashlib.sha256(str(passphrase).encode("utf-8")).digest()
    return Cipher(algorithms.AES(key), modes.CBC(iv))


def _encrypt(data: bytes, passphrase: str) -> tuple[bytes, bytes]:
    from cryptography.hazmat.primitives import padding

    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = _cipher(passphrase, iv).encryptor()
    return encryptor.update(padded) + encryptor.finalize(), iv


def _decrypt(data: bytes, iv: bytes, passphrase: str) -> bytes:
    from cryptography.hazmat.primitives import padding

    decryptor = _cipher(passphrase, iv).decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    try:
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError as exc:
        raise ValueError("Cannot decrypt the database: wrong passphrase?") from exc


def _is_encrypted_layout(columns: list[str], rows: list[dict[str, Any]]) -> bool:
    return columns == ["value", "iv"] and len(rows) == 1 and isinstance(rows[0]["value"], bytes)


def _normalize(value: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """Normalise une entree table en (colonnes ordonnees, lignes) ; gere les tables vides.

    Accepte une liste de dicts (lignes) ou un dict de colonnes {col: [valeurs]}.
    """
    if isinstance(value, dict):
        columns = list(value.keys())
        n = len(next(iter(value.values()))) if value else 0
        rows = [{c: value[c][i] for c in columns} for i in range(n)]
        return columns, rows
    rows = list(value)
    columns: list[str] = []
    for row in rows:
        for col in row:
            if col not in columns:
                columns.append(col)
    return columns, rows


def _write_table(
    conn: sqlite3.Connection, name: str, columns: list[str], rows: list[dict[str, Any]]
) -> None:
    """Ecrase la table `name` (overwrite) avec des colonnes non typees et insere les lignes."""
    quoted = ", ".join(f'"{c}"' for c in columns)
    conn.execute(f'DROP TABLE IF EXISTS "{name}"')
    conn.execute(f'CREATE TABLE "{name}" ({quoted})')
    if rows:
        placeholders = ", ".join("?" for _ in columns)
        conn.executemany(
            f'INSERT INTO "{name}" ({quoted}) VALUES ({placeholders})',
            [[row.get(c) for c in columns] for row in rows],
        )


def _read_raw(conn: sqlite3.Connection, name: str) -> tuple[list[str], list[dict[str, Any]]]:
    cursor = conn.execute(f'SELECT * FROM "{name}"')
    cols = [d[0] for d in cursor.description]
    return cols, [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]


def _read_table(
    conn: sqlite3.Connection, name: str, passphrase: str | None
) -> tuple[list[str], list[dict[str, Any]]]:
    """Lit une table (colonnes, lignes), en la dechiffrant si `passphrase` est fournie."""
    columns, rows = _read_raw(conn, name)
    if passphrase is None:
        if _is_encrypted_layout(columns, rows):
            # Le source R renverrait silencieusement le blob ; on prefere un message clair.
            raise ValueError(f"Table '{name}' is encrypted: a passphrase is required")
        # Une base en clair ecrite par R stocke ses numeriques en REAL (0 -> 0.0) : meme
        # conversion que le chemin chiffre (nombre entier -> int).
        for row in rows:
            for col, val in row.items():
                if isinstance(val, float) and val.is_integer():
                    row[col] = int(val)
        return columns, rows
    if columns != ["value", "iv"] or len(rows) != 1:
        raise ValueError(
            f"Table '{name}' is not encrypted (or wrong layout) but a passphrase was given"
        )
    data = _decrypt(bytes(rows[0]["value"]), bytes(rows[0]["iv"]), passphrase)
    return unserialize_data_frame(data)


def _existing_columns(conn: sqlite3.Connection, name: str, passphrase: str | None) -> list[str]:
    """Colonnes de la table deja stockee (pour ne pas les perdre en ecrivant une table vide)."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    if not exists:
        return []
    try:
        return _read_table(conn, name, passphrase)[0]
    except ValueError:
        return []


def _connect(conn: sqlite3.Connection | str) -> tuple[sqlite3.Connection, bool]:
    """Retourne (connexion, doit_fermer) : ouvre le fichier si `conn` est un chemin."""
    if isinstance(conn, str):
        return sqlite3.connect(conn), True
    return conn, False


def create_db(credentials_data: Any, sqlite_path: str, passphrase: str | None = None) -> None:
    """Cree une base SQLite de credentials (tables credentials, pwd_mngt, logs).

    Args:
        credentials_data: liste de dicts (ou dict de colonnes) avec au minimum `user` et
            `password` en clair.
        sqlite_path: chemin du fichier SQLite a creer.
        passphrase: si fournie, tables chiffrees au format du package R (cf. module).

    Raises:
        ValueError: si `user` ou `password` manque, ou si un `user` est duplique.
    """
    columns, rows = _normalize(credentials_data)
    if not {"user", "password"}.issubset(columns):
        raise ValueError("credentials_data must contains columns: 'user', 'password'")
    users = [row.get("user") for row in rows]
    if len(users) != len(set(users)):
        raise ValueError("Duplicated users in credentials_data")

    for row in rows:
        row.setdefault("admin", False)
        row.setdefault("start", None)
        row.setdefault("expire", None)
    extras = [c for c in columns if c not in _DEFAULT_COLS]
    ordered = _DEFAULT_COLS + extras
    # Conversion en character comme create_db R (NA reste None, pas la chaine "NA").
    cred_rows = [
        {c: (None if row.get(c) is None else str(row.get(c))) for c in ordered} for row in rows
    ]

    conn = sqlite3.connect(sqlite_path)
    try:
        write_db(conn, cred_rows, "credentials", passphrase)
        today = date.today().isoformat()
        pwd_rows = [
            {
                "user": row["user"],
                "must_change": "FALSE",
                "have_changed": "FALSE",
                "date_change": today,
                "n_wrong_pwd": 0,
            }
            for row in cred_rows
        ]
        write_db(conn, pwd_rows, "pwd_mngt", passphrase)
        logs_empty = {"user": [], "server_connected": [], "token": [], "logout": [], "app": []}
        write_db(conn, logs_empty, "logs", passphrase)
    finally:
        conn.close()


def write_db(
    conn: sqlite3.Connection | str,
    value: Any,
    name: str = "credentials",
    passphrase: str | None = None,
) -> None:
    """Ecrit (ecrase) une table de la base (ex-`write_db_encrypt`).

    Pour la table `credentials`, hache les mots de passe non encore haches (flag
    `is_hashed_password`) et passe leur flag a "TRUE" (idempotent).

    Args:
        conn: connexion sqlite3 ouverte ou chemin du fichier.
        value: liste de dicts ou dict de colonnes.
        name: nom de la table (defaut "credentials").
        passphrase: si fournie, la table est chiffree au format du package R.
    """
    connection, must_close = _connect(conn)
    try:
        _write(connection, value, name, passphrase)
        connection.commit()
    finally:
        if must_close:
            connection.close()


@contextmanager
def transaction(sqlite_path: str) -> Iterator[sqlite3.Connection]:
    """Connexion en transaction (BEGIN IMMEDIATE) : tout est valide ou rien (rollback).

    Les lectures (`read_db`) et ecritures (`write_in_transaction`) faites sur cette connexion
    voient un etat coherent : le verrou d'ecriture est pris des le debut.
    """
    connection = sqlite3.connect(sqlite_path, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
    finally:
        connection.close()


def write_in_transaction(
    connection: sqlite3.Connection, value: Any, name: str, passphrase: str | None = None
) -> None:
    """Comme `write_db`, sans valider : pour une connexion ouverte par `transaction`."""
    _write(connection, value, name, passphrase)


def _write(connection: sqlite3.Connection, value: Any, name: str, passphrase: str | None) -> None:
    columns, rows = _normalize(value)
    if name == "credentials" and "password" in columns:
        if "is_hashed_password" not in columns:
            columns.append("is_hashed_password")
        for row in rows:
            if not _is_hashed(row.get("is_hashed_password")):
                row["password"] = hash_pwd(str(row["password"]))
                row["is_hashed_password"] = "TRUE"
            else:
                row.setdefault("is_hashed_password", "TRUE")

    if not columns:
        # Liste vide : on garde les colonnes deja stockees (une table R vide les a).
        columns = _existing_columns(connection, name, passphrase)
    if passphrase is None:
        _write_table(connection, name, columns, rows)
    else:
        blob, iv = _encrypt(serialize_data_frame(columns, rows), passphrase)
        connection.execute(f'DROP TABLE IF EXISTS "{name}"')
        connection.execute(f'CREATE TABLE "{name}" ("value" BLOB, "iv" BLOB)')
        connection.execute(f'INSERT INTO "{name}" ("value", "iv") VALUES (?, ?)', (blob, iv))


def read_db(
    conn: sqlite3.Connection | str, name: str = "credentials", passphrase: str | None = None
) -> list[dict[str, Any]]:
    """Lit une table de la base (ex-`read_db_decrypt`).

    Args:
        conn: connexion sqlite3 ouverte ou chemin du fichier.
        name: nom de la table.
        passphrase: passphrase de la base si elle est chiffree (format du package R).

    Returns:
        Les lignes de la table sous forme de liste de dicts.

    Raises:
        ValueError: passphrase manquante pour une table chiffree, ou incorrecte.
    """
    connection, must_close = _connect(conn)
    try:
        return _read_table(connection, name, passphrase)[1]
    finally:
        if must_close:
            connection.close()
