"""Backend de credentials SQLite (source R/credentials-db.R).

Module 4 du plan. Fidelite REDESIGN (decisions D4, D7, D8) :
  - stockage repense : tables SQLite en clair (colonnes reelles, inspectables), la ou le source
    stockait un blob `serialize()` R chiffre AES (illisible hors R) ;
  - pas de chiffrement de table (D7) : la confidentialite repose sur le hash scrypt des mots de
    passe et les permissions fichier ; le parametre `passphrase` est accepte pour compat d'appel
    mais IGNORE (aucun chiffrement) ;
  - fonctions renommees `write_db` / `read_db` (D8, plus de "encrypt"/"decrypt").

Les colonnes SQLite sont creees sans type declare (affinite NONE) : les types Python sont
preserves au round-trip (n_wrong_pwd reste int, une valeur manquante reste None).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from shinymanager.passwords import hash_pwd

_DEFAULT_COLS = ["user", "password", "start", "expire", "admin"]
# Valeurs que R `as.logical` considere comme TRUE (cf. tokens._r_as_logical_is_true).
_TRUE_STRINGS = {"T", "TRUE", "True", "true"}


def _is_hashed(value: Any) -> bool:
    """Reproduit `as.logical(is_hashed_password)` : True seulement pour True / 'TRUE'..."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value in _TRUE_STRINGS
    return False


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
    conn.commit()


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
        passphrase: ignore (pas de chiffrement, D7) ; accepte pour compat d'appel.

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
    """Ecrit une table de la base (ex-`write_db_encrypt`, sans chiffrement, D7/D8).

    Pour la table `credentials`, hache les mots de passe non encore haches (flag
    `is_hashed_password`) et passe leur flag a "TRUE" (idempotent).

    Args:
        conn: connexion sqlite3 ouverte ou chemin du fichier.
        value: liste de dicts ou dict de colonnes.
        name: nom de la table (defaut "credentials").
        passphrase: ignore (D7).
    """
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

    connection, must_close = _connect(conn)
    try:
        _write_table(connection, name, columns, rows)
    finally:
        if must_close:
            connection.close()


def read_db(
    conn: sqlite3.Connection | str, name: str = "credentials", passphrase: str | None = None
) -> list[dict[str, Any]]:
    """Lit une table de la base (ex-`read_db_decrypt`, D8).

    Args:
        conn: connexion sqlite3 ouverte ou chemin du fichier.
        name: nom de la table.
        passphrase: ignore (D7).

    Returns:
        Les lignes de la table sous forme de liste de dicts (types Python preserves).
    """
    connection, must_close = _connect(conn)
    try:
        cursor = connection.execute(f'SELECT * FROM "{name}"')
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]
    finally:
        if must_close:
            connection.close()
