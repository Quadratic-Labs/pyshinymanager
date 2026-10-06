"""shinymanager — authentification pour Shiny for Python (portage du package R shinymanager)."""

from shinymanager.auth_module import auth_server, auth_ui
from shinymanager.check_credentials import check_credentials
from shinymanager.db import create_db, read_db, write_db
from shinymanager.db_sql import create_sql_db, load_config
from shinymanager.fab import fab_button
from shinymanager.i18n import get_labels, set_labels, use_language
from shinymanager.passwords import generate_pwd
from shinymanager.pwd_module import pwd_server, pwd_ui
from shinymanager.secure_app import (
    create_secure_app,
    secure_app,
    secure_server,
    user_info,
)
from shinymanager.send_mail import send_smtp_mail

__version__ = "0.1.0"

__all__ = [
    "auth_server",
    "auth_ui",
    "check_credentials",
    "create_db",
    "create_secure_app",
    "create_sql_db",
    "fab_button",
    "generate_pwd",
    "get_labels",
    "load_config",
    "pwd_server",
    "pwd_ui",
    "read_db",
    "secure_app",
    "secure_server",
    "send_smtp_mail",
    "set_labels",
    "use_language",
    "user_info",
    "write_db",
]
