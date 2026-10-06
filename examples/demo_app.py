"""App de demonstration : une app Shiny protegee par shinymanager.

Lancer :  shiny run examples/demo_app.py
Login de demo : user "admin" / mot de passe "admin123" (backend SQLite en clair, hache scrypt).

Reset de mot de passe en libre-service (1.1.1.1) active : le lien "Forgot password?" envoie un
mot de passe temporaire, ici simplement ECRIT DANS LA CONSOLE (pas de serveur mail). Les emails
sont dans la colonne `email` des credentials ; une base creee avant 1.1.1.1 n'en a pas : la
supprimer (demo_credentials.sqlite) pour la recreer.
"""

from pathlib import Path

from shiny import render, ui

from shinymanager import check_credentials, create_db, create_secure_app, settings, user_info

_DB = Path(__file__).parent / "demo_credentials.sqlite"
if not _DB.exists():
    create_db(
        [
            {"user": "admin", "password": "admin123", "admin": "TRUE", "email": "admin@ex.com"},
            {"user": "fanny", "password": "azerty12", "email": "fanny@ex.com"},
        ],
        str(_DB),
    )

# UI de l'application protegee (contenu ; secure_app l'enveloppe dans une page).
app_content = ui.div(
    ui.h2("Application protegee"),
    ui.p("Contenu visible uniquement apres authentification."),
    ui.output_text("hello"),
    # Equivalent du `res_auth <- secure_server(...)` + renderPrint du source R.
    ui.h4("Infos utilisateur"),
    ui.output_text_verbatim("auth_output"),
    id="protected-content",
)


def server(input, output, session):
    @render.text
    def hello():
        return "Bienvenue dans l'application securisee."

    @render.text
    def auth_output():
        return repr(user_info())


# Image affichee CENTREE au-dessus des champs du login, via le parametre `tags_top`
# (equivalent de `tag_img` du package R). Ici un SVG inline (data URI) : aucune dependance
# reseau. On peut tout aussi bien passer `ui.tags.img(src="/static/logo.png")`.
_LOGO = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='72' height='72' viewBox='0 0 24 24'>"
    "<path fill='%234582ec' d='M12 1a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h12a2 2 0 0 0"
    " 2-2v-9a2 2 0 0 0-2-2h-1V6a5 5 0 0 0-5-5zm3 8H9V6a3 3 0 0 1 6 0v3z'/></svg>"
)
_login_image = ui.tags.img(
    src=_LOGO, alt="Logo", width="72", height="72", style="margin-bottom:10px;"
)

# Equivalent de options("shinymanager.reset_password" = TRUE) de la demo R.
settings.set_option("reset_password", "username")
settings.set_option("pwd_validity", 1)


def send_mail(user, email, temp_password):
    # Demo : le "mail" est ecrit dans la console. En vrai : send_smtp_mail(...) ou tout autre
    # backend, qui doit lever une exception si l'envoi echoue.
    print(f"[MAIL] to={email} | user={user} | temp_pwd={temp_password}", flush=True)


#app = create_secure_app(
#    app_content,
#    check_credentials(str(_DB)),
#    server=server,
#    enable_admin=True,
#    choose_language=True,
#    tags_top=_login_image,  # <-- image centree en haut du panneau de login
#    send_mail=send_mail,
#)
app = create_secure_app(
    app_content,
    check_credentials(str(_DB)),
    server=server,
    enable_admin=True,
    tags_top=_login_image,
    tags_bottom=ui.tags.p("Support : support@quadratic-labs.com", style="text-align:center;"),
    background="linear-gradient(rgba(0,0,255,0.5), rgba(255,255,0,0.5))",
    status="success",
    head_auth=ui.tags.style(".panel-auth h3 { color: #4582ec; font-weight: bold; }"),
    send_mail=send_mail,
    choose_language=True
    
)