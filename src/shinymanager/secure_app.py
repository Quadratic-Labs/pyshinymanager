"""Enveloppe d'authentification d'une app Shiny (source R/secure-app.R).

Module 11 du plan. Fidelite IDIOMATIQUE. Assemble toute l'application :
  - `secure_app(ui)` retourne une UI fonction de la requete qui ROUTE selon le token de cookie
    (login / changement de mot de passe / admin / app protegee) ;
  - le token transite par un cookie **httponly + samesite=strict** (D3, decision DA1 option A),
    pose par une route ASGI de login (`/sm-login`) qui reutilise `process_login` (module 8) ;
  - `/sm-logout` invalide le token et efface le cookie ;
  - `create_secure_app(...)` assemble l'App py-shiny et installe ces routes.

Le routage sous cookie utilise `is_valid_server` (appartenance) et non le `is_valid` one-shot du
source : l'anti-rejeu one-shot protegeait le token dans l'URL, sans objet avec un cookie serveur
(resolution de tokens ZF-1).
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from importlib.resources import files
from typing import Any

from faicons import icon_svg
from shiny import App, Inputs, Outputs, Session, reactive, ui
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from shinymanager import admin as admin_module
from shinymanager import fab as fab_module
from shinymanager import logs_view, settings
from shinymanager.auth_module import process_login
from shinymanager.i18n import Language, use_language
from shinymanager.logs import logout_logs, save_logs
from shinymanager.pwd_lifecycle import is_force_chg_pwd
from shinymanager.pwd_module import pwd_server, pwd_ui
from shinymanager.reset_password import reset_pwd_user_email, save_reset_logs
from shinymanager.tokens import _tok

COOKIE_NAME = "shinymanager"
#: Cookie memorisant la langue choisie au login (le source la gardait dans l'URL via le token).
LANG_COOKIE = "shinymanager_language"
#: Repertoire des assets statiques embarques (equivalent addResourcePath("shinymanager", ...)).
_WWW = files("shinymanager").joinpath("www")
#: Langues enregistrees (code -> nom d'affichage) pour le selecteur de langue du login.
#: Vient du module i18n (extrait du source) : ne PAS le recopier ici, une copie manuelle avait
#: introduit "Francais"/"Espanol" sans cedille ni tilde.
_REGISTERED = Language().get_language_registered()

#: Codes d'erreur de login -> cle i18n du message. LISTE BLANCHE : le code transite par l'URL
#: (?error=), donc seuls ces codes peuvent produire un message. Le source affiche 4 messages
#: distincts (module-auth.R:264-325) ; les reduire a un seul masque a l'utilisateur la raison
#: reelle du refus (compte verrouille / expire / non autorise).
LOGIN_ERRORS: dict[str, str] = {
    "locked": "Your account is locked",
    "expired": "Your account has expired",
    "unauthorized": "You are not authorized for this application",
    "credentials": "Username or password are incorrect",
    "temp_expired": "Your temporary password has expired, please request a new one.",
}
#: Cle i18n renvoyee par `process_login` -> code d'erreur transmis dans l'URL.
_ERROR_CODES: dict[str, str] = {key: code for code, key in LOGIN_ERRORS.items()}


def _has_backend() -> bool:
    return _tok.get_sqlite_path() is not None or _tok.get_sql_config_db() is not None


def _check_language(language: str) -> str:
    """Valide une langue, avec repli sur l'anglais + warning (port de secure-app.R:36-39)."""
    if language not in _REGISTERED:
        warnings.warn(
            "Only supported language for the now are: " + ", ".join(_REGISTERED),
            stacklevel=3,
        )
        return "en"
    return language


def _route(
    token: str | None, *, enable_admin: bool, admin_requested: bool, has_backend: bool
) -> str:
    """Decide la page a afficher : 'login' | 'password' | 'admin' | 'app'.

    Transcription du routage du source (secure-app.R), avec `is_valid_server` sous cookie.
    """
    if not token or not _tok.is_valid_server(token):
        return "login"
    if is_force_chg_pwd(token):
        return "password"
    if enable_admin and _tok.is_admin(token) and admin_requested and has_backend:
        return "admin"
    return "app"


def _language_selector(language: str, choose_language: Any) -> ui.Tag | None:
    """Selecteur de langue du login (cache si une seule langue).

    Le changement passe par la route `/sm-set-language` qui POSE LE COOKIE de langue puis
    recharge : ainsi l'UI ET le serveur (tables, notifications) lisent tous le meme cookie. Une
    ancienne version rechargeait avec `?language=`, que le serveur ne voyait pas -> UI traduite
    mais tables anglaises.
    """
    if choose_language is True:
        codes = list(_REGISTERED)
    elif isinstance(choose_language, str):
        # Le source accepte un vecteur de caracteres, donc AUSSI une chaine simple ("fr") :
        # sans ce cas, `choose_language="fr"` etait silencieusement ignore (une str est
        # iterable, mais itere sur ses lettres).
        codes = [choose_language] if choose_language in _REGISTERED else []
        if language not in codes:
            codes = [*codes, language]
    elif isinstance(choose_language, (list, tuple)):
        codes = [c for c in choose_language if c in _REGISTERED]
        if language not in codes:
            codes = [*codes, language]
    else:
        codes = [language]
    options = [
        ui.tags.option(
            _REGISTERED.get(c, c), value=c, selected="selected" if c == language else None
        )
        for c in codes
    ]
    style = "display:none" if len(codes) == 1 else "margin-bottom:10px;text-align:right;"
    lan = use_language(language)
    return ui.tags.div(
        ui.tags.span(lan.get("Language") + " :", style="font-style:italic;margin-right:6px;"),
        ui.tags.select(
            *options,
            id="auth-language",
            class_="form-select d-inline-block",
            style="width:auto;",
            # Les messages transitoires (?error=, ?reset=) ne survivent pas au changement de
            # langue (module-auth.R 1.1.1.1 : removeUI de msg_auth et reset_pwd_msg).
            onchange="var p=new URLSearchParams(location.search);p.delete('error');"
            "p.delete('reset');var q=p.toString();"
            "var n=encodeURIComponent(location.pathname+(q?'?'+q:''));"
            "location='/sm-set-language?lang='+encodeURIComponent(this.value)+'&next='+n;",
        ),
        style=style,
    )


def _login_page(
    language: str,
    error: str | None = None,
    *,
    reset: str | None = None,
    status: str = "primary",
    tags_top: Any = None,
    tags_bottom: Any = None,
    background: str | None = None,
    head_auth: Any = None,
    choose_language: Any = None,
    theme: Any = None,
) -> ui.Tag:
    """Page de login fidele au source (auth_ui) : panneau centre, i18n, selecteur de langue.

    Le token est pose par la route /sm-login (cookie httponly, DA1) : le panneau est donc un
    formulaire POST reproduisant la structure/les libelles du source.

    Args:
        language: code de langue de l'ecran.
        error: code d'erreur de `LOGIN_ERRORS` (transmis par /sm-login via ?error=). Un code
            inconnu n'affiche AUCUN message : la liste blanche interdit d'injecter du texte
            arbitraire dans la page via l'URL.
        reset: "sent" apres une demande de reset (transmis par /sm-reset-password via ?reset=) :
            affiche le message generique. Toute autre valeur est ignoree (liste blanche).
        status: statut bootstrap du panneau et du bouton.
        tags_top: contenu libre au-dessus du titre.
        tags_bottom: contenu libre sous le formulaire.
        background: valeur CSS de fond appliquee a `.panel-auth`.
        head_auth: balises libres ajoutees au `<head>`.
        choose_language: True (12 langues), une langue, une liste de langues, ou None.
        theme: theme bootstrap de la page.
    """
    lan = use_language(language)
    head = [ui.tags.link(rel="stylesheet", href="/shinymanager/styles-auth.css"), head_auth]
    if background:
        bg = background.rstrip(";")
        head.append(ui.tags.style(f".panel-auth {{background:{bg},#FFF;}}"))
    error_key = LOGIN_ERRORS.get(error) if error else None
    msg = (
        ui.tags.div(
            icon_svg("triangle-exclamation"),
            " " + lan.get(error_key),
            id="auth-msg_auth",
            class_="alert alert-danger",
        )
        if error_key
        else None
    )
    panel_body = ui.tags.div(
        _language_selector(language, choose_language),
        ui.tags.div(
            tags_top, ui.tags.h3(lan.get("Please authenticate")), style="text-align:center;"
        ),
        ui.tags.br(),
        ui.tags.form(
            # La langue choisie voyage avec le POST -> /sm-login la memorise en cookie, pour que
            # les pages ET le serveur (tables, notifications) suivantes soient dans cette langue.
            ui.tags.input(type="hidden", name="language", value=language),
            ui.tags.div(
                ui.tags.label(lan.get("Username:"), for_="auth-user_id"),
                ui.tags.input(
                    type="text", name="user", id="auth-user_id", class_="form-control", autofocus=""
                ),
                ui.tags.label(lan.get("Password:"), for_="auth-user_pwd"),
                ui.tags.input(
                    type="password", name="password", id="auth-user_pwd", class_="form-control"
                ),
                class_="mb-2",
            ),
            ui.tags.br(),
            ui.tags.button(
                lan.get("Login"), id="auth-go_auth", type="submit", class_=f"btn btn-{status} w-100"
            ),
            method="post",
            action="/sm-login",
            id="sm-login-form",
        ),
        _reset_password_block(lan, language, reset == "sent"),
        ui.tags.div(msg, id="auth-result_auth"),
        tags_bottom,
        # `card-body` restitue le padding du `.panel-body` Bootstrap 3 du source (absent en BS5).
        class_="panel-body card-body",
    )
    return ui.page_fluid(
        ui.tags.head(*[h for h in head if h is not None]),
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
            id="auth-mod",
            class_="panel-auth",
        ),
        # API publique documentee du source (@note secure-app.R:16-17) : input$shinymanager_where
        # est renseigne a CHAQUE etape. Le source ne rend pas shinymanager_language ici (#198).
        _hidden_marker("shinymanager_where", "authentication"),
        ui.tags.script(src="/shinymanager/bindEnter.js"),
        theme=theme,
    )


def _text_field(lan: Language, label_key: str, input_id: str, name: str) -> ui.Tag:
    return ui.tags.div(
        ui.tags.label(lan.get(label_key), for_=input_id, class_="control-label"),
        ui.tags.input(type="text", name=name, id=input_id, class_="form-control"),
        class_="form-group shiny-input-container",
        style="width:100%;",
    )


def _reset_password_block(lan: Language, language: str, sent: bool) -> ui.TagList | None:
    """Lien "Forgot password?" + panneau de reset (port de module-auth.R, 1.1.1.1).

    Affiche seulement si l'option `reset_password` est active. Le source insere le panneau cote
    serveur (insertUI) ; sous DA1 la page de login n'a pas de session reactive : le panneau est
    deplie en JS et soumis a la route `/sm-reset-password`, qui renvoie ici avec `?reset=sent`.
    Le message est TOUJOURS le meme (anti-enumeration).
    """
    if not settings.reset_password_enabled():
        return None
    result = (
        ui.tags.div(
            icon_svg("circle-info"),
            " "
            + lan.get(
                "If the account exists and an email address is associated with it, an email "
                "containing a temporary password has been sent."
            ),
            id="auth-reset_pwd_msg",
            class_="alert alert-info",
        )
        if sent
        else None
    )
    # Re-cliquer le lien recree un formulaire vierge, sans message (removeUI + insertUI du source).
    show_js = (
        "var f=document.getElementById('auth-reset_pwd_form');f.reset();f.style.display='';"
        "var m=document.getElementById('auth-reset_pwd_msg');if(m){m.remove();}return false;"
    )
    form = ui.tags.form(
        ui.tags.hr(),
        ui.tags.input(type="hidden", name="language", value=language),
        _text_field(lan, "Username:", "auth-reset_user", "user"),
        None
        if settings.reset_password_username_only()
        else _text_field(lan, "Email:", "auth-reset_email", "email"),
        ui.tags.button(
            lan.get("Reset my password"),
            id="auth-do_reset_pwd",
            type="submit",
            class_="btn btn-default w-100",
        ),
        ui.tags.br(),
        ui.tags.br(),
        ui.tags.div(result, id="auth-reset_pwd_result"),
        method="post",
        action="/sm-reset-password",
        id="auth-reset_pwd_form",
        style=None if sent else "display:none;",
    )
    return ui.TagList(
        # Le source place 2 <br> sous le bouton Login (container-btn-ok) : sans eux, la marge
        # negative du lien le fait chevaucher le bouton.
        ui.tags.br(),
        ui.tags.br(),
        ui.tags.div(
            ui.tags.a(
                lan.get("Forgot password?"),
                href="#",
                id="auth-show_reset_pwd",
                class_="action-button",
                onclick=show_js,
            ),
            style="text-align: center; margin-top: -10px; margin-bottom: 10px;",
        ),
        ui.tags.div(form, id="auth-reset_pwd_panel"),
    )


def _pwd_page(
    language: str,
    tag_img: Any = None,
    status: str = "primary",
    head_auth: Any = None,
    theme: Any = None,
) -> ui.Tag:
    """Ecran de changement de mot de passe (port de la branche pwd de secure-app.R:60-70)."""
    return ui.page_fluid(
        ui.tags.head(
            ui.tags.link(rel="stylesheet", href="/shinymanager/styles-auth.css"), head_auth
        ),
        pwd_ui("password", language=language, tag_img=tag_img, status=status),
        _hidden_marker("shinymanager_where", "password"),
        _hidden_marker("shinymanager_language", language),
        # Touche Entree dans les champs (source : module-pwd.R:29). L'asset etait embarque mais
        # aucune page ne le chargeait.
        ui.tags.script(src="/shinymanager/bindEnter.js"),
        theme=theme,
    )


def _admin_nav_script() -> ui.Tag:
    return ui.tags.script(
        "document.addEventListener('click', function(e){"
        "  if(e.target.closest('#sm_logout_link')) window.location='/sm-logout';"
        "  if(e.target.closest('#sm_app_link')) window.location='/';"
        "});"
    )


def _hidden_marker(input_id: str, value: str) -> ui.Tag:
    """Marqueur cache du source (shiny-utils.R) : renseigne un input avec la page courante."""
    return ui.tags.div(
        ui.input_select(input_id, None, choices=[value], selected=value),
        style="display: none;",
    )


def _admin_page(
    language: str,
    fab_position: str = "bottom-right",
    theme: Any = None,
    inputs_list: dict[str, Any] | None = None,
    max_users: int | None = None,
) -> ui.Tag:
    """Page admin fidele au source (secure-app.R:72-108) : navbarPage Admin, onglets Home/Logs."""
    lan = use_language(language)
    # FAB fidele au source (secure-app.R:79-91) : Logout + Go to application. Corrige le
    # bloquant "admin piege" (page admin sans moyen de sortir).
    fab = fab_module.fab_button(
        {"id": "sm_logout_link", "label": lan.get("Logout"), "icon": "right-from-bracket"},
        {"id": "sm_app_link", "label": lan.get("Go to application"), "icon": "share"},
        position=fab_position,
        input_id="sm_admin_fab",
    )
    header = ui.TagList(
        ui.tags.head(ui.tags.link(rel="stylesheet", href="/shinymanager/fab-button.min.css")),
        ui.tags.style(".navbar-header {margin-left: 16.66% !important;}"),
        fab,
        _hidden_marker("shinymanager_where", "admin"),
        # Rendu une seule fois ici (pas par onglet) pour eviter un id duplique (source #198).
        _hidden_marker("shinymanager_language", language),
        fab_module.timeout_script(),
        _admin_nav_script(),
    )
    tabs = [
        ui.nav_panel(
            ui.TagList(icon_svg("house"), " " + lan.get("Home")),
            admin_module.admin_ui("admin", language=language),
            value="home",
        )
    ]
    # Onglet Logs conditionne par l'option show_logs (source : show_logs_enabled()).
    if settings.get_option("show_logs"):
        tabs.append(ui.nav_panel(lan.get("Logs"), logs_view.logs_ui("logs", language=language)))
    return ui.page_navbar(
        *tabs,
        title="Admin",
        id="sm_admin_nv",
        theme=theme,
        header=header,
    )


def _app_page(base: Any, is_admin: bool, language: str, fab_position: str) -> ui.Tag:
    lan = use_language(language)
    actions = [{"id": "sm_logout_link", "label": lan.get("Logout"), "icon": "right-from-bracket"}]
    if is_admin:
        actions.append(
            {"id": "sm_admin_link", "label": lan.get("Administrator mode"), "icon": "gears"}
        )
    return ui.page_fluid(
        ui.tags.head(ui.tags.link(rel="stylesheet", href="/shinymanager/fab-button.min.css")),
        base,
        fab_module.fab_button(*actions, position=fab_position, input_id="sm_fab"),
        fab_module.timeout_script(),
        # Marqueurs de la branche application du source (secure-app.R:142-143). Le source ne
        # thematise PAS cette branche (tagList sans fluidPage(theme=)) : pas de theme ici.
        _hidden_marker("shinymanager_where", "application"),
        _hidden_marker("shinymanager_language", language),
        # Liens de navigation (logout / admin) vers les routes app-level.
        ui.tags.script(
            "document.addEventListener('click', function(e){"
            "  if(e.target.closest('#sm_logout_link')) window.location='/sm-logout';"
            "  if(e.target.closest('#sm_admin_link')) window.location='/?admin=true';"
            "});"
        ),
    )


def secure_app(
    ui_def: Any,
    *,
    enable_admin: bool = False,
    language: str = "en",
    fab_position: str = "bottom-right",
    status: str = "primary",
    head_auth: Any = None,
    theme: Any = None,
    tags_top: Any = None,
    tags_bottom: Any = None,
    background: str | None = None,
    choose_language: Any = None,
    tag_img: Any = None,
    inputs_list: dict[str, Any] | None = None,
    max_users: int | None = None,
) -> Callable[[Request], Any]:
    """Enveloppe l'UI d'une app pour exiger une authentification (equivalent secure_app R).

    Parametres de personnalisation fideles au source (auth_ui/pwd_ui) : status, head_auth, theme,
    tags_top, tags_bottom, background, choose_language (selecteur de langue au login), tag_img
    (image de l'ecran mot de passe). La langue est surchargeable par ?language= dans l'URL.
    `inputs_list` et `max_users` personnalisent le panneau admin (cf. `admin_server`).
    """
    language = _check_language(language)
    # Le source previent quand le mode admin est demande sans backend : il est alors
    # silencieusement inoperant (secure-app.R:125-127).
    if enable_admin and not _has_backend():
        warnings.warn(
            "Admin mode is only available when using a SQLite / SQL database!", stacklevel=2
        )

    def app_ui(request: Request) -> Any:
        token = request.cookies.get(COOKIE_NAME)
        # Le COOKIE est la seule source de langue (pose par le selecteur via /sm-set-language,
        # ou au login), pour que l'UI et le serveur soient TOUJOURS coherents. `?language=` n'est
        # lu qu'a defaut de cookie (1re visite avant tout choix). Validee (liste blanche).
        lang = _check_language(
            request.cookies.get(LANG_COOKIE)
            or request.query_params.get("language")
            or language
        )
        admin_requested = request.query_params.get("admin") == "true"
        page = _route(
            token,
            enable_admin=enable_admin,
            admin_requested=admin_requested,
            has_backend=_has_backend(),
        )
        if page == "login":
            return _login_page(
                lang,
                error=request.query_params.get("error"),
                reset=request.query_params.get("reset"),
                status=status,
                tags_top=tags_top,
                tags_bottom=tags_bottom,
                background=background,
                head_auth=head_auth,
                choose_language=choose_language,
                theme=theme,
            )
        if page == "password":
            return _pwd_page(lang, tag_img=tag_img, status=status, head_auth=head_auth, theme=theme)
        if page == "admin":
            return _admin_page(
                lang,
                fab_position=fab_position,
                theme=theme,
                inputs_list=inputs_list,
                max_users=max_users,
            )
        save_logs(token)
        base = ui_def(request) if callable(ui_def) else ui_def
        is_admin = enable_admin and _tok.is_admin(token) and _has_backend()
        return _app_page(base, is_admin, lang, fab_position)

    return app_ui


def parse_user_info(
    info: dict[str, Any], inputs_list: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Met en forme les infos utilisateur d'un token (port de secure-app.R:318-338).

    Comme le source, les valeurs multiples stockees en chaine sont re-eclatees sur ``;`` :
    la colonne `applications`, et toute colonne declaree `multiple` dans `inputs_list`.

    Args:
        info: infos brutes du token.
        inputs_list: inputs personnalises des modales admin (une entree `args.multiple` vraie
            signale une colonne multi-valeurs).

    Returns:
        Les infos mises en forme.
    """
    out: dict[str, Any] = {}
    for key, value in info.items():
        multiple = key == "applications"
        if not multiple and inputs_list and key in inputs_list:
            multiple = bool((inputs_list[key].get("args") or {}).get("multiple"))
        if multiple and value is not None and not isinstance(value, (list, tuple)):
            out[key] = str(value).split(";")
        else:
            out[key] = value
    return out


def user_info(
    session: Session | None = None, inputs_list: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Retourne les infos de l'utilisateur authentifie (equivalent du retour de `secure_server` R).

    A appeler depuis le serveur d'une application protegee. Le token est lu dans le cookie de la
    session RACINE (une session de module n'a pas de connexion HTTP).

    Args:
        session: session shiny (par defaut, la session active).
        inputs_list: cf. `parse_user_info`.

    Returns:
        Les infos utilisateur, ou ``{}`` si aucun utilisateur authentifie.
    """
    from shiny.session import require_active_session

    sess = session or require_active_session(None)
    conn = sess.root_scope().http_conn
    token = conn.cookies.get(COOKIE_NAME) if conn else None
    if not token:
        return {}
    return parse_user_info(_tok.get(token) or {}, inputs_list)


def secure_server(
    check_credentials: Callable[[str, str], dict],
    *,
    timeout: int = 15,
    validate_pwd: Callable[[str], bool] | None = None,
    language: str = "en",
    inputs_list: dict[str, Any] | None = None,
    max_users: int | None = None,
    file_encoding: str = "",
    send_mail: Callable[[str, str, str], Any] | None = None,
) -> Callable[[Inputs, Outputs, Session], Any]:
    """Orchestre le serveur securise (timeout, module mot de passe ; equivalent secure_server R).

    `send_mail(user, email, temp_password)` sert au reset en libre-service (option
    `reset_password`) ; il DOIT lever une exception si l'envoi echoue, sinon le mot de passe
    serait change sans que l'utilisateur le recoive. Cf. `send_smtp_mail`.

    Le login/logout passent par des routes ASGI (cf. create_secure_app) ; ce serveur cable le
    module de changement de mot de passe et le timeout d'inactivite.

    Returns:
        La fonction serveur. Appelee, elle retourne un `reactive.calc` des infos utilisateur
        (equivalent du `reactiveValues` retourne par le source). Depuis le serveur d'une app
        assemblee par `create_secure_app`, utiliser plutot la fonction `user_info()`.
    """
    _tok.set_timeout(timeout)
    _tok.set_send_mail(send_mail)
    # ECART ASSUME (Jeremy 2026-10-06) : le source ne previent pas. Sans `send_mail`, le lien
    # "Forgot password?" s'affiche mais aucune demande n'aboutit, en silence.
    if settings.reset_password_enabled() and send_mail is None:
        warnings.warn(
            "Option 'reset_password' is enabled but no 'send_mail' function was provided: "
            "password reset requests will never send any email.",
            stacklevel=2,
        )

    def server(input: Inputs, output: Outputs, session: Session) -> Any:  # pragma: no cover
        conn = session.root_scope().http_conn
        token = conn.cookies.get(COOKIE_NAME) if conn else None
        # Langue de CETTE session : le cookie pose au login prime sur le defaut, pour que les
        # rendus serveur (tables admin, notifications) suivent la langue de l'utilisateur et non
        # la langue par defaut de l'app. Sans ca, l'UI etait en francais mais les tables anglaises.
        lang = _check_language((conn.cookies.get(LANG_COOKIE) if conn else None) or language)
        # Modules cables inconditionnellement ; ils ne rendent que si leur UI est presente.
        admin_module.admin_server(
            "admin", language=lang, inputs_list=inputs_list, max_users=max_users
        )
        if settings.get_option("show_logs"):
            logs_view.logs_server("logs", language=lang, file_encoding=file_encoding)
        if token and is_force_chg_pwd(token):
            info = _tok.get(token) or {}
            pwd_server("password", user=info, validate_pwd=validate_pwd, language=lang)

        # --- Expiration de session (port de secure-app.R:376-391) --------------------------
        # Sans ces deux observers, le heartbeat client ne sert a RIEN et aucun token n'expire.
        async def _expire_if_timed_out(update: bool) -> None:
            if not token or _tok.is_valid_timeout(token, update=update):
                return
            _tok.remove(token)
            await session.send_custom_message("smReload", {})

        if timeout > 0:
            # 1) Sur activite utilisateur : rafraichit l'horodatage (update=True).
            @reactive.effect
            @reactive.event(input[fab_module.TIMEOUT_INPUT])
            async def _on_user_activity() -> None:
                await _expire_if_timed_out(update=True)

            # 2) Controle periodique (source : invalidateLater(30000)), sans rafraichir.
            @reactive.effect
            async def _check_timeout_periodically() -> None:
                reactive.invalidate_later(30)
                await _expire_if_timed_out(update=False)

        @reactive.calc
        def _user_info() -> dict[str, Any]:
            return user_info(session, inputs_list)

        return _user_info

    return server


def _make_login_handler(check_credentials: Callable[[str, str], dict]) -> Callable:
    async def login(request: Request) -> Response:
        form = await request.form()
        user = str(form.get("user", ""))
        password = str(form.get("password", ""))
        outcome, token, _info = process_login(user, password, check_credentials)
        # Memorise la langue choisie au login (champ cache du formulaire), pour les pages et le
        # serveur suivants. Validee par liste blanche (entree utilisateur).
        lang = _check_language(str(form.get("language", "")))
        if outcome.authenticated and token:
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", path="/")
            resp.set_cookie(LANG_COOKIE, lang, httponly=True, samesite="strict", path="/")
            return resp
        # Le source distingue 4 refus (verrouille / expire / non autorise / identifiants) :
        # `process_login` calcule deja la cle, il ne faut pas la jeter. On garde aussi la langue
        # pour que la page de login re-affichee reste dans la langue choisie.
        code = _ERROR_CODES.get(outcome.message_key or "", "credentials")
        resp = RedirectResponse(f"/?error={code}&language={lang}", status_code=303)
        resp.set_cookie(LANG_COOKIE, lang, httponly=True, samesite="strict", path="/")
        return resp

    return login


async def _reset_password_handler(request: Request) -> Response:
    """Demande de reset du login (port de l'observeEvent do_reset_pwd, module-auth.R 1.1.1.1).

    Le resultat est journalise cote admin mais JAMAIS montre : la page de login affiche toujours
    le meme message generique. L'envoi du mail (bloquant) tourne hors de la boucle d'evenements.
    """
    form = await request.form()
    lang = _check_language(str(form.get("language", "")))
    if not settings.reset_password_enabled():
        return RedirectResponse("/", status_code=303)
    user = str(form.get("user", ""))
    email = form.get("email")
    res = await run_in_threadpool(reset_pwd_user_email, user, None if email is None else str(email))
    save_reset_logs(user, res["reason"])
    resp = RedirectResponse(f"/?reset=sent&language={lang}", status_code=303)
    resp.set_cookie(LANG_COOKIE, lang, httponly=True, samesite="strict", path="/")
    return resp


async def _set_language_handler(request: Request) -> Response:
    """Pose le cookie de langue puis redirige vers `next` (route du selecteur de langue).

    Faire poser le cookie ici (et non recharger avec `?language=`) garantit que l'UI ET le
    serveur lisent la meme langue. `next` est restreint aux chemins locaux (anti-open-redirect).
    """
    lang = _check_language(request.query_params.get("lang", ""))
    nxt = request.query_params.get("next", "/")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    resp = RedirectResponse(nxt, status_code=303)
    resp.set_cookie(LANG_COOKIE, lang, httponly=True, samesite="strict", path="/")
    return resp


async def _logout_handler(request: Request) -> Response:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        # Horodate la deconnexion AVANT d'oublier le token (le source a le meme ordre,
        # secure-app.R:354-360 : logout_logs a besoin du token pour retrouver ses lignes).
        logout_logs(token)
        _tok.remove(token)
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    resp.delete_cookie(LANG_COOKIE, path="/")
    return resp


def create_secure_app(
    ui_def: Any,
    check_credentials: Callable[[str, str], dict],
    *,
    server: Callable[[Inputs, Outputs, Session], None] | None = None,
    enable_admin: bool = False,
    timeout: int = 15,
    validate_pwd: Callable[[str], bool] | None = None,
    language: str = "en",
    fab_position: str = "bottom-right",
    inputs_list: dict[str, Any] | None = None,
    max_users: int | None = None,
    file_encoding: str = "",
    send_mail: Callable[[str, str, str], Any] | None = None,
    **secure_app_kwargs: Any,
) -> App:
    """Assemble l'App py-shiny securisee et installe les routes de login/logout (cookie httponly).

    Args:
        ui_def: UI de l'application a proteger (contenu, ou fonction de la requete).
        check_credentials: fonction de verification (cf. `check_credentials`).
        server: serveur de l'application protegee (definit ses outputs). Compose avec le serveur
            de securite ; None si l'app n'a pas de logique serveur propre.
        enable_admin: active le mode admin (SQLite/SQL uniquement).
        timeout: timeout d'inactivite en minutes.
        validate_pwd: politique de mot de passe pour le changement.
        language: langue par defaut.
        fab_position: position du bouton flottant.
        inputs_list: inputs personnalises des modales admin (cf. `admin_server`).
        max_users: nombre maximum d'utilisateurs (garde a l'ajout).
        file_encoding: encodage du CSV de logs telecharge (cf. `fileEncoding` du source).
        send_mail: fonction `(user, email, temp_password)` d'envoi du mot de passe temporaire
            (reset en libre-service, option `reset_password`) ; cf. `secure_server`.
        **secure_app_kwargs: personnalisation transmise a `secure_app` (status, head_auth, theme,
            tags_top, tags_bottom, background, choose_language, tag_img).

    Returns:
        Une App py-shiny prete a servir (routes /sm-login, /sm-logout, /sm-set-language,
        /sm-reset-password ; assets statiques).
    """
    sec_server = secure_server(
        check_credentials,
        timeout=timeout,
        validate_pwd=validate_pwd,
        language=language,
        inputs_list=inputs_list,
        max_users=max_users,
        file_encoding=file_encoding,
        send_mail=send_mail,
    )

    def combined_server(input: Inputs, output: Outputs, session: Session) -> None:
        sec_server(input, output, session)
        if server is not None:
            server(input, output, session)

    app = App(
        secure_app(
            ui_def,
            enable_admin=enable_admin,
            language=language,
            fab_position=fab_position,
            inputs_list=inputs_list,
            max_users=max_users,
            **secure_app_kwargs,
        ),
        combined_server,
        static_assets={"/shinymanager": str(_WWW)},
    )
    # Routes app-level (chemin fixe, hors session) : posent/effacent le cookie httponly.
    login_route = Route("/sm-login", _make_login_handler(check_credentials), methods=["POST"])
    logout_route = Route("/sm-logout", _logout_handler, methods=["GET"])
    lang_route = Route("/sm-set-language", _set_language_handler, methods=["GET"])
    reset_route = Route("/sm-reset-password", _reset_password_handler, methods=["POST"])
    app.starlette_app.routes.insert(0, login_route)
    app.starlette_app.routes.insert(0, reset_route)
    app.starlette_app.routes.insert(0, logout_route)
    app.starlette_app.routes.insert(0, lang_route)
    return app
