"""Phase 8 — tests du panneau d'administration (contrat, integration backend, sans navigateur).

Deux niveaux :
  - les OPERATIONS (add / edit / delete / reset / force-change + listes) sont testees en
    integration contre un backend reel, sur les deux backends ;
  - la SURFACE (ids d'input, libelles i18n, modele d'interaction, gardes) est verifiee contre
    docs/migration/comprehension/surface/admin-panel.md, l'admin ayant ete reconstruit fidelement.
Le cablage reactif reste hors couverture (approche DU1).
"""

import pytest
from shiny.module import ResolvedId

from shinymanager import admin, logs, settings
from shinymanager.check_credentials import check_credentials
from shinymanager.db import create_db, read_db
from shinymanager.db_sql import create_sql_db, read_table_sql
from shinymanager.i18n import use_language
from shinymanager.pwd_lifecycle import is_force_chg_pwd
from shinymanager.tokens import _tok


@pytest.fixture(autouse=True)
def _reset():
    settings.reset_options()
    _tok.__init__()
    yield
    settings.reset_options()
    _tok.__init__()


@pytest.fixture
def sqlite_backend(tmp_path):
    path = str(tmp_path / "c.sqlite")
    create_db([{"user": "alice", "password": "Secret1", "admin": "TRUE"}], path)
    _tok.set_sqlite_path(path)
    return path


@pytest.fixture
def sql_backend(tmp_path):
    conf = {"connection": {"url": f"sqlite:///{tmp_path / 'sql.sqlite'}"}}
    create_sql_db([{"user": "alice", "password": "Secret1"}], conf)
    _tok.set_sql_config_db(conf)
    return conf


# --- Ajout ---


def test_add_user_sqlite_creates_credentials_and_pwd_mngt(sqlite_backend):
    admin.add_user({"user": "bob", "password": "Bob12345"})
    creds = {r["user"]: r for r in read_db(sqlite_backend, "credentials")}
    assert "bob" in creds
    # mot de passe hache et verifiable via check_credentials.
    check = check_credentials(sqlite_backend)
    assert check("bob", "Bob12345")["result"] is True
    pwd = {r["user"]: r for r in read_db(sqlite_backend, "pwd_mngt")}
    assert pwd["bob"]["must_change"] == "TRUE"
    assert pwd["bob"]["n_wrong_pwd"] == 0


def test_add_user_sql_backend(sql_backend):
    admin.add_user({"user": "bob", "password": "Bob12345"})
    users = {r["user"] for r in read_table_sql(sql_backend, "credentials")}
    assert users == {"alice", "bob"}


# --- Suppression ---


def test_delete_user_removes_from_both_tables(sqlite_backend):
    admin.add_user({"user": "bob", "password": "Bob12345"})
    admin.delete_user("bob")
    assert "bob" not in {r["user"] for r in read_db(sqlite_backend, "credentials")}
    assert "bob" not in {r["user"] for r in read_db(sqlite_backend, "pwd_mngt")}


def test_delete_user_sql(sql_backend):
    admin.add_user({"user": "bob", "password": "Bob12345"})
    admin.delete_user("bob")
    assert "bob" not in {r["user"] for r in read_table_sql(sql_backend, "credentials")}
    assert "bob" not in {r["user"] for r in read_table_sql(sql_backend, "pwd_mngt")}


# --- Edition ---


def test_edit_user_updates_column(sqlite_backend):
    admin.edit_user("alice", {"admin": "FALSE"})
    creds = {r["user"]: r for r in read_db(sqlite_backend, "credentials")}
    assert creds["alice"]["admin"] == "FALSE"


# --- Reset / force change ---


def test_reset_password_returns_new_and_forces_change(sqlite_backend):
    check_credentials(sqlite_backend)  # cable _tok
    new_pwd = admin.reset_password("alice")
    assert isinstance(new_pwd, str) and len(new_pwd) == 8
    # L'ancien ne marche plus, le nouveau si.
    check = check_credentials(sqlite_backend)
    assert check("alice", "Secret1")["result"] is False
    assert check("alice", new_pwd)["result"] is True
    # must_change force.
    _tok.add("t", {"user": "alice"})
    assert is_force_chg_pwd("t") is True


def test_force_change_password_sets_must_change(sqlite_backend):
    admin.force_change_password("alice")
    _tok.add("t", {"user": "alice"})
    assert is_force_chg_pwd("t") is True


# --- Liste + logs ---


def test_list_users_excludes_password_columns(sqlite_backend):
    users = admin.list_users()
    assert users and "password" not in users[0]
    assert "is_hashed_password" not in users[0]


def test_read_logs_dedup_and_filter(sqlite_backend):
    _tok.add("tok", {"user": "alice"})
    logs.save_logs_failed("alice", "Wrong pwd")
    logs.save_logs("tok")
    success = logs.read_logs(status="Success")
    assert all(r["status"] == "Success" for r in success)
    assert any(r["user"] == "alice" for r in success)


def test_list_pwds_drops_n_wrong_pwd(sqlite_backend):
    pwds = admin.list_pwds()
    assert pwds and "n_wrong_pwd" not in pwds[0]
    assert {"user", "must_change", "have_changed", "date_change"} <= set(pwds[0])


def test_users_csv_excludes_passwords_and_uses_semicolon(sqlite_backend):
    csv_text = admin.users_csv()
    assert csv_text.splitlines()[0].startswith('"user";')
    assert "Secret1" not in csv_text
    assert "is_hashed_password" not in csv_text


# --- Fonctions pures portees du source ---


def test_make_title_matches_r_helper():
    # utils.R : capitalize(gsub("_", " ", x))
    assert admin.make_title("n_wrong_pwd") == "N wrong pwd"
    assert admin.make_title("user") == "User"


def test_apply_edit_keeps_empty_values_when_nulls_allowed():
    # _sm_enabled_null coche (defaut du source) => les valeurs vides sont ecrites.
    changes = admin.apply_edit({"user": "a", "expire": ""}, ["user", "expire"], enabled_null=True)
    assert changes == {"user": "a", "expire": ""}


def test_apply_edit_drops_empty_values_when_nulls_refused():
    # _sm_enabled_null decoche => check_isTruthy du source : les champs vides sont ignores.
    changes = admin.apply_edit({"user": "a", "expire": ""}, ["user", "expire"], enabled_null=False)
    assert changes == {"user": "a"}


def test_apply_edit_ignores_unknown_columns():
    assert admin.apply_edit({"user": "a", "zzz": "1"}, ["user"], enabled_null=True) == {"user": "a"}


# --- Surface : structure de l'UI (cf. surface/admin-panel.md) ---


def test_admin_ui_exposes_source_input_ids(sqlite_backend):
    html = admin.admin_ui("admin").get_html_string()
    for input_id in (
        "admin-add_user",
        "admin-table_users",
        "admin-table_pwds",
        "admin-select_all_users",
        "admin-edit_selected_users",
        "admin-remove_selected_users",
        "admin-change_selected_allusers",
        "admin-change_selected_pwds",
    ):
        assert input_id in html, input_id
    # Boutons de selection groupee desactives par defaut (0 selectionne).
    assert "btn-primary pull-right disabled" in html
    assert "col-sm-10 offset-sm-1" in html  # layout fluidRow > column(10, offset = 1)


def test_admin_ui_downloads_follow_the_download_option(sqlite_backend):
    assert "admin-download_users_database" in admin.admin_ui("admin").get_html_string()
    settings.set_option("download", [])
    html = admin.admin_ui("admin").get_html_string()
    assert "admin-download_users_database" not in html
    assert "admin-download_sql_database" not in html


def test_admin_ui_hides_sql_download_on_sql_backend(sql_backend):
    # Le source conditionne ce bouton au backend sqlite (conditionalPanel output$is_sqlite).
    html = admin.admin_ui("admin").get_html_string()
    assert "admin-download_sql_database" not in html


def test_users_table_uses_one_input_per_action_with_user_as_value(sqlite_backend):
    admin.add_user({"user": "bob", "password": "Bob12345"})
    table = admin.users_table_html(admin.list_users()).get_html_string()
    # Point de fidelite : 1 input par ACTION (pas 1 par utilisateur), valeur = user de la ligne.
    assert table.count("admin-edit_user") == 2
    assert table.count("admin-remove_user") == 2
    assert "&quot;alice&quot;" in table and "&quot;bob&quot;" in table
    assert "priority: &apos;event&apos;" in table
    assert "Secret1" not in table  # pas de mot de passe dans la table


def test_users_table_headers_and_admin_column_are_translated(sqlite_backend):
    lan = use_language("fr")
    table = admin.users_table_html(admin.list_users(), language="fr").get_html_string()
    assert f"<td>{lan.get('Yes')}</td>" in table  # admin = TRUE -> Oui
    tooltip = lan.get("Edit user").replace("'", "&apos;")  # echappement htmltools
    assert f'data-title="{tooltip}"' in table
    assert "<th>Utilisateur</th>" in table  # make_title(lan$get("user"))


def test_pwds_table_actions_and_yes_no(sqlite_backend):
    table = admin.pwds_table_html(admin.list_pwds()).get_html_string()
    assert "admin-change_pwd" in table
    assert "admin-reset_pwd" in table
    assert "admin-change_mult_pwds" in table  # groupe de selection
    assert "n_wrong_pwd" not in table


def test_row_button_escapes_the_username(sqlite_backend):
    # Le source interpole le nom brut dans du JS ; ici la valeur est echappee (json).
    admin.add_user({"user": "o'brien\"; alert(1)//", "password": "Bob12345"})
    table = admin.users_table_html(admin.list_users()).get_html_string()
    assert "; alert(1)//" not in table.replace("&quot;", '"').split("onclick=")[0]
    assert '\\"; alert(1)//' in table.replace("&quot;", '"')


# --- Surface : modale add / edit / edit multiple (cf. surface §3) ---


def _modal_ids(inputs):
    import re

    ids = []
    for tag in inputs:
        found = re.findall(r'id="([^"]+)"', tag.get_html_string())
        ids += [i.removesuffix("-label") for i in found[:1]]
    return ids


def test_add_modal_has_password_and_must_change(sqlite_backend):
    creds = read_db(sqlite_backend, "credentials")
    ns = ResolvedId("admin")("add_user")
    ids = _modal_ids(admin.edit_user_inputs(ns, creds, None, None, use_language("en")))
    assert ids == [
        "admin-add_user-user",
        "admin-add_user-start",
        "admin-add_user-expire",
        "admin-add_user-admin",
        "admin-add_user-password",
        "admin-add_user-must_change",
    ]


def test_edit_modal_has_enabled_null_and_no_password(sqlite_backend):
    creds = read_db(sqlite_backend, "credentials")
    ns = ResolvedId("admin")("edit_user")
    ids = _modal_ids(admin.edit_user_inputs(ns, creds, ["alice"], None, use_language("en")))
    assert ids == [
        "admin-edit_user-user",
        "admin-edit_user-start",
        "admin-edit_user-expire",
        "admin-edit_user-admin",
        "admin-edit_user-_sm_enabled_null",
    ]


def test_edit_multiple_modal_drops_user_admin_password(sqlite_backend):
    creds = read_db(sqlite_backend, "credentials")
    ns = ResolvedId("admin")("edit_mult_user")
    ids = _modal_ids(admin.edit_user_inputs(ns, creds, ["alice", "bob"], None, use_language("en")))
    # Ni user (pas de renommage groupe), ni admin, ni password/must_change/_sm_enabled_null.
    assert ids == ["admin-edit_mult_user-start", "admin-edit_mult_user-expire"]


def test_edit_modal_uses_custom_input_from_inputs_list(sqlite_backend):
    from shiny import ui

    creds = read_db(sqlite_backend, "credentials")
    inputs_list = {"user": {"fun": ui.input_select, "args": {"choices": ["alice", "bob"]}}}
    ns = ResolvedId("admin")("edit_user")
    inputs = admin.edit_user_inputs(ns, creds, ["alice"], inputs_list, use_language("en"))
    assert "<select" in inputs[0].get_html_string()


def test_edit_modal_falls_back_to_text_input_on_broken_custom_input(sqlite_backend):
    def boom(**kwargs):
        raise RuntimeError("bad input spec")

    creds = read_db(sqlite_backend, "credentials")
    ns = ResolvedId("admin")("edit_user")
    inputs = admin.edit_user_inputs(
        ns, creds, ["alice"], {"user": {"fun": boom, "args": {}}}, use_language("en")
    )
    # Repli textInput comme le source (tryCatch autour du do.call).
    assert 'type="text"' in inputs[0].get_html_string()


# --- Surface : modales de confirmation (cf. surface §4) ---


def test_remove_modal_labels_and_button():
    lan = use_language("fr")
    html = admin._remove_modal("admin-delete_user", ["alice", "bob"], lan).get_html_string()
    assert lan.get("Delete user(s)") in html
    assert lan.get("Cancel") in html
    assert "<b>alice, bob</b>" in html


def test_change_and_reset_pwd_modals_labels():
    lan = use_language("en")
    chg = admin._change_pwd_modal("admin-changed_password", ["alice"], lan).get_html_string()
    assert lan.get("Ask to change password") in chg
    assert lan.get("Confirm") in chg
    rst = admin._reset_pwd_modal("admin-reseted_password", ["alice"], lan).get_html_string()
    assert lan.get("Reset password") in rst


def test_untranslated_source_keys_fall_back_to_raw_text():
    # Quirk du source a reproduire : ces cles sont absentes du dictionnaire.
    lan = use_language("fr")
    assert lan.get("Maximum number of users : %s") == "Maximum number of users : %s"
    assert lan.get("Fail to update user") == "Fail to update user"
