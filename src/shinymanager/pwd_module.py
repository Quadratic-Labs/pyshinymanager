"""Module Shiny de changement de mot de passe (source R/module-pwd.R).

Module 9 du plan. Fidelite IDIOMATIQUE (py-shiny), approche de validation DU1 : l'orchestration
`process_pwd_change` (cascade de validations + update) est testee sans navigateur ; `pwd_ui`
retourne une structure verifiable ; `pwd_server` est un cablage reactif mince (smoke au module 11).

Cascade reproduite du source (module-pwd.R L142-201), dans l'ordre :
  1. les deux mots de passe doivent etre identiques ;
  2. le nouveau doit differer de l'ancien (check_new_pwd) ;
  3. il doit respecter la politique (validate_pwd) ;
  4. update_pwd ; succes ou echec.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from faicons import icon_svg
from shiny import Inputs, Outputs, Session, module, reactive, render, ui
from shiny.module import current_namespace

from shinymanager.i18n import use_language
from shinymanager.passwords import validate_pwd as _default_validate_pwd
from shinymanager.pwd_lifecycle import check_new_pwd
from shinymanager.pwd_lifecycle import update_pwd as _default_update_pwd


@dataclass
class PwdChangeOutcome:
    """Resultat d'une tentative de changement de mot de passe."""

    success: bool
    message_key: str


def process_pwd_change(
    user: str,
    pwd_one: str,
    pwd_two: str,
    validate_pwd: Callable[[str], bool] | None = None,
    update_pwd: Callable[[str, str], dict] | None = None,
) -> PwdChangeOutcome:
    """Applique la cascade de validation puis le changement de mot de passe.

    Args:
        user: identifiant de l'utilisateur.
        pwd_one: nouveau mot de passe.
        pwd_two: confirmation.
        validate_pwd: politique de validation (defaut : politique par defaut du package).
        update_pwd: fonction d'update injectable (source : parametre de `pwd_server` ;
            defaut : `pwd_lifecycle.update_pwd` du package).

    Returns:
        PwdChangeOutcome (success + cle de message i18n).
    """
    validator = validate_pwd or _default_validate_pwd
    updater = update_pwd or _default_update_pwd
    if pwd_one != pwd_two:
        return PwdChangeOutcome(False, "The two passwords are different")
    if not check_new_pwd(user, pwd_one):
        return PwdChangeOutcome(False, "New password cannot be the same as old")
    if not validator(pwd_one):
        return PwdChangeOutcome(False, "Password does not respect safety requirements")
    if updater(user, pwd_one)["result"]:
        return PwdChangeOutcome(True, "Password successfully updated! Please re-login")
    return PwdChangeOutcome(False, "Failed to update password")


@module.ui
def pwd_ui(language: str = "en", tag_img: object = None, status: str = "primary") -> ui.TagList:
    """UI du module de changement de mot de passe (equivalent pwd_ui R).

    Structure fidele au source (module-pwd.R:18-83) : head (styles-auth.css + bindEnter.js),
    div#pwd-mod.panel-auth, panneau CENTRE (fluidRow > column(4, offset=4) > panel panel-<status>
    > panel-body), titre h3, image optionnelle `tag_img`, hint `help-block` + icone circle-info,
    conteneur `container-btn-update`, appel `bindEnter(ns)`, ancre `result_pwd`.
    """
    lan = use_language(language)
    hint = lan.get(
        "Password must contain at least one number, one lowercase, "
        "one uppercase and must be at least length 6."
    )
    # Equivalent de `ns("")` en R (= "password-") : resolve_id refuse la chaine vide.
    ns = f"{current_namespace()}-"
    panel_body = ui.tags.div(
        ui.tags.div(
            tag_img,
            ui.tags.h3(lan.get("Please change your password")),
            style="text-align: center;",
        ),
        ui.tags.br(),
        ui.input_password("pwd_one", lan.get("New password:"), width="100%"),
        ui.input_password("pwd_two", lan.get("Confirm password:"), width="100%"),
        ui.tags.span(icon_svg("circle-info"), " " + hint, class_="help-block"),
        ui.tags.br(),
        ui.tags.div(
            ui.input_action_button(
                "update_pwd", lan.get("Update new password"), width="100%", class_=f"btn-{status}"
            ),
            ui.tags.br(),
            ui.tags.br(),
            id=f"{ns}container-btn-update",
        ),
        # Le source lie la touche Entree aux champs du module (module-pwd.R:73-75).
        ui.tags.script(f"bindEnter('{ns}');"),
        ui.tags.br(),
        ui.output_ui("result_pwd"),
        class_="panel-body",
    )
    return ui.TagList(
        ui.tags.head(
            ui.tags.link(href="/shinymanager/styles-auth.css", rel="stylesheet"),
            ui.tags.script(src="/shinymanager/bindEnter.js"),
        ),
        ui.tags.div(
            ui.tags.br(),
            ui.tags.div(style="height: 70px;"),
            ui.tags.br(),
            ui.tags.div(
                ui.tags.div(
                    ui.tags.div(panel_body, class_=f"panel panel-{status} card"),
                    class_="col-sm-4 offset-sm-4",
                ),
                class_="row",
            ),
            id=f"{ns}pwd-mod",
            class_="panel-auth",
        ),
    )


@module.server
def pwd_server(
    input: Inputs,
    output: Outputs,
    session: Session,
    user: dict[str, Any],
    update_pwd: Callable[[str, str], dict] | None = None,
    validate_pwd: Callable[[str], bool] | None = None,
    language: str = "en",
) -> reactive.Value:
    """Serveur du module de changement de mot de passe (equivalent pwd_server R).

    `update_pwd` reproduit le parametre injectable du source (defaut : update du package).

    Returns:
        Une reactive.value exposant le resultat du changement a l'appelant.
    """
    lan = use_language(language)
    password = reactive.value({"result": False, "user": None})
    message = reactive.value(None)
    success = reactive.value(False)

    @reactive.effect  # pragma: no cover
    @reactive.event(input.update_pwd)
    def _on_update() -> None:
        outcome = process_pwd_change(
            user["user"], input.pwd_one(), input.pwd_two(), validate_pwd, update_pwd
        )
        message.set(outcome.message_key)
        success.set(outcome.success)
        if outcome.success:
            password.set({"result": True, "user": user["user"]})

    @render.ui  # pragma: no cover
    def result_pwd() -> Any:
        if message() is None:
            return None
        if success():
            # Message de succes + bouton de reconnexion (equivalent du bouton relog du source).
            return ui.div(
                ui.div(
                    icon_svg("check"),
                    " " + lan.get(message()),
                    class_="alert alert-success",
                ),
                ui.tags.a(lan.get("Login"), href="/sm-logout", class_="btn btn-primary w-100"),
            )
        return ui.div(
            icon_svg("triangle-exclamation"),
            " " + lan.get(message()),
            class_="alert alert-danger",
        )

    return password
