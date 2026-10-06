# shinymanager (Python)

Authentification pour applications **Shiny for Python** : page de login, gestion des
utilisateurs, expiration de session, changement de mot de passe et panneau d'administration.
Portage du package R [shinymanager](https://github.com/datastorm-open/shinymanager) (GPL-3).

## Ce que fournit le paquet

- Une **page de login** qui protege l'application ; le jeton de session transite par un cookie
  `httponly` (jamais dans l'URL).
- La **gestion des mots de passe** : politique de complexite, changement force au premier login,
  expiration, verrouillage apres trop d'echecs.
- Un **panneau admin** (SQLite / SQL) : ajout / edition / suppression d'utilisateurs,
  reinitialisation de mot de passe, et un onglet de **statistiques de connexion** (2 graphes).
- Un **"mot de passe oublie"** optionnel sur la page de login (SQLite / SQL) : un mot de passe
  temporaire est envoye par mail, a changer au prochain login (voir ci-dessous).
- **12 langues** d'interface, surchargeables.

## Installation

```bash
python -m venv .venv
. .venv/Scripts/activate      # Windows ; sous Linux/macOS : source .venv/bin/activate
pip install -e ".[dev]"
```

Python >= 3.10. Licence GPL-3 (oeuvre derivee du source R).

## Demarrage rapide

```python
from pathlib import Path
from shiny import render, ui
from shinymanager import check_credentials, create_db, create_secure_app

# 1. Une base de credentials (a ne faire qu'une fois ; le mot de passe est hache a l'ecriture).
db = Path("credentials.sqlite")
if not db.exists():
    create_db(
        [
            {"user": "admin", "password": "admin123", "admin": "TRUE"},
            {"user": "fanny", "password": "azerty12"},
        ],
        str(db),
    )

# 2. L'UI et le serveur de VOTRE application.
app_ui = ui.page_fluid(ui.h2("Application protegee"), ui.output_text("hello"))

def server(input, output, session):
    @render.text
    def hello():
        return "Bienvenue."

# 3. On enveloppe le tout : login, cookie, admin, timeout sont geres automatiquement.
app = create_secure_app(
    app_ui,
    check_credentials(str(db)),
    server=server,
    enable_admin=True,     # onglet admin accessible aux comptes admin=TRUE
    timeout=15,            # minutes d'inactivite avant expiration
    language="fr",
)
```

```bash
shiny run app.py     # login de demo : admin / admin123
```

Le panneau admin s'ouvre via le bouton flottant (compte admin) ou l'URL `/?admin=true`.

## Mot de passe oublie (reset par mail)

Desactive par defaut. Il faut une colonne `email` dans les credentials, un backend SQLite / SQL et
une fonction d'envoi `send_mail(user, email, temp_password)` qui leve une exception en cas
d'echec. Le helper `send_smtp_mail` (bibliotheque standard, sans dependance) couvre le cas SMTP ;
toute autre fonction convient.

```python
import os

from shinymanager import create_secure_app, send_smtp_mail, settings

settings.set_option("reset_password", True)        # ou "username" : nom seul, mail vers l'email stocke
settings.set_option("reset_password_validity", 20)  # optionnel : mot de passe temporaire valable 20 min

app = create_secure_app(
    app_ui,
    check_credentials("credentials.sqlite"),
    send_mail=lambda user, email, temp_password: send_smtp_mail(
        to=email,
        subject="Password reset",
        body=f"Hello {user}, your temporary password is: {temp_password}",
        from_="no-reply@example.com",
        host="smtp.example.com",
        username="no-reply@example.com",
        password=os.environ["SMTP_PASSWORD"],
    ),
)
```

Le visiteur voit toujours le meme message, que le compte existe ou non ; le resultat reel est
journalise dans les logs admin (`Reset password`, `Reset password: unknown user`, ...). Autres
options : `email_column` (nom de la colonne email). En SQL, l'expiration necessite d'ajouter a la
main une colonne texte `temp_pwd_expire` a la table `pwd_mngt`.

## Backends de credentials

| Backend | Usage |
|---|---|
| liste de dictionnaires | tests, prototypage (en memoire) |
| SQLite (`create_db`) | production simple, un fichier |
| SQL externe (`create_sql_db` + config YAML) | Postgres, MySQL, MSSQL, Databricks (via SQLAlchemy) |

## Documentation

- [Documentation fonctionnelle](docs/DOC_shinymanager.md) — ce que fait le paquet,
  modele de donnees, flux d'authentification, limites.
- Version `0.1.0` = portage de shinymanager R 1.1.1.1 (numerotation independante du R). La
  tracabilite de migration (correspondance R -> Python, ecarts assumes) vit dans le workspace de
  migration, hors de ce depot.

## Qualite

```bash
ruff check src tests
ruff format --check src tests
pytest -m "not oracle"        # tests unitaires
pytest -m oracle              # tests differentiels R -> Python (voir ci-dessous)
```

Les tests `oracle` executent le package R comme reference : ils exigent `Rscript` (+ pkgload,
scrypt, jsonlite) ET le source R 1.1.1.1 dans `../source/shinymanager-1.1.1.1/` (relatif au
dossier parent de ce depot ; autre dossier sous `../source/` via la variable `SM_SOURCE`).

## Structure

- `src/shinymanager/` : le paquet (18 modules).
- `tools/oracle/` : harnais de validation differentielle (execute le R source comme reference).
- `tests/` : tests unitaires et differentiels.
