"""Module Shiny de login (source R/module-auth.R).

Module 8 du plan. Deux surfaces coherentes :
  - `_evaluate_login` (decision pure) + `process_login` (orchestration) : testables sans
    navigateur ; REUTILISES par `secure_app` (flux cookie httponly) ET par `auth_server` ci-dessous.
  - `auth_ui` / `auth_server` : le module de login REACTIF standalone, reconstruit fidelement
    contre `docs/migration/comprehension/surface/auth-screens.md` (panneau centre, i18n, selecteur
    de langue, params du source). Utilisable hors du flux cookie : `auth_server` expose l'etat
    d'authentification (contrat `authentication` du source) ; l'integrateur gere le token.

`_evaluate_login` reproduit exactement l'arbre de decision de l'observeEvent du source
(module-auth.R L249-322). Le cablage reactif est `# pragma: no cover` (approche DU1).
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from faicons import icon_svg
from shiny import Inputs, Outputs, Session, module, reactive, render, ui
from shiny.module import current_namespace

from shinymanager import settings
from shinymanager.i18n import Language, use_language
from shinymanager.logs import save_logs_failed
from shinymanager.pwd_lifecycle import check_locked_account, is_temp_pwd_expired
from shinymanager.reset_password import reset_pwd_user_email, save_reset_logs
from shinymanager.tokens import _tok

#: Langues enregistrees (code -> nom d'affichage), depuis le module i18n (extrait du source).
_REGISTERED = Language().get_language_registered()


def _language_codes(language: str, choose_language: Any) -> list[str]:
    """Codes proposes dans le selecteur de langue (fidele a module-auth.R:65-71)."""
    if choose_language is True:
        return list(_REGISTERED)
    if isinstance(choose_language, str):
        codes = [choose_language] if choose_language in _REGISTERED else []
    elif isinstance(choose_language, (list, tuple)):
        codes = [c for c in choose_language if c in _REGISTERED]
    else:
        return [language]
    if language not in codes:
        codes = [*codes, language]
    return codes


@dataclass
class LoginOutcome:
    """Resultat de l'evaluation d'une tentative de login."""

    authenticated: bool
    log_status: str | None
    message_key: str | None


def _evaluate_login(
    res_auth: dict[str, Any], locked: bool, temp_pwd_expired: bool = False
) -> LoginOutcome:
    """Traduit (resultat check_credentials, verrouillage, expiration du mdp temporaire) en decision.

    Reproduit l'arbre du source : succes non verrouille et mdp temporaire valide -> authentifie ;
    succes verrouille -> compte verrouille ; succes avec mdp temporaire expire (1.1.1.1) ->
    message dedie ; sinon message selon user inconnu / expire / non autorise / mauvais mdp.
    """
    if res_auth.get("result") and not locked and not temp_pwd_expired:
        return LoginOutcome(True, None, None)
    if res_auth.get("result") and locked:
        return LoginOutcome(False, "Locked Account", "Your account is locked")
    if res_auth.get("result") and temp_pwd_expired:
        # Message dedie : n'est montre qu'a qui connait le mot de passe (temporaire), il ne
        # revele donc rien sur l'existence des comptes.
        return LoginOutcome(
            False,
            "Reset password: expired",
            "Your temporary password has expired, please request a new one.",
        )
    if res_auth.get("user_info") is None:
        return LoginOutcome(False, "Unknown user", "Username or password are incorrect")
    if res_auth.get("expired"):
        return LoginOutcome(False, "Expired", "Your account has expired")
    if not res_auth.get("authorized"):
        return LoginOutcome(False, "Unauthorized", "You are not authorized for this application")
    return LoginOutcome(False, "Wrong pwd", "Username or password are incorrect")


def process_login(
    user: str, password: str, check_credentials: Callable[[str, str], dict]
) -> tuple[LoginOutcome, str | None, Any]:
    """Traite une tentative de login (verification, verrouillage, journalisation, token).

    Returns:
        (outcome, token, user_info). token/user_info non None uniquement si authentifie.
    """
    res_auth = check_credentials(user, password)
    limit = settings.get_pwd_failure_limit()
    locked = check_locked_account(user, limit) if math.isfinite(limit) else False
    # mot de passe temporaire envoye par le reset en libre-service et plus valide ?
    temp_pwd_expired = bool(res_auth.get("result")) and is_temp_pwd_expired(user)

    outcome = _evaluate_login(res_auth, locked, temp_pwd_expired)
    if outcome.authenticated:
        token = _tok.generate(user)
        _tok.add(token, dict(res_auth.get("user_info") or {}))
        return outcome, token, res_auth.get("user_info")

    if outcome.log_status:
        save_logs_failed(user, outcome.log_status)
    return outcome, None, None


@module.ui
def auth_ui(
    language: str = "en",
    *,
    status: str = "primary",
    tags_top: Any = None,
    tags_bottom: Any = None,
    background: str | None = None,
    choose_language: Any = None,
    **deprecated: Any,
) -> ui.TagList:
    """UI du module de login reactif (equivalent auth_ui R, module-auth.R:24-147).

    Panneau CENTRE (col-4 offset-4, panel panel-<status>, panel-body), i18n, selecteur de langue
    optionnel (`choose_language`), points d'injection `tags_top`/`tags_bottom`, fond `background`.
    Ids namespaces du source : user_id / user_pwd / go_auth / language / result_auth.

    `tag_img`/`tag_div` (deprecies dans le source) sont acceptes et redirigent vers
    `tags_top`/`tags_bottom` avec un avertissement, a l'identique de module-auth.R:37-43.

    C'est le module reactif standalone (login sans le flux cookie de secure_app). Il expose l'etat
    d'authentification via `auth_server` ; l'integrateur decide de la gestion du token.
    """
    # Compat deprecie du source : tag_img -> tags_top, tag_div -> tags_bottom.
    if "tag_img" in deprecated:
        warnings.warn(
            "'tag_img' est deprecie ; utiliser 'tags_top'.", DeprecationWarning, stacklevel=2
        )
        tags_top = deprecated["tag_img"]
    if "tag_div" in deprecated:
        warnings.warn(
            "'tag_div' est deprecie ; utiliser 'tags_bottom'.", DeprecationWarning, stacklevel=2
        )
        tags_bottom = deprecated["tag_div"]

    lan = use_language(language)
    ns = f"{current_namespace()}-"
    head: list[Any] = [
        ui.tags.link(rel="stylesheet", href="/shinymanager/styles-auth.css"),
        ui.tags.script(src="/shinymanager/bindEnter.js"),
    ]
    if background:
        head.append(ui.tags.style(f".panel-auth {{background:{background.rstrip(';')},#FFF;}}"))

    codes = _language_codes(language, choose_language)
    lang_options = [
        ui.tags.option(
            _REGISTERED.get(c, c), value=c, selected="selected" if c == language else None
        )
        for c in codes
    ]
    # Bloc langue fidele (module-auth.R:88-113) : label a gauche (output reactif), select a droite ;
    # masque si une seule langue, sinon superpose au titre via marge negative.
    lang_block = ui.tags.div(
        ui.tags.div(
            ui.tags.div(ui.output_ui("label_language"), class_="col-sm-4 offset-sm-4"),
            ui.tags.div(
                ui.tags.div(
                    ui.tags.select(
                        *lang_options, id=f"{ns}language", class_="form-select", style="width:100%;"
                    ),
                    style="text-align:left;font-size:12px;",
                ),
                class_="col-sm-4",
            ),
            class_="row",
        ),
        style=("display:none" if len(codes) == 1 else "margin-bottom:-50px;"),
    )

    panel_body = ui.tags.div(
        lang_block,
        ui.tags.div(
            tags_top,
            ui.tags.h3(lan.get("Please authenticate"), id=f"{ns}shinymanager-auth-head"),
            style="text-align:center;",
        ),
        ui.tags.br(),
        ui.tags.div(
            ui.input_text("user_id", lan.get("Username:"), width="100%"),
            ui.input_password("user_pwd", lan.get("Password:"), width="100%"),
            id=f"{ns}user_input",
        ),
        ui.tags.br(),
        ui.tags.div(
            ui.input_action_button(
                "go_auth", lan.get("Login"), width="100%", class_=f"btn-{status}"
            ),
            ui.tags.br(),
            ui.tags.br(),
            id=f"{ns}container-btn-ok",
        ),
        # 1.1.1.1 : lien de reset en libre-service + ancre du panneau (module-auth.R:134-143).
        ui.tags.div(
            ui.input_action_link("show_reset_pwd", lan.get("Forgot password?")),
            style="text-align: center; margin-top: -10px; margin-bottom: 10px;",
        )
        if settings.reset_password_enabled()
        else None,
        ui.tags.div(id=f"{ns}reset_pwd_panel"),
        ui.tags.script(f"bindEnter('{ns}');"),
        ui.tags.br(),
        ui.output_ui("result_auth"),
        # Separateur avant l'injection basse, seulement si presente (module-auth.R:139).
        ui.tags.div(ui.tags.hr(), style="margin-top:-10px;") if tags_bottom is not None else None,
        tags_bottom,
        ui.output_ui("update_shinymanager_language"),
        class_="panel-body",
    )
    return ui.TagList(
        ui.tags.head(*head),
        ui.tags.div(
            ui.tags.br(),
            ui.tags.div(style="height:70px;"),
            ui.tags.br(),
            ui.tags.div(
                ui.tags.div(
                    ui.tags.div(panel_body, class_=f"panel panel-{status} card"),
                    class_="col-sm-4 offset-sm-4",
                ),
                class_="row",
            ),
            id=f"{ns}auth-mod",
            class_="panel-auth",
        ),
    )


@module.server
def auth_server(
    input: Inputs,
    output: Outputs,
    session: Session,
    check_credentials: Callable[[str, str], dict],
    language: str = "en",
) -> reactive.Value:
    """Serveur du module de login reactif (equivalent auth_server R, module-auth.R:179-335).

    Sur clic `go_auth` : verifie, journalise les echecs, genere le token en cas de succes, et
    expose l'etat d'authentification. Reproduit aussi le changement de langue a la volee
    (relabel des champs, module-auth.R:197-222).

    Returns:
        reactive.value ``{result, user, user_info, token}`` (contrat `authentication` du source).
    """
    auth = reactive.value({"result": False, "user": None, "user_info": None, "token": None})
    message: reactive.Value = reactive.value(None)
    current_lan: reactive.Value = reactive.value(use_language(language))

    @reactive.effect  # pragma: no cover
    @reactive.event(input.language, ignore_init=True)
    def _on_language() -> None:
        lang = input.language()
        lan = use_language(lang)
        current_lan.set(lan)
        # 1.1.1.1 : retire les messages transitoires (pas de texte dans l'ancienne langue)
        message.set(None)
        ui.remove_ui(selector=f"#{session.ns('reset_pwd_msg')}")
        ui.update_text("user_id", label=lan.get("Username:"))
        ui.update_text("user_pwd", label=lan.get("Password:"))
        ui.update_action_button("go_auth", label=lan.get("Login"))
        if settings.reset_password_enabled():
            ui.update_action_link("show_reset_pwd", label=lan.get("Forgot password?"))
            ui.update_text("reset_user", label=lan.get("Username:"))
            ui.update_text("reset_email", label=lan.get("Email:"))
            ui.update_action_button("do_reset_pwd", label=lan.get("Reset my password"))

    @render.ui  # pragma: no cover
    def label_language() -> Any:
        # Label du selecteur de langue (module-auth.R:216-219), relabellise a la volee.
        return ui.tags.p(
            current_lan().get("Language") + " :",
            style="text-align:right;font-style:italic;margin-top:5px;",
        )

    @reactive.effect  # pragma: no cover
    @reactive.event(input.go_auth)
    def _on_submit() -> None:
        outcome, token, user_info = process_login(
            input.user_id(), input.user_pwd(), check_credentials
        )
        if outcome.authenticated:
            auth.set(
                {"result": True, "user": input.user_id(), "user_info": user_info, "token": token}
            )
            message.set(None)
        else:
            message.set(outcome.message_key)

    # --- Reset en libre-service (1.1.1.1, module-auth.R:373-419) ---------------------------
    @reactive.effect  # pragma: no cover
    @reactive.event(input.show_reset_pwd, ignore_init=True)
    def _on_show_reset_pwd() -> None:
        lan = current_lan()
        ui.remove_ui(selector=f"#{session.ns('reset_pwd_form')}")
        ui.insert_ui(
            ui.tags.div(
                ui.tags.hr(),
                ui.input_text("reset_user", lan.get("Username:"), width="100%"),
                None
                if settings.reset_password_username_only()
                else ui.input_text("reset_email", lan.get("Email:"), width="100%"),
                ui.input_action_button("do_reset_pwd", lan.get("Reset my password"), width="100%"),
                ui.tags.br(),
                ui.tags.br(),
                ui.tags.div(id=session.ns("reset_pwd_result")),
                id=session.ns("reset_pwd_form"),
            ),
            selector=f"#{session.ns('reset_pwd_panel')}",
        )

    @reactive.effect  # pragma: no cover
    @reactive.event(input.do_reset_pwd, ignore_init=True)
    def _on_do_reset_pwd() -> None:
        ui.remove_ui(selector=f"#{session.ns('reset_pwd_msg')}")
        # Le resultat est journalise cote admin mais jamais montre : le meme message generique
        # est toujours affiche, pour ne pas reveler quels comptes / emails existent.
        email = None if settings.reset_password_username_only() else input.reset_email()
        res = reset_pwd_user_email(input.reset_user(), email)
        save_reset_logs(input.reset_user(), res["reason"])
        ui.insert_ui(
            ui.tags.div(
                icon_svg("circle-info"),
                " "
                + current_lan().get(
                    "If the account exists and an email address is associated with it, an "
                    "email containing a temporary password has been sent."
                ),
                id=session.ns("reset_pwd_msg"),
                class_="alert alert-info",
            ),
            selector=f"#{session.ns('reset_pwd_result')}",
        )

    @render.ui  # pragma: no cover
    def update_shinymanager_language() -> Any:
        # Ancre du source (module-auth.R:212-214) : injecte l'i18n client-wide au changement de
        # langue. Le relabel cible (champs/label) est fait ci-dessus ; l'i18n client-wide global
        # (traduction de data-i18n hors module) n'est pas portee -> ecart suivi au registre.
        return None

    @render.ui  # pragma: no cover
    def result_auth() -> Any:
        if message() is None:
            return None
        return ui.div(
            icon_svg("triangle-exclamation"),
            " " + current_lan().get(message()),
            id=f"{current_namespace()}-msg_auth",
            class_="alert alert-danger",
        )

    return auth
