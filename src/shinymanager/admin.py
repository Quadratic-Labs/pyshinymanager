"""Panneau d'administration (sources R/module-admin.R, R/module-edit_user.R, R/shiny-utils.R).

Module 12 du plan. RECONSTRUCTION FIDELE contre la cible de surface
`docs/migration/comprehension/surface/admin-panel.md` : memes ecrans, memes ids d'input, memes
libelles i18n, meme modele d'interaction (actions par ligne + modales), memes gardes.

Ecarts assumes vs le source (registre de fidelite) :
  - DT (DataTables) -> table HTML construite ici. Le modele d'interaction est preserve : UN SEUL
    input par action, valeur = utilisateur de la ligne cliquee (`Shiny.setInputValue` + priorite
    'event', comme `input_btns`). Le pagination/scroll de DT n'est pas porte.
  - selection multiple : le source cree un input par ligne (`input_checkbox_ui`) puis les agrege
    (`input_checkbox`) ; py-shiny ne permet pas d'enumerer les inputs, donc la collecte se fait
    cote client (www/shiny-utils.js) et pousse la LISTE des users coches dans un seul input.
    Valeur observable identique.
  - `conditionalPanel(output$is_sqlite)` -> condition evaluee a la construction de l'UI : le
    backend actif est un etat de processus (`_tok`), lisible depuis l'UI. Resultat identique.
  - `download_users_database` du source lit toujours en SQLite (bug : casse sur backend SQL) ;
    ici la lecture passe par `list_users()` et fonctionne sur les deux backends.
  - la valeur envoyee par les boutons de ligne est echappee (json), le source l'interpole
    brutalement dans du JS (injection possible via un nom d'utilisateur).

Quirks du source REPRODUITS (comportement source fait foi) :
  - `change_selected_allusers` coche TOUT sans basculer, la ou `select_all_users` bascule ;
  - cles i18n absentes du dictionnaire -> texte brut ("Maximum number of users : %s",
    "Fail to update user", titre "Edit user" en dur pour l'edition multiple) ;
  - la faute "succesfully" des messages de creation / reinitialisation est preservee.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import warnings
from datetime import date
from pathlib import Path
from typing import Any

from faicons import icon_svg
from shiny import Inputs, Outputs, Session, module, reactive, render, ui

from shinymanager import db, db_sql, settings
from shinymanager.i18n import Language, use_language
from shinymanager.passwords import generate_pwd, hash_pwd
from shinymanager.pwd_lifecycle import TEMP_PWD_EXPIRE, force_chg_pwd, set_temp_pwd_expire
from shinymanager.tokens import _tok

_TRUE_STRINGS = {"T", "TRUE", "True", "true"}

#: Colonnes jamais exposees dans les tables ni les exports.
_PWD_COLS = ("password", "is_hashed_password")


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
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
    return db.read_db(handle, name) if kind == "sqlite" else db_sql.read_table_sql(handle, name)


def make_title(x: str) -> str:
    """Titre de colonne : underscores en espaces, premiere lettre en majuscule (utils.R)."""
    s = x.replace("_", " ")
    return s[:1].upper() + s[1:]


def _capitalize(x: str) -> str:
    """Equivalent de R.utils::capitalize (premiere lettre seulement)."""
    return x[:1].upper() + x[1:]


def _columns(rows: list[dict[str, Any]]) -> list[str]:
    """Colonnes dans l'ordre d'apparition (equivalent des colonnes du data.frame R)."""
    columns: list[str] = []
    for r in rows:
        for c in r:
            if c not in columns:
                columns.append(c)
    return columns


# --------------------------------------------------------------------------------------------
# Operations (contrat fonctionnel, testees en integration sur les deux backends)
# --------------------------------------------------------------------------------------------


def _new_pwd_row(user: str, must_change: bool = True) -> dict[str, Any]:
    return {
        "user": user,
        "must_change": "TRUE" if must_change else "FALSE",
        "have_changed": "FALSE",
        "date_change": date.today().isoformat(),
        "n_wrong_pwd": 0,
    }


def add_user(user_data: dict[str, Any], must_change: bool = True) -> None:
    """Ajoute un utilisateur (credentials + ligne pwd_mngt) sur le backend actif.

    Le mot de passe fourni en clair est hache a l'ecriture ; une ligne pwd_mngt est creee
    (must_change par defaut True pour un nouvel utilisateur).
    """
    kind, handle = _active()
    if kind is None:
        return
    row = {k: v for k, v in user_data.items() if k != "must_change"}
    row["is_hashed_password"] = "FALSE"
    if kind == "sqlite":
        creds = db.read_db(handle, "credentials")
        creds.append(row)
        db.write_db(handle, creds, "credentials")
        pwd = db.read_db(handle, "pwd_mngt")
        new_pwd = _new_pwd_row(user_data["user"], must_change)
        # colonne ajoutee par le reset en libre-service (1.1.1.1) : vide = pas d'expiration
        if any(TEMP_PWD_EXPIRE in r for r in pwd):
            new_pwd[TEMP_PWD_EXPIRE] = ""
        pwd.append(new_pwd)
        db.write_db(handle, pwd, "pwd_mngt")
    else:
        db_sql.write_sql_db(handle, [row], "credentials")
        db_sql.write_sql_db(handle, [_new_pwd_row(user_data["user"], must_change)], "pwd_mngt")


def apply_edit(values: dict[str, Any], columns: list[str], *, enabled_null: bool) -> dict[str, Any]:
    """Filtre les valeurs saisies dans une modale d'edition (port de `update_user` R).

    Args:
        values: valeurs brutes de la modale (colonne -> valeur).
        columns: colonnes existantes de la table credentials (les autres sont ignorees).
        enabled_null: valeur de la case `_sm_enabled_null`. True (defaut du source) => les
            valeurs vides sont ECRITES ; False => les champs vides sont ignores.

    Returns:
        Les changements a appliquer.
    """
    changes = {k: v for k, v in values.items() if k in columns}
    if not enabled_null:
        # check_isTruthy du source : on ne garde que les valeurs non vides.
        changes = {k: v for k, v in changes.items() if v not in (None, "", [])}
    return changes


def edit_user(user: str, changes: dict[str, Any]) -> None:
    """Modifie les colonnes `changes` de l'utilisateur `user` (hors mot de passe)."""
    kind, handle = _active()
    if kind is None:
        return
    if kind == "sqlite":
        creds = db.read_db(handle, "credentials")
        for r in creds:
            if r.get("user") == user:
                r.update(changes)
        db.write_db(handle, creds, "credentials")
    else:
        db_sql.update_sql_db(handle, "credentials", changes, "user", user)
    # Renommage : pwd_mngt suit l'utilisateur (module-admin.R:512-529).
    new_user = changes.get("user")
    if new_user and new_user != user:
        if kind == "sqlite":
            pwd = db.read_db(handle, "pwd_mngt")
            for r in pwd:
                if r.get("user") == user:
                    r["user"] = new_user
            db.write_db(handle, pwd, "pwd_mngt")
        else:
            db_sql.update_sql_db(handle, "pwd_mngt", {"user": new_user}, "user", user)


def delete_user(user: str) -> None:
    """Supprime un utilisateur de credentials ET de pwd_mngt."""
    delete_users([user])


def delete_users(users: list[str]) -> None:
    """Supprime plusieurs utilisateurs de credentials ET de pwd_mngt (suppression groupee)."""
    kind, handle = _active()
    if kind is None:
        return
    if kind == "sqlite":
        creds = [r for r in db.read_db(handle, "credentials") if r.get("user") not in users]
        db.write_db(handle, creds, "credentials")
        pwd = [r for r in db.read_db(handle, "pwd_mngt") if r.get("user") not in users]
        db.write_db(handle, pwd, "pwd_mngt")
    else:
        for user in users:
            db_sql.delete_sql_db(handle, "credentials", "user", user)
            db_sql.delete_sql_db(handle, "pwd_mngt", "user", user)


def force_change_password(user: str) -> None:
    """Force l'utilisateur a changer son mot de passe au prochain login (must_change=True)."""
    force_chg_pwd(user, True)


def reset_password(user: str) -> str:
    """Reinitialise le mot de passe et force le changement au prochain login.

    Genere un mot de passe temporaire, l'applique (hache), et positionne must_change.

    Returns:
        Le mot de passe en clair genere (a transmettre a l'utilisateur).
    """
    kind, handle = _active()
    new_pwd = generate_pwd()
    assert isinstance(new_pwd, str)
    if kind == "sqlite":
        creds = db.read_db(handle, "credentials")
        for r in creds:
            if r.get("user") == user:
                r["password"] = new_pwd
                r["is_hashed_password"] = "FALSE"
        db.write_db(handle, creds, "credentials")
    elif kind == "sql":
        db_sql.update_sql_db(handle, "credentials", {"password": hash_pwd(new_pwd)}, "user", user)
    force_chg_pwd(user, True)
    # mot de passe genere par un admin : pas d'expiration, annule un mot de passe mail en attente
    set_temp_pwd_expire(user, "")
    return new_pwd


def list_users() -> list[dict[str, Any]]:
    """Retourne les utilisateurs (credentials sans les colonnes de mot de passe)."""
    kind, handle = _active()
    if kind is None:
        return []
    rows = _read(kind, handle, "credentials")
    return [{k: v for k, v in r.items() if k not in _PWD_COLS} for r in rows]


def list_pwds() -> list[dict[str, Any]]:
    """Retourne pwd_mngt sans `n_wrong_pwd` ni `temp_pwd_expire` (module-admin.R:300-305)."""
    kind, handle = _active()
    if kind is None:
        return []
    return [
        {k: v for k, v in r.items() if k not in ("n_wrong_pwd", TEMP_PWD_EXPIRE)}
        for r in _read(kind, handle, "pwd_mngt")
    ]


def users_csv() -> str:
    """Export CSV des utilisateurs (sep ';', NA vide), sans les colonnes de mot de passe."""
    rows = list_users()
    buf = io.StringIO()
    columns = _columns(rows)
    writer = csv.DictWriter(
        buf, fieldnames=columns, delimiter=";", restval="", quoting=csv.QUOTE_NONNUMERIC
    )
    writer.writeheader()
    for r in rows:
        writer.writerow({c: "" if r.get(c) is None else r.get(c) for c in columns})
    return buf.getvalue()


# --------------------------------------------------------------------------------------------
# Briques d'UI (port de input_btns / input_checkbox_ui + table)
# --------------------------------------------------------------------------------------------


def _row_button(input_id: str, user: str, tooltip: str, icon: str, status: str) -> ui.Tag:
    """Bouton d'action d'une ligne (port de `input_btns` : 1 input/action, valeur = user)."""
    return ui.tags.button(
        icon_svg(icon),
        class_=f"btn btn-{status}",
        style="float: right;",
        onclick=f"Shiny.setInputValue('{input_id}', {json.dumps(user)}, {{priority: 'event'}})",
        **{"data-toggle": "tooltip", "data-title": tooltip, "data-container": "body"},
    )


def _row_checkbox(group_id: str, user: str) -> ui.Tag:
    """Case a cocher d'une ligne (port de `input_checkbox_ui`, collecte cote client)."""
    return ui.tags.input(
        type="checkbox",
        class_="sm-row-check",
        style="float: right;",
        **{"data-group": group_id, "data-user": user},
    )


def _sm_table(
    rows: list[dict[str, Any]],
    lan: Language,
    *,
    actions: list[tuple[str, str, str, str, str]],
    group_id: str,
    yes_no_cols: tuple[str, ...],
) -> ui.Tag:
    """Table d'administration : colonnes de donnees + colonnes d'action par ligne.

    Args:
        rows: lignes a afficher.
        lan: jeu de labels.
        actions: (nom de colonne, input_id, cle i18n du tooltip, icone, statut bootstrap).
        group_id: input_id du groupe de selection (cases a cocher).
        yes_no_cols: colonnes booleennes a afficher en Yes/No.
    """
    columns = _columns(rows)
    action_names = [a[0] for a in actions]
    headers = [ui.tags.th(make_title(lan.get(c))) for c in [*columns, *action_names, "Select"]]
    body = []
    for r in rows:
        user = str(r.get("user", ""))
        cells = []
        for c in columns:
            value = r.get(c)
            if c in yes_no_cols:
                value = lan.get("Yes") if _is_true(value) else lan.get("No")
            cells.append(ui.tags.td("" if value is None else str(value)))
        for _name, input_id, tooltip, icon, status in actions:
            button = _row_button(input_id, user, lan.get(tooltip), icon, status)
            cells.append(ui.tags.td(button, width="50px"))
        cells.append(ui.tags.td(_row_checkbox(group_id, user), width="50px"))
        body.append(ui.tags.tr(*cells, id=f"user-row-{user}"))
    return ui.tags.table(
        ui.tags.thead(ui.tags.tr(*headers)),
        ui.tags.tbody(*body),
        class_="table table-condensed table-hover",
    )


def users_table_html(
    users: list[dict[str, Any]], language: str = "en", ns: str = "admin"
) -> ui.Tag:
    """Table des utilisateurs (sans colonnes de mot de passe), actions Edit / Remove / Select."""
    return _sm_table(
        users,
        use_language(language),
        actions=[
            ("Edit", f"{ns}-edit_user", "Edit user", "pen-to-square", "primary"),
            ("Remove", f"{ns}-remove_user", "Delete user", "trash-can", "danger"),
        ],
        group_id=f"{ns}-select_mult_users",
        yes_no_cols=("admin",),
    )


def pwds_table_html(pwds: list[dict[str, Any]], language: str = "en", ns: str = "admin") -> ui.Tag:
    """Table pwd_mngt, actions Change password / Reset password / Select."""
    return _sm_table(
        pwds,
        use_language(language),
        actions=[
            ("Change password", f"{ns}-change_pwd", "Ask to change password", "key", "primary"),
            (
                "Reset password",
                f"{ns}-reset_pwd",
                "Reset password",
                "arrow-rotate-left",
                "warning",
            ),
        ],
        group_id=f"{ns}-change_mult_pwds",
        yes_no_cols=("must_change", "have_changed"),
    )


# --------------------------------------------------------------------------------------------
# Modale d'edition / d'ajout (port de module-edit_user.R)
# --------------------------------------------------------------------------------------------


def edit_user_inputs(
    ns: Any,
    credentials: list[dict[str, Any]],
    username: list[str] | None = None,
    inputs_list: dict[str, Any] | None = None,
    lan: Language | None = None,
) -> list[Any]:
    """Genere un input par colonne de credentials (port de `edit_user_ui`).

    Args:
        ns: fonction de namespace (equivalent de `NS(id)` R).
        credentials: table credentials complete.
        username: utilisateur(s) edite(s) ; None => mode ajout.
        inputs_list: inputs personnalises par colonne ``{col: {"fun": callable, "args": {...}}}``.
        lan: jeu de labels.

    Returns:
        La liste des inputs de la modale.
    """
    if lan is None:
        lan = use_language()
    all_users = [r.get("user") for r in credentials]
    if username and all(u in all_users for u in username):
        data_user = [r for r in credentials if r.get("user") in username]
    else:
        data_user = []
    columns = _columns(credentials)
    multiple = bool(username) and len(username) > 1

    def _unique(col: str) -> Any:
        values = {r.get(col) for r in data_user}
        return values.pop() if len(values) == 1 else None

    inputs: list[Any] = []
    for col in columns:
        if col in ("password", "is_hashed_password"):
            continue  # jamais editables directement
        if col == "user" and multiple:
            continue  # MULTIPLE USERS: dont modify user name
        if col in ("start", "expire"):
            value = _unique(col)
            inputs.append(
                ui.input_date(
                    ns(col),
                    _capitalize(lan.get(col)),
                    # "" et NON None : `value=None` OMET l'attribut data-initial-date, et le
                    # binding shiny met alors la DATE DU JOUR. Le source passe NA -> attribut nu
                    # -> champ VIDE. `value=""` rend data-initial-date="" = meme resultat que R.
                    # Sans ca, tout compte cree via la modale nait avec start=expire=aujourd'hui.
                    value=value if value else "",
                    language=lan.get_dateInput() or "en",
                    width="100%",
                )
            )
        elif col == "admin":
            if multiple:
                continue  # MULTIPLE USERS: dont allow to set all users admin
            inputs.append(
                ui.input_checkbox(
                    ns(col),
                    _capitalize(lan.get(col)),
                    value=bool(data_user) and all(_is_true(r.get(col)) for r in data_user),
                )
            )
        elif inputs_list and col in inputs_list and "fun" in inputs_list[col]:
            inputs.append(_custom_input(ns, col, data_user, inputs_list[col], lan))
        else:
            value = _unique(col)
            inputs.append(
                ui.input_text(
                    ns(col),
                    _capitalize(lan.get(col)),
                    value="" if value is None else str(value),
                    width="100%",
                )
            )

    if username is None:
        # ADD : mot de passe pre-rempli + case "demander le changement".
        inputs.append(
            ui.input_text(ns("password"), lan.get("Password"), value=generate_pwd(), width="100%")
        )
        inputs.append(
            ui.input_checkbox(ns("must_change"), lan.get("Ask to change password"), value=True)
        )
    elif len(username) == 1:
        # EDIT simple : autoriser l'ecriture des valeurs vides.
        inputs.append(
            ui.input_checkbox(ns("_sm_enabled_null"), lan.get("Allowed null values"), value=True)
        )
    return inputs


def _custom_input(
    ns: Any, col: str, data_user: list[dict[str, Any]], spec: dict[str, Any], lan: Language
) -> Any:
    """Construit un input personnalise (`inputs_list`), avec repli textInput comme le source.

    Comme le source (module-edit_user.R:72-115), les arguments sont filtres d'apres la SIGNATURE
    de la fonction : `value`, `selected`, `label` et `width` ne sont poses que si la fonction les
    accepte. Toute erreur retombe sur un `input_text` (equivalent du tryCatch du source).
    """
    import inspect

    fun = spec["fun"]
    args = dict(spec.get("args") or {})
    values = [r.get(col) for r in data_user]
    value = values[0] if len(set(values)) == 1 else None
    try:
        params = set(inspect.signature(fun).parameters)
    except (TypeError, ValueError):  # builtin sans signature introspectable
        params = set()

    args["id"] = ns(col)
    for name, default in (("label", _capitalize(lan.get(col))), ("width", "100%")):
        if name in params:
            args.setdefault(name, default)
        else:
            args.pop(name, None)
    if "value" in params:
        if data_user:
            args["value"] = value
    else:
        args.pop("value", None)
    if "selected" in params:
        if data_user:
            # Multi-valeurs stockees en chaine : re-eclatees sur ';' (source).
            if args.get("multiple") and isinstance(value, str):
                args["selected"] = value.split(";")
            else:
                args["selected"] = value
    else:
        args.pop("selected", None)

    try:
        return fun(**args)
    except Exception:
        warnings.warn(
            f"Error building custom input for column '{col}'. Verify 'inputs_list' argument.",
            stacklevel=2,
        )
        return ui.input_text(
            ns(col),
            _capitalize(lan.get(col)),
            value="" if value is None else str(value),
            width="100%",
        )


# --------------------------------------------------------------------------------------------
# Modales (port de module-admin.R:924-976)
# --------------------------------------------------------------------------------------------


def _spinner_modal() -> ui.Tag:
    """Modale bloquante d'attente pendant une ecriture en base."""
    return ui.modal(
        ui.tags.div(ui.tags.img(src="/shinymanager/1497.gif", style="height:50px"), align="center"),
        title=None,
        footer=None,
        size="s",
        easy_close=False,
    )


def _remove_modal(input_id: str, users: list[str], lan: Language) -> ui.Tag:
    return ui.modal(
        ui.tags.p(
            ui.HTML(
                lan.get("Are you sure to remove user(s): %s from the database ?")
                % f"<b>{', '.join(users)}</b>"
            )
        ),
        fade=False,
        footer=ui.TagList(
            ui.tags.button(
                lan.get("Delete user(s)"),
                id=input_id,
                class_="btn btn-danger action-button",
                **{"data-bs-dismiss": "modal", "data-dismiss": "modal"},
            ),
            ui.modal_button(lan.get("Cancel")),
        ),
    )


def _change_pwd_modal(input_id: str, users: list[str], lan: Language) -> ui.Tag:
    return ui.modal(
        ui.tags.p(
            ui.HTML(
                lan.get("Ask %s to change password on next connection?")
                % f"<b>{', '.join(users)}</b>"
            )
        ),
        title=lan.get("Ask to change password"),
        footer=ui.TagList(
            ui.modal_button(lan.get("Cancel")),
            ui.tags.button(
                lan.get("Confirm"),
                id=input_id,
                class_="btn btn-primary action-button",
                **{"data-bs-dismiss": "modal", "data-dismiss": "modal"},
            ),
        ),
    )


def _reset_pwd_modal(input_id: str, users: list[str], lan: Language) -> ui.Tag:
    return ui.modal(
        ui.tags.p(ui.HTML(lan.get("Reset password for %s?") % f"<b>{', '.join(users)}</b>")),
        title=lan.get("Reset password"),
        footer=ui.TagList(
            ui.modal_button(lan.get("Cancel")),
            ui.tags.button(
                lan.get("Confirm"),
                id=input_id,
                class_="btn btn-primary action-button",
                **{"data-bs-dismiss": "modal", "data-dismiss": "modal"},
            ),
        ),
    )


# --------------------------------------------------------------------------------------------
# UI du panneau (port de admin_ui)
# --------------------------------------------------------------------------------------------


@module.ui
def admin_ui(language: str = "en") -> ui.TagList:
    """UI du panneau admin (port de `admin_ui` R : sections Users et Passwords)."""
    lan = use_language(language)
    download = settings.get_option("download") or []
    kind, _handle = _active()
    return ui.TagList(
        ui.tags.head(
            ui.tags.link(href="/shinymanager/styles-admin.css", rel="stylesheet"),
            ui.tags.script(src="/shinymanager/shiny-utils.js"),
        ),
        ui.tags.div(
            ui.tags.div(
                ui.tags.h3(icon_svg("users"), " " + lan.get("Users"), class_="text-primary"),
                ui.tags.hr(),
                ui.input_action_button(
                    "add_user",
                    ui.TagList(icon_svg("plus"), " " + lan.get("Add a user")),
                    width="100%",
                    class_="btn-primary",
                ),
                ui.tags.br(),
                ui.tags.br(),
                ui.tags.br(),
                ui.output_ui("table_users"),
                ui.tags.br(),
                ui.input_action_button(
                    "select_all_users",
                    icon_svg("square-check"),
                    class_="btn-secondary pull-right",
                    style="margin-left: 5px",
                ),
                ui.input_action_button(
                    "edit_selected_users",
                    ui.TagList(
                        icon_svg("pen-to-square"),
                        " " + lan.get("Edit selected users"),
                    ),
                    class_="btn-primary pull-right disabled",
                    style="margin-left: 5px",
                ),
                ui.input_action_button(
                    "remove_selected_users",
                    ui.TagList(
                        icon_svg("trash-can"),
                        " " + lan.get("Remove selected users"),
                    ),
                    class_="btn-danger pull-right disabled",
                ),
                ui.tags.br(),
                (
                    ui.TagList(
                        ui.tags.br(),
                        ui.tags.br(),
                        ui.tags.br(),
                        ui.download_button(
                            "download_users_database",
                            ui.TagList(
                                icon_svg("download"),
                                " " + lan.get("Download Users file"),
                            ),
                            class_="btn-primary center-block",
                        ),
                        # Espace demande par Jeremy (2026-07-16) entre le bouton de
                        # telechargement et la section Passwords. Le source n'en met pas ->
                        # deviation cosmetique assumee.
                        ui.tags.br(),
                        ui.tags.br(),
                    )
                    if "users" in download
                    else None
                ),
                ui.tags.h3(
                    icon_svg("key"),
                    " " + lan.get("Passwords"),
                    class_="text-primary",
                    style="margin-top: 30px;",
                ),
                ui.tags.hr(),
                ui.output_ui("table_pwds"),
                ui.tags.br(),
                ui.input_action_button(
                    "change_selected_allusers",
                    icon_svg("square-check"),
                    class_="btn-secondary pull-right",
                    style="margin-left: 5px",
                ),
                ui.input_action_button(
                    "change_selected_pwds",
                    ui.TagList(
                        icon_svg("key"),
                        " " + lan.get("Force selected users to change password"),
                    ),
                    class_="btn-primary pull-right disabled",
                ),
                # Le source conditionne ce bouton par un conditionalPanel sur output$is_sqlite ;
                # le backend actif est ici un etat de processus, lisible des la construction.
                (
                    ui.TagList(
                        ui.tags.br(),
                        ui.tags.br(),
                        ui.tags.br(),
                        ui.tags.hr(),
                        ui.download_button(
                            "download_sql_database",
                            ui.TagList(
                                icon_svg("download"),
                                " " + lan.get("Download SQL database"),
                            ),
                            class_="btn-primary center-block",
                        ),
                    )
                    if "db" in download and kind == "sqlite"
                    else None
                ),
                ui.tags.br(),
                ui.tags.br(),
                class_="col-sm-10 offset-sm-1",
            ),
            class_="row",
        ),
    )


# --------------------------------------------------------------------------------------------
# Serveur du panneau (port de `admin`)
# --------------------------------------------------------------------------------------------


@module.server
def admin_server(
    input: Inputs,
    output: Outputs,
    session: Session,
    language: str = "en",
    inputs_list: dict[str, Any] | None = None,
    max_users: int | None = None,
) -> None:  # pragma: no cover
    """Serveur du panneau admin (port de la fonction `admin` de module-admin.R).

    Le cablage reactif est exclu de la couverture (approche DU1) : les operations sous-jacentes
    sont testees en integration, la structure d'UI est verifiee par les tests de structure.
    """
    from shinymanager.secure_app import COOKIE_NAME

    lan = use_language(language)
    ns = session.ns
    # Sous-namespaces des modales, comme les callModule du source : les inputs de la modale
    # d'ajout sont "admin-add_user-<colonne>", etc. Un ResolvedId est deja resolu : on peut donc
    # le passer tel quel a ui.input_* et a input[...] (pas de re-validation, pas de re-prefixage).
    add_ns = ns("add_user")
    edit_ns = ns("edit_user")
    mult_ns = ns("edit_mult_user")

    def html_id(name: str) -> str:
        """Id HTML brut namespace (les ids du source contiennent des TIRETS, que ns() refuse).

        `ResolvedId` valide ses segments (lettres/chiffres/underscore) : il ne peut pas produire
        `admin-placeholder-user-exist`. Ces ids ne sont pas des inputs shiny, seulement des
        ancres pour insert_ui/remove_ui : on les construit donc a la main, a l'identique du source.
        """
        return f"{ns}-{name}"

    # Le cookie est porte par la connexion HTTP, qui n'existe que sur la session RACINE : une
    # session de module (SessionProxy) n'a pas de http_conn. Equivalent du token_start du source.
    root_conn = session.root_scope().http_conn
    token_start = root_conn.cookies.get(COOKIE_NAME) if root_conn else None
    current_user = _tok.get_user(token_start) if token_start else None

    update_read_db = reactive.value(0)

    def _bump() -> None:
        update_read_db.set(update_read_db() + 1)

    @reactive.calc
    def users() -> list[dict[str, Any]]:
        update_read_db()
        return list_users()

    @reactive.calc
    def pwds() -> list[dict[str, Any]]:
        update_read_db()
        return list_pwds()

    # Auto-reader periodique (source : reactiveFileReader sqlite / reactiveTimer SQL,
    # module-admin.R:170-231). Rafraichit users()/pwds() pour supporter plusieurs sessions admin
    # concurrentes. Intervalle selon le backend actif : `auto_sqlite_reader` (defaut 1000 ms) en
    # sqlite, `auto_sql_reader` (defaut Inf = desactive) en SQL.
    #
    # En sqlite, on ne bump QUE sur changement reel de mtime, comme `reactiveFileReader` du source
    # (qui ne se declenche que quand le fichier change). Un bump inconditionnel a chaque tick
    # invaliderait `update_read_db`, donc `_reset_selection_on_redraw`, effacant la selection
    # multiple chaque seconde. En SQL, le source utilise `reactiveTimer` (re-lecture a chaque
    # tick) : on reproduit ce comportement (bump inconditionnel), le defaut Inf le desactivant.
    def _mtime(handle: Any) -> float | None:
        try:
            return os.path.getmtime(handle)
        except OSError:
            return None

    _reader_stamp = [_mtime(_active()[1]) if _active()[0] == "sqlite" else None]

    @reactive.effect
    def _auto_reader() -> None:
        kind, handle = _active()
        interval_ms = settings.get_option(
            "auto_sqlite_reader" if kind == "sqlite" else "auto_sql_reader"
        )
        if not math.isfinite(interval_ms):
            return
        reactive.invalidate_later(interval_ms / 1000)
        if kind == "sqlite":
            stamp = _mtime(handle)
            if stamp is None or stamp == _reader_stamp[0]:
                return
            _reader_stamp[0] = stamp
        with reactive.isolate():
            _bump()

    def _selected(input_id: str) -> list[str]:
        value = input[input_id]() if input_id in input else None
        if not value:
            return []
        return list(value) if isinstance(value, (list, tuple)) else [str(value)]

    async def _toggle(input_id: str, enable: bool) -> None:
        await session.send_custom_message(
            "togglewidget", {"inputId": ns(input_id), "type": "enable" if enable else "disable"}
        )

    # --- Tables -----------------------------------------------------------------------------

    @render.ui
    def table_users() -> Any:
        return users_table_html(users(), language=language, ns=str(ns))

    @render.ui
    def table_pwds() -> Any:
        return pwds_table_html(pwds(), language=language, ns=str(ns))

    # Une table redessinee revient avec toutes ses cases decochees : la selection serveur doit
    # retomber a vide, sinon les actions groupees porteraient sur la selection d'AVANT le
    # redessin. Le source obtient le meme effet en detruisant les inputs de ligne a chaque rendu
    # (input_checkbox_ui -> remove_input/rmInputSM, shiny-utils.R:133-141).
    @reactive.effect
    async def _reset_selection_on_redraw() -> None:
        update_read_db()
        for group in ("select_mult_users", "change_mult_pwds"):
            await session.send_custom_message("smResetSelection", {"group": ns(group)})

    # --- Selection multiple -----------------------------------------------------------------

    @reactive.effect
    @reactive.event(input.select_all_users)
    async def _select_all_users() -> None:
        # Bascule (source) : si tout n'est pas coche -> tout cocher, sinon tout decocher.
        check = len(_selected("select_mult_users")) < len(users())
        await session.send_custom_message(
            "smCheckAll", {"group": ns("select_mult_users"), "value": check}
        )

    @reactive.effect
    @reactive.event(input.change_selected_allusers)
    async def _select_all_pwds() -> None:
        # Quirk du source (module-admin.R:339-347) : coche TOUT, sans bascule.
        await session.send_custom_message(
            "smCheckAll", {"group": ns("change_mult_pwds"), "value": True}
        )

    @reactive.effect
    @reactive.event(input.select_mult_users)
    async def _on_users_selected() -> None:
        selected = _selected("select_mult_users")
        await _toggle("remove_selected_users", len(selected) > 0)
        # DEVIATION assumee vs source (Jeremy 2026-07-16) : le source desactive "edit selected"
        # tant qu'un seul user est coche (on edite un unique via le crayon de ligne). Ici on
        # l'active des 1 selectionne ; l'edition d'un user unique passe alors en mode complet
        # (cf. _edit_selected).
        await _toggle("edit_selected_users", len(selected) > 0)

    @reactive.effect
    @reactive.event(input.change_mult_pwds)
    async def _on_pwds_selected() -> None:
        await _toggle("change_selected_pwds", len(_selected("change_mult_pwds")) > 0)

    # --- Suppression ------------------------------------------------------------------------

    @reactive.effect
    @reactive.event(input.remove_user)
    def _ask_remove_user() -> None:
        user = input.remove_user()
        if current_user is not None and current_user == user:
            ui.modal_show(
                ui.modal(
                    lan.get("You can't remove yourself!"),
                    footer=ui.modal_button(lan.get("Cancel")),
                    easy_close=True,
                )
            )
        else:
            ui.modal_show(_remove_modal(ns("delete_user"), [user], lan))

    @reactive.effect
    @reactive.event(input.delete_user)
    def _delete_user() -> None:
        ui.modal_show(_spinner_modal())
        delete_user(input.remove_user())
        ui.modal_remove()
        _bump()

    @reactive.effect
    @reactive.event(input.remove_selected_users)
    def _ask_remove_selected() -> None:
        selected = _selected("select_mult_users")
        ui.modal_show(_remove_modal(ns("delete_selected_users"), selected, lan))

    @reactive.effect
    @reactive.event(input.delete_selected_users)
    def _delete_selected() -> None:
        ui.modal_show(_spinner_modal())
        delete_users(_selected("select_mult_users"))
        ui.modal_remove()
        _bump()

    # --- Ajout ------------------------------------------------------------------------------

    @reactive.effect
    @reactive.event(input.add_user)
    def _ask_add_user() -> None:
        if max_users is not None and len(users()) >= max_users:
            ui.modal_show(
                ui.modal(
                    # Cle absente du dictionnaire du source (espaces autour du ':') : texte brut.
                    lan.get("Maximum number of users : %s") % max_users,
                    title=lan.get("Too many users"),
                )
            )
            return
        ui.modal_show(
            ui.modal(
                *edit_user_inputs(add_ns, _credentials(), None, inputs_list, lan),
                ui.tags.div(id=html_id("placeholder-user-exist")),
                title=lan.get("Add a user"),
                footer=ui.TagList(
                    ui.modal_button(lan.get("Cancel")),
                    ui.tags.button(
                        lan.get("Confirm new user"),
                        id=ns("added_user"),
                        class_="btn btn-primary action-button",
                        **{"data-bs-dismiss": "modal", "data-dismiss": "modal"},
                    ),
                ),
            )
        )

    @reactive.effect
    @reactive.event(input[add_ns("user")])
    async def _check_new_user_exists() -> None:
        await _warn_if_exists(
            input[add_ns("user")](),
            existing=[u["user"] for u in users()],
            button="added_user",
        )

    @reactive.effect
    @reactive.event(input.added_user)
    def _add_user() -> None:
        values = _modal_values(add_ns, with_password=True)
        must_change = bool(values.pop("must_change", True))
        password = values.pop("password", None)
        ui.modal_show(_spinner_modal())
        try:
            add_user({**values, "password": password}, must_change=must_change)
        except Exception:
            ui.modal_remove()
            ui.notification_show(lan.get("Failed to update user"), type="error")
            return
        ui.modal_remove()
        # Faute "succesfully" du source preservee.
        created = lan.get("New user %s succesfully created!") % f"<b>{values['user']}</b>"
        ui.modal_show(
            ui.modal(
                ui.tags.p(ui.HTML(created)),
                ui.tags.p(lan.get("Password:"), ui.tags.b(password)),
                footer=ui.modal_button(lan.get("Dismiss")),
            )
        )
        _bump()

    # --- Edition ----------------------------------------------------------------------------

    @reactive.effect
    @reactive.event(input.edit_user)
    def _ask_edit_user() -> None:
        ui.modal_show(
            ui.modal(
                *edit_user_inputs(edit_ns, _credentials(), [input.edit_user()], inputs_list, lan),
                ui.tags.div(id=html_id("placeholder-edituser-exist")),
                title=lan.get("Edit user"),
                footer=ui.TagList(
                    ui.modal_button(lan.get("Cancel")),
                    ui.tags.button(
                        lan.get("Confirm change"),
                        id=ns("edited_user"),
                        class_="btn btn-primary action-button",
                        **{"data-bs-dismiss": "modal", "data-dismiss": "modal"},
                    ),
                ),
            )
        )

    @reactive.effect
    @reactive.event(input[edit_ns("user")])
    async def _check_edited_user_exists() -> None:
        existing = [u["user"] for u in users() if u["user"] != input.edit_user()]
        await _warn_if_exists(
            input[edit_ns("user")](),
            existing=existing,
            button="edited_user",
            placeholder="placeholder-edituser-exist",
            alert="alert-edituser-exist",
        )

    @reactive.effect
    @reactive.event(input.edited_user)
    def _edit_user() -> None:
        values = _modal_values(edit_ns)
        enabled_null = bool(values.pop("_sm_enabled_null", True))
        changes = apply_edit(values, _columns(_credentials()), enabled_null=enabled_null)
        ui.modal_show(_spinner_modal())
        try:
            edit_user(input.edit_user(), changes)
        except Exception:
            ui.modal_remove()
            # Cle absente du dictionnaire du source : texte brut.
            ui.notification_show(lan.get("Fail to update user"), type="error")
            return
        ui.modal_remove()
        ui.notification_show(lan.get("User successfully updated"), type="message")
        _bump()

    @reactive.effect
    @reactive.event(input.edit_selected_users)
    def _ask_edit_selected() -> None:
        ui.modal_show(
            ui.modal(
                *edit_user_inputs(
                    mult_ns, _credentials(), _selected("select_mult_users"), inputs_list, lan
                ),
                title="Edit user",  # titre en dur dans le source
                footer=ui.TagList(
                    ui.modal_button(lan.get("Cancel")),
                    ui.tags.button(
                        lan.get("Confirm change"),
                        id=ns("edited_mult_user"),
                        class_="btn btn-primary action-button",
                        **{"data-bs-dismiss": "modal", "data-dismiss": "modal"},
                    ),
                ),
            )
        )

    @reactive.effect
    @reactive.event(input.edited_mult_user)
    def _edit_selected() -> None:
        selected = _selected("select_mult_users")
        values = _modal_values(mult_ns)
        columns = _columns(_credentials())
        if len(selected) == 1:
            # DEVIATION assumee (Jeremy 2026-07-16) : 1 seul selectionne -> edition COMPLETE
            # (user/admin editables, `_sm_enabled_null` respecte), comme le crayon de ligne.
            # La modale s'est deja rendue en mode simple (edit_user_inputs, len==1).
            enabled_null = bool(values.pop("_sm_enabled_null", True))
            changes = apply_edit(values, columns, enabled_null=enabled_null)
        else:
            # Edition multiple (source) : user et admin retires, champs vides ignores
            # (`check_isTruthy = TRUE`, module-edit_user.R:183-197) pour ne pas effacer une
            # colonne chez tout le lot.
            values.pop("user", None)
            values.pop("admin", None)
            changes = apply_edit(values, columns, enabled_null=False)
        ui.modal_show(_spinner_modal())
        try:
            for user in selected:
                edit_user(user, changes)
        except Exception:
            ui.modal_remove()
            ui.notification_show(lan.get("Fail to update user"), type="error")
            return
        ui.modal_remove()
        ui.notification_show(lan.get("User successfully updated"), type="message")
        _bump()

    # --- Mots de passe ----------------------------------------------------------------------

    @reactive.effect
    @reactive.event(input.change_pwd)
    def _ask_change_pwd() -> None:
        ui.modal_show(_change_pwd_modal(ns("changed_password"), [input.change_pwd()], lan))

    @reactive.effect
    @reactive.event(input.changed_password)
    def _change_pwd() -> None:
        _apply_force_change([input.change_pwd()])

    @reactive.effect
    @reactive.event(input.change_selected_pwds)
    def _ask_change_selected_pwds() -> None:
        ui.modal_show(
            _change_pwd_modal(ns("changed_password_users"), _selected("change_mult_pwds"), lan)
        )

    @reactive.effect
    @reactive.event(input.changed_password_users)
    def _change_selected_pwds() -> None:
        _apply_force_change(_selected("change_mult_pwds"))

    def _apply_force_change(targets: list[str]) -> None:
        ui.modal_show(_spinner_modal())
        try:
            for user in targets:
                force_change_password(user)
        except Exception:
            ui.modal_remove()
            ui.notification_show(lan.get("Failed to update the database"), type="error")
            return
        ui.modal_remove()
        ui.notification_show(lan.get("Change saved!"), type="message")
        _bump()

    @reactive.effect
    @reactive.event(input.reset_pwd)
    def _ask_reset_pwd() -> None:
        ui.modal_show(_reset_pwd_modal(ns("reseted_password"), [input.reset_pwd()], lan))

    @reactive.effect
    @reactive.event(input.reseted_password)
    def _reset_pwd() -> None:
        ui.modal_show(_spinner_modal())
        try:
            password = reset_password(input.reset_pwd())
        except Exception:
            ui.modal_remove()
            # Cle du source pour CE site (module-admin.R:824) : "Failed to update user", qui est
            # traduite. A ne pas confondre avec "Fail to update user" (module-admin.R:536/600,
            # edition), qui est absente du dictionnaire.
            ui.notification_show(lan.get("Failed to update user"), type="error")
            return
        ui.modal_remove()
        ui.modal_show(
            ui.modal(
                # Faute "succesfully" du source preservee.
                ui.tags.p(lan.get("Password succesfully reset!")),
                ui.tags.p(lan.get("Temporary password:"), ui.tags.b(password)),
                footer=ui.modal_button(lan.get("Dismiss")),
            )
        )
        _bump()

    # --- Telechargements --------------------------------------------------------------------

    @render.download(filename=lambda: f"shinymanager-users-{date.today()}.csv")
    def download_users_database():
        if "users" not in (settings.get_option("download") or []):
            return
        yield users_csv()

    @render.download(filename=lambda: f"shinymanager-sql-{date.today()}.sqlite")
    def download_sql_database():
        kind, handle = _active()
        if "db" not in (settings.get_option("download") or []) or kind != "sqlite":
            return
        yield Path(handle).read_bytes()

    # --- Utilitaires ------------------------------------------------------------------------

    def _credentials() -> list[dict[str, Any]]:
        """Credentials complets (colonnes de mot de passe comprises) pour construire les modales."""
        update_read_db()
        kind, handle = _active()
        return [] if kind is None else _read(kind, handle, "credentials")

    def _modal_values(sub_ns: Any, with_password: bool = False) -> dict[str, Any]:
        """Relit les inputs de la modale `sub_ns` (le source utilise reactiveValuesToList)."""
        keys = [c for c in _columns(_credentials()) if c not in _PWD_COLS]
        keys += ["password", "must_change"] if with_password else ["_sm_enabled_null"]
        values: dict[str, Any] = {}
        for key in keys:
            input_id = sub_ns(key)
            if input_id not in input:
                continue
            value = input[input_id]()
            if isinstance(value, (list, tuple)):
                value = ";".join(str(v) for v in value)  # multi-valeurs jointes par ';'
            elif isinstance(value, date):
                value = value.isoformat()
            elif isinstance(value, bool) and key not in ("must_change", "_sm_enabled_null"):
                value = "TRUE" if value else "FALSE"
            values[key] = value
        return values

    async def _warn_if_exists(
        new: str,
        existing: list[str],
        button: str,
        placeholder: str = "placeholder-user-exist",
        alert: str = "alert-user-exist",
    ) -> None:
        """Alerte 'User already exist!' + desactivation du bouton de confirmation."""
        # html_id et NON ns : les ids du source contiennent des tirets, que ResolvedId refuse.
        ui.remove_ui(selector=f"#{html_id(alert)}", immediate=True)
        if new in existing:
            ui.insert_ui(
                selector=f"#{html_id(placeholder)}",
                ui=ui.tags.div(
                    icon_svg("triangle-exclamation"),
                    " " + lan.get("User already exist!"),
                    id=html_id(alert),
                    class_="alert alert-warning",
                ),
                immediate=True,
            )
            await _toggle(button, False)
        elif new == "":
            await _toggle(button, False)
        else:
            await _toggle(button, True)
