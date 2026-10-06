"""Backend de credentials SQL externe (source R/credentials-db-sql.R).

Module 5 du plan. Fidelite REDESIGN (decisions D1, D6, D8, et DS1 du gate module 5) :
  - config **purement declarative** (DS1 option A) : connexion par parametres (driver par nom,
    secrets via ``${ENV_VAR}``), noms de tables ; AUCUN code (`!expr` non porte, D1) et AUCUN
    SQL brut dans la config ;
  - le SQL est **genere par SQLAlchemy** (multi-SGBD), remplacant les templates glue_sql du YAML ;
  - le cache de connexion fragile du source (env de package) est remplace par un **engine
    SQLAlchemy** (pooling integre) ;
  - backend **Spark/Databricks** (D6) : atteint via un **dialecte SQLAlchemy** (driver
    databricks-sql-connector, extra optionnel `databricks`) par la MEME voie generique - pas de
    chemin de code specifique ni de dependance pyspark. Le source avait un chemin sparklyr ad hoc
    (table temporaire + INSERT BY NAME) ; ici c'est juste une URL de connexion. Validation sur un
    endpoint Databricks reel : dette (non testable sans infra).

Representation alignee sur le backend SQLite (module db) : booleens et dates stockes en chaines,
n_wrong_pwd en entier, valeurs manquantes None, pour que la logique aval soit backend-agnostique.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    insert,
    inspect,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from shinymanager.passwords import hash_pwd

_DEFAULT_TABLES = {"credentials": "credentials", "pwd_mngt": "pwd_mngt", "logs": "logs"}
_CRED_COLS = ["user", "password", "start", "expire", "admin"]
_ENV_RE = re.compile(r"^\$\{(\w+)\}$")
_engines: dict[str, Engine] = {}


def _resolve_secret(value: Any) -> Any:
    """Interpole ``${ENV_VAR}`` depuis l'environnement (secrets hors config, esprit D1)."""
    if isinstance(value, str):
        m = _ENV_RE.match(value)
        if m:
            return os.environ.get(m.group(1), "")
    return value


def _build_url(conf: dict[str, Any]) -> str:
    """Construit une URL SQLAlchemy depuis la config declarative."""
    conn = {k: _resolve_secret(v) for k, v in conf["connection"].items()}
    if "url" in conn:
        return str(conn["url"])
    from sqlalchemy import URL

    return URL.create(
        drivername=conn["drivername"],
        username=conn.get("username"),
        password=conn.get("password"),
        host=conn.get("host"),
        port=conn.get("port"),
        database=conn.get("database"),
    ).render_as_string(hide_password=False)


def connect_sql_db(conf: dict[str, Any]) -> Engine:
    """Retourne un engine SQLAlchemy (mis en cache sauf connect_every_request).

    Remplace le cache de connexion global du source par le pooling de SQLAlchemy.
    """
    url = _build_url(conf)
    if conf.get("connect_every_request"):
        return create_engine(url, poolclass=NullPool)
    if url not in _engines:
        _engines[url] = create_engine(url)
    return _engines[url]


def _table_name(conf: dict[str, Any], key: str) -> str:
    return conf.get("tables", {}).get(key, _DEFAULT_TABLES[key])


def _rows(value: Any) -> list[dict[str, Any]]:
    """Normalise une entree (liste de dicts ou dict de colonnes) en liste de dicts."""
    if isinstance(value, dict):
        cols = list(value.keys())
        n = len(next(iter(value.values()))) if value else 0
        return [{c: value[c][i] for c in cols} for i in range(n)]
    return list(value)


def load_config(conf: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Charge une config SQL declarative depuis un chemin YAML, ou la retourne telle quelle.

    Args:
        conf: chemin d'un fichier ``.yml``/``.yaml``, ou config deja chargee.

    Returns:
        La config sous forme de dict.
    """
    if isinstance(conf, (str, Path)):
        return yaml.safe_load(Path(conf).read_text(encoding="utf-8"))
    return conf


def create_sql_db(credentials_data: Any, conf: str | Path | dict[str, Any]) -> bool:
    """Cree/initialise un backend SQL de credentials depuis une config declarative.

    Args:
        credentials_data: liste de dicts (ou dict de colonnes) ; `user` et `password` requis.
        conf: config declarative (connection + tables), ou CHEMIN d'un fichier YAML la contenant
            (le source R n'accepte qu'un `config_path`).

    Returns:
        True.

    Raises:
        ValueError: si `user`/`password` manque ou si un `user` est duplique.
    """
    conf = load_config(conf)
    rows = _rows(credentials_data)
    columns = list({c for row in rows for c in row})
    if not {"user", "password"}.issubset(columns):
        raise ValueError("credentials_data must contains columns: 'user', 'password'")
    users = [row.get("user") for row in rows]
    if len(users) != len(set(users)):
        raise ValueError("Duplicated users in credentials_data")

    engine = connect_sql_db(conf)
    insp = inspect(engine)
    pwd_table = _table_name(conf, "pwd_mngt")
    # Idempotence (ZF-5) : n'inserer les users que si pwd_mngt n'existait pas avant.
    init_user = not insp.has_table(pwd_table)

    extras = [c for c in columns if c not in _CRED_COLS]
    metadata = MetaData()
    cred_tbl = Table(
        _table_name(conf, "credentials"),
        metadata,
        Column("user", String, primary_key=True),
        Column("password", String),
        Column("start", String),
        Column("expire", String),
        Column("admin", String),
        *[Column(c, String) for c in extras],
    )
    pwd_tbl = Table(
        pwd_table,
        metadata,
        Column("user", String, primary_key=True),
        Column("must_change", String),
        Column("have_changed", String),
        Column("date_change", String),
        Column("n_wrong_pwd", Integer),
    )
    Table(
        _table_name(conf, "logs"),
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user", String),
        Column("server_connected", String),
        Column("token", String),
        Column("logout", String),
        Column("status", String),
        Column("app", String),
    )
    metadata.create_all(engine)  # ne recree pas les tables existantes

    if init_user:
        cred_rows = []
        for row in rows:
            r = {c: (None if row.get(c) is None else str(row.get(c))) for c in _CRED_COLS + extras}
            r.setdefault("admin", "False")
            if row.get("admin") is None:
                r["admin"] = "False"
            r["password"] = hash_pwd(str(row["password"]))
            cred_rows.append(r)
        today = date.today().isoformat()
        pwd_rows = [
            {
                "user": row["user"],
                "must_change": "FALSE",
                "have_changed": "FALSE",
                "date_change": today,
                "n_wrong_pwd": 0,
            }
            for row in rows
        ]
        with engine.begin() as cx:
            cx.execute(insert(cred_tbl), cred_rows)
            cx.execute(insert(pwd_tbl), pwd_rows)
    return True


def write_sql_db(conf: dict[str, Any], value: Any, name: str = "credentials") -> None:
    """Ajoute (append) des lignes a une table du backend SQL.

    Pour `credentials`, hache les mots de passe non flagues `is_hashed_password`. La colonne
    is_hashed_password n'est PAS persistee cote SQL (comme le source) : elle est retiree avant
    insertion.
    """
    rows = _rows(value)
    if name == "credentials":
        for row in rows:
            flagged = str(row.get("is_hashed_password", "")).upper() in {"TRUE", "T"}
            if "password" in row and not flagged:
                row["password"] = hash_pwd(str(row["password"]))
        for row in rows:
            row.pop("is_hashed_password", None)

    engine = connect_sql_db(conf)
    metadata = MetaData()
    tbl = Table(_table_name(conf, name), metadata, autoload_with=engine)
    valid_cols = {c.name for c in tbl.columns}
    clean = [{k: v for k, v in row.items() if k in valid_cols} for row in rows]
    with engine.begin() as cx:
        cx.execute(insert(tbl), clean)


def read_table_sql(conf: dict[str, Any], name: str = "credentials") -> list[dict[str, Any]]:
    """Lit toutes les lignes d'une table du backend SQL sous forme de liste de dicts."""
    engine = connect_sql_db(conf)
    metadata = MetaData()
    tbl = Table(_table_name(conf, name), metadata, autoload_with=engine)
    with engine.connect() as cx:
        return [dict(row) for row in cx.execute(select(tbl)).mappings()]


def update_sql_db(
    conf: dict[str, Any], name: str, values: dict[str, Any], where_col: str, where_val: Any
) -> None:
    """Met a jour les lignes d'une table SQL ou `where_col == where_val` (UPDATE cible)."""
    engine = connect_sql_db(conf)
    metadata = MetaData()
    tbl = Table(_table_name(conf, name), metadata, autoload_with=engine)
    with engine.begin() as cx:
        cx.execute(update(tbl).where(tbl.c[where_col] == where_val).values(**values))


def delete_sql_db(conf: dict[str, Any], name: str, where_col: str, where_val: Any) -> None:
    """Supprime les lignes d'une table SQL ou `where_col == where_val`."""
    engine = connect_sql_db(conf)
    metadata = MetaData()
    tbl = Table(_table_name(conf, name), metadata, autoload_with=engine)
    with engine.begin() as cx:
        cx.execute(delete(tbl).where(tbl.c[where_col] == where_val))
