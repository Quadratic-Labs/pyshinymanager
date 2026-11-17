# shinymanager (Python)

> Simple and secure authentication mechanism for single **Shiny for Python** applications.
> Credentials can be stored in an encrypted SQLite database or in your own SQL database
> (PostgreSQL, MySQL, MS SQL Server, Databricks, ...). The application UI is not served until
> authentication is successful.

## Contents

- [Goal and origin of the project](#goal-and-origin-of-the-project)
- [Features](#features)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Credentials](#credentials)
- [Storage backends](#storage-backends)
- [Admin mode](#admin-mode)
- [Passwords](#passwords)
- [Languages](#languages)
- [Customization](#customization)
- [API reference](#api-reference)
- [Options](#options)
- [Differences with the R package](#differences-with-the-r-package)
- [Troubleshooting](#troubleshooting)
- [Security](#security)
- [Development](#development)
- [License](#license)

## Goal and origin of the project

**Goal: bring authentication to Shiny for Python.** [Shiny for Python](https://shiny.posit.co/py/)
(py-shiny) has no ready-to-use authentication tool: no official solution, no package offering
login, user management and an admin panel inside the application. This package fills that gap
with a complete, self-contained solution (no external identity provider needed): authentication
page, password management, account locking, admin panel, connection logs and self-service
password reset.

**Compatible with the R package.** It is a port of the R package
[shinymanager](https://github.com/datastorm-open/shinymanager), written by Benoit Thieurmel and
Victor Perrier (GPL-3), and it stays compatible with it: same features, same options, same
labels, and **the same SQLite database format** (a database created with shinymanager R works as
is in Python, and the other way round). A Shiny project can therefore move from R to Python while
keeping its configuration, its users and their passwords.

| Python package | R package |
|---|---|
| `0.1.0` | `1.2.1` |

Both projects are versioned independently. The port was validated against the R package itself:
the business logic (passwords, tokens, locking, logs, password reset, i18n, database format) is
covered by differential tests that run the R code and compare its results with the Python code.
Where Python behaves differently, it is a deliberate choice, listed in
[Differences with the R package](#differences-with-the-r-package).

## Features

- An **authentication page** protecting your application. The session token travels in an
  `httponly` cookie, never in the URL.
- **Password management**: complexity rules, forced change (first login, admin request, validity
  period), account locking after too many failures, self-service reset by email.
- An **admin panel** (SQLite / SQL): add, edit and delete users, reset passwords, force password
  changes, connection logs with statistics (users overview, security events) and downloads.
- **Cross-application** access control, account start / expiration dates.
- **12 languages**, customizable labels, customizable login page.

## Installation

Python >= 3.10. The repository is private: you need read access (configured `git` credentials or
a token).

```bash
pip install git+https://github.com/Quadratic-Labs/pyshinymanager.git
```

Optional extras:

| Extra | Installs | Needed for |
|---|---|---|
| `encryption` | `cryptography` | encrypted SQLite databases (`passphrase`) |
| `plotly` | `plotly` | interactive charts in the admin logs tab (otherwise simple static charts) |
| `databricks` | `databricks-sql-connector`, `databricks-sqlalchemy` | Databricks SQL backend |

```bash
pip install "shinymanager[encryption,plotly] @ git+https://github.com/Quadratic-Labs/pyshinymanager.git"
```

SQL backends also need the SQLAlchemy driver of your database (e.g. `psycopg` for PostgreSQL).

## Quick start

```python
# app.py
from shiny import render, ui

from shinymanager import check_credentials, create_secure_app, user_info

# define some basic credentials (in memory)
credentials = [
    {"user": "shiny", "password": "azerty", "start": "2019-04-15", "admin": "FALSE"},
    {"user": "shinymanager", "password": "12345", "expire": "2030-12-31", "admin": "TRUE"},
]

app_ui = ui.page_fluid(
    ui.h2("My secure application"),
    ui.output_text_verbatim("auth_output"),
)


def server(input, output, session):
    @render.text
    def auth_output():
        return repr(user_info())

    # your classic server logic


app = create_secure_app(app_ui, check_credentials(credentials), server=server)
```

```bash
shiny run app.py
```

`create_secure_app()` builds the whole application: the login page, the password change page,
the admin panel, the session timeout, and your UI and server once the user is authenticated.
Once logged in, a floating button lets users log out and, for admins, open the admin panel.

A demo is available in `examples/demo_app.py` (`admin` / `admin123`, `fanny` / `azerty12`; the
password reset "emails" are printed in the console):

```bash
shiny run examples/demo_app.py
```

## Credentials

Credentials are a table with one row per user (a list of dicts, or a dict of columns):

| Column | Required | Meaning |
|---|---|---|
| `user` | yes | user name (unique) |
| `password` | yes | password in clear: it is hashed (`scrypt`) when written to a database |
| `start` | no | first day of validity (`YYYY-MM-DD`); before it, "Your account has expired" |
| `expire` | no | last day of validity (`YYYY-MM-DD`) |
| `admin` | no | `"TRUE"` to give access to the admin panel |
| `applications` | no | applications the user may access, separated by `;` (see option `application`) |
| `email` | no | email address, used by the password reset (column name: option `email_column`) |
| any other | no | returned by `user_info()` and editable in the admin panel |

Values are stored as text, as in R (`"TRUE"` / `"FALSE"` for logical values).

## Storage backends

`check_credentials(db, passphrase=None)` returns the function used to authenticate users. The
type of `db` selects the backend:

| `db` | Backend | Admin panel, logs, locking, password reset |
|---|---|---|
| list of dicts / dict of columns | in memory | no |
| path ending with `.sqlite` | SQLite (optionally encrypted) | yes |
| path ending with `.yml` / `.yaml`, or a config dict | your SQL database | yes |
| your own function | anything | no |

### Secure SQLite database

Store your credentials in a SQLite database protected with a symmetric AES encryption, and
password hashing using `scrypt`. The format is **the same as the R package**: a database created
with shinymanager R can be used as is in Python (same passphrase, same passwords), and the other
way round.

```python
import os

from shinymanager import check_credentials, create_db, create_secure_app

# Init the database (once). Passwords are automatically hashed.
create_db(
    [
        {"user": "shiny", "password": "azerty", "admin": "FALSE"},
        {"user": "shinymanager", "password": "12345", "admin": "TRUE"},
    ],
    "path/to/database.sqlite",  # will be created
    passphrase=os.environ["SHINYMANAGER_KEY"],
)

# Enable the admin mode or not
app = create_secure_app(
    app_ui,
    check_credentials("path/to/database.sqlite", passphrase=os.environ["SHINYMANAGER_KEY"]),
    server=server,
    enable_admin=True,
)
```

Each table is stored like `write_db_encrypt()` does in R: the R serialized data.frame, encrypted
with AES-256-CBC using the SHA-256 of the passphrase (requires the `encryption` extra). Without
`passphrase`, tables are stored in clear (as in R); passwords are hashed anyway. Keep the
passphrase out of your code (environment variable, secret manager...).

Do not run an R application and a Python application on the **same** database at the same time:
both rewrite whole tables, so concurrent writes would overwrite each other. Switching from one to
the other is fine.

### Use your own SQL database

The configuration is a declarative YAML file (or the equivalent dict). SQL is generated by
SQLAlchemy, so any SQLAlchemy dialect works. Secrets are read from environment variables with
`${VAR}`:

```yaml
# sql_config.yml
connection:
  drivername: postgresql+psycopg   # SQLAlchemy dialect + driver
  host: localhost
  port: 5432
  database: shiny
  username: ${PG_USER}
  password: ${PG_PASSWORD}
  # or directly: url: postgresql+psycopg://user:pwd@localhost:5432/shiny
tables:                    # optional, these are the default names
  credentials: credentials
  pwd_mngt: pwd_mngt
  logs: logs
connect_every_request: false   # optional: true = new connection for each request (no pool)
```

```python
from shinymanager import check_credentials, create_secure_app, create_sql_db

# Create the tables and insert the credentials (once)
create_sql_db(
    [{"user": "shiny", "password": "azerty"}, {"user": "shinymanager", "password": "12345"}],
    "sql_config.yml",
)

app = create_secure_app(app_ui, check_credentials("sql_config.yml"), server=server)
```

Passwords are always hashed. SQL tables are not encrypted by shinymanager (as in R): rely on your
database security.

### Use your own function

You can also use your own authentication function, for example to check against an internal
database. It must take two arguments `user` and `password`, and return a `dict` with at least
`result` (`True` to authorize access, or `False`) and `user_info` (everything you want to
retrieve about the user in the app, `None` when the user is unknown):

```python
def my_custom_check_creds(user: str, password: str) -> dict:
    if user == "test" and password == "123":  # replace with your own lookup
        return {"result": True, "user_info": {"user": user, "something": 123}}
    return {"result": False, "user_info": None}


app = create_secure_app(app_ui, my_custom_check_creds, server=server)
```

The returned dict may also contain `expired` and `authorized` (booleans) to display the matching
messages.

## Admin mode

With a SQLite or SQL backend and `enable_admin=True`, users with `admin = "TRUE"` get an admin
panel (floating button "Administrator mode", or URL `/?admin=true`):

- **Users**: add, edit (one or several users), remove; download the users file.
- **Passwords**: ask a user to change their password, reset a password (a temporary password is
  generated and the user must change it at next login).
- **Logs**: connections per user and per day over a period, filtered by user and application;
  download the logs and the database.

Options `show_logs`, `download`, `auto_sqlite_reader` / `auto_sql_reader` and the arguments
`inputs_list` / `max_users` customize it.

## Passwords

### Validity period and failure limit

```python
from shinymanager import settings

settings.set_option("pwd_validity", 90)       # force a change when the last one is more than 90 days old
settings.set_option("pwd_failure_limit", 5)   # lock the account after 5 wrong passwords
```

A locked account is unlocked by an admin password reset (or by a self-service reset, see
`reset_password_unlock` below). The attempt reaching the limit locks the account right away
(logged as `Account locked`), and a locked account shows "Your account is locked" even with a
wrong password. When a limit is set, the admin table shows a *Locked* column.

### Custom password rules

The default rule requires at least one digit, one lowercase, one uppercase and 6 characters.

```python
import re


def validate_pwd_custom(pwd: str) -> bool:
    rules = [r"[0-9]", r"[a-z]", r"[A-Z]", r"[^\w\s]", r".{8,}"]
    return all(re.search(rule, pwd) for rule in rules)


app = create_secure_app(app_ui, check, server=server, validate_pwd=validate_pwd_custom)
```

### Self-service password reset (by email)

With `settings.set_option("reset_password", True)`, a *"Forgot password?"* link is added on the
authentication page. A user can reset the password by entering a valid username **and** its
associated email: a temporary password is generated, sent by email, and the user is forced to
change it on next login. A generic message is displayed as soon as the form is sent, to avoid
account enumeration, and the response always takes at least 5 seconds whatever the outcome.
Known limits (as in R): an account that can't reset its password gets a dedicated message, and a
failed sending or saving shows an error message; both reveal that the account exists. No reset is
done for an expired account or an account not authorized for the application. Each attempt is
recorded in the admin logs (`Reset password`, `Reset password: <reason>`) and errors are also
raised as Python warnings. The temporary password replaces the current one as soon as the email
is sent: anyone who knows a username (and its email with `True`) can invalidate the password of
this account, so prefer `True` to `"username"`.

- `settings.set_option("reset_password", "username")`: the form only asks for a username and
  the temporary password is sent to the email stored for that user.
- `settings.set_option("reset_password_cooldown", 60)`: minimal time, in **seconds**, before a
  user who received a reset email can get a new one (default `60`, `0` for no limit). A request
  within this time gets the generic message, sends nothing and is logged as
  `Reset password: too soon`. Kept in memory: lost on restart, and each worker has its own.
- `settings.set_option("reset_password_max", 3)`: number of resets a user can request in a row
  (default `inf`). The counter is reset on the next successful login with a non temporary
  password, or by an admin reset. It is stored in a `n_user_reset` column of `pwd_mngt`: created
  automatically with SQLite, to be added as an integer column with a SQL backend.
- `settings.set_option("reset_password_unlock", False)`: an account locked by
  `pwd_failure_limit` can only be unlocked by an admin (default `True`: a reset unlocks it).
- `settings.set_option("reset_password_validity", 20)`: the temporary password is valid for 20
  minutes (default: no expiration). Admin-generated passwords are not concerned, and an admin
  reset cancels a pending emailed password. The expiration is stored as text (UTC) in a
  `temp_pwd_expire` column of the `pwd_mngt` table: created automatically with SQLite, to be
  added as a text column (e.g. `varchar(19)`) with a SQL backend.

It requires an `email` column, a SQLite / SQL backend and a `send_mail(user, email,
temp_password)` function. **The function must raise an exception when sending fails**: the
password is changed only after a successful send. Use your own function, or the helper
`send_smtp_mail()` (standard library, no extra dependency):

```python
import os

from shinymanager import create_secure_app, send_smtp_mail, settings

settings.set_option("reset_password", True)
settings.set_option("reset_password_validity", 20)


def send_mail(user, email, temp_password):
    validity = settings.get_option("reset_password_validity")
    send_smtp_mail(
        to=email,
        from_="no-reply@example.com",
        subject="Password reset",
        body=f"Hello {user}, your temporary password is: {temp_password}\n"
        f"It is valid for {validity} minutes.",
        host="smtp.example.com",
        port=587,
        username="no-reply@example.com",
        password=os.environ["SMTP_PASSWORD"],
    )


app = create_secure_app(app_ui, check, server=server, send_mail=send_mail)
```

The new password and its `pwd_mngt` state are saved in a single transaction, cancelled if the
password or the email of the account changed while the email was being sent. With a SQL backend,
`temp_pwd_expire` must be a text column: otherwise the reset is refused before sending.

The *"Forgot password?"* link is hidden when the reset can't work (no `send_mail`, credentials
in memory, or no email column in the credentials), with a warning giving the reason (once per
process and per reason). A warning is also emitted at startup if `reset_password` is enabled
without `send_mail`.

## Languages

English (`en`), French (`fr`), Portuguese (`pt-BR`), Spanish (`es`), German (`de`), Polish (`pl`),
Japanese (`ja`), Greek (`el`), Indonesian (`id`), Chinese (`zh-CN`), Norwegian (`no`), Italian
(`it`).

```python
from shinymanager import set_labels

app = create_secure_app(app_ui, check, language="fr", choose_language=True)

# change some labels (globally, for a language)
set_labels("en", **{"Please authenticate": "You have to login"})
```

The language chosen on the login page is kept for the whole session (application, admin panel).

## Customization

```python
app = create_secure_app(
    app_ui,
    check,
    server=server,
    tags_top=ui.tags.img(src="https://example.com/logo.png", width="100"),
    tags_bottom=ui.tags.p("Contact: support@example.com", style="text-align:center;"),
    background="linear-gradient(rgba(0,0,255,0.5), rgba(255,255,0,0.5))",
    status="success",
    head_auth=ui.tags.style(".panel-auth h3 { font-weight: bold; }"),
)
```

The login page is plain HTML posted to HTTP routes (this is what allows the `httponly` cookie):
it can hold any static content (images, text, links, CSS, JavaScript), but no reactive output.
Useful CSS hooks: `.panel-auth`, `#auth-mod`, `#auth-go_auth`, `#auth-show_reset_pwd`.

## API reference

### `create_secure_app(ui_def, check_credentials, *, ...)`

Builds the secured py-shiny `App`. Equivalent of `shinyApp(secure_app(ui), server)` with
`secure_server()` in R.

| Argument | Default | Description |
|---|---|---|
| `ui_def` | | UI of your application (tags, or a function of the request) |
| `check_credentials` | | function `(user, password) -> dict`, see `check_credentials()` |
| `server` | `None` | server function of your application |
| `enable_admin` | `False` | enable the admin panel (SQLite / SQL backends) |
| `timeout` | `15` | inactivity timeout in minutes (`0` = never) |
| `validate_pwd` | `None` | function `(pwd) -> bool` for new passwords (default rule otherwise) |
| `language` | `"en"` | default language |
| `fab_position` | `"bottom-right"` | floating button position: `bottom-right`, `top-right`, `bottom-left`, `top-left`, `none` |
| `inputs_list` | `None` | custom inputs for columns in the admin forms (see below) |
| `max_users` | `None` | maximum number of users (admin panel) |
| `file_encoding` | `""` | encoding of the downloaded logs CSV |
| `send_mail` | `None` | function `(user, email, temp_password)` for the password reset |
| `choose_language` | `None` | language selector on the login page: `True` (all), a code, a list of codes |
| `status` | `"primary"` | Bootstrap status of the login panel and button |
| `tags_top` | `None` | content above the login title (logo...) |
| `tags_bottom` | `None` | content below the login form |
| `background` | `None` | CSS background of the login page |
| `head_auth` | `None` | tags added to the `<head>` of the login and password pages |
| `theme` | `None` | Bootstrap theme of the pages (py-shiny `theme=`) |
| `tag_img` | `None` | image of the password change page |

`inputs_list` maps a column to a py-shiny input function and its arguments, as in R:

```python
inputs_list = {
    "group": {"fun": ui.input_select, "args": {"choices": ["a", "b"], "multiple": True}},
}
```

`id`, `label`, `value` / `selected` and `width` are filled automatically. Multiple values are
stored separated by `;` and returned as a list by `user_info()`.

### `check_credentials(db, passphrase=None)`

Returns the authentication function for a backend (see [Storage backends](#storage-backends)).
The function returns `{"result", "expired", "authorized", "user_info"}`. With SQLite / SQL, the
credentials are read again at each login attempt. A missing `.sqlite` / `.yml` file raises a
`FileNotFoundError` naming the working directory (instead of creating an empty database).

### `user_info(session=None, inputs_list=None)`

To call in your server code: returns the information of the authenticated user (all credentials
columns except the password), or `{}`.

### SQLite: `create_db`, `read_db`, `write_db`

| Function | Description |
|---|---|
| `create_db(credentials_data, sqlite_path, passphrase=None)` | creates the database (`credentials`, `pwd_mngt`, `logs` tables); fails on duplicated users or missing `user` / `password` |
| `read_db(conn, name="credentials", passphrase=None)` | reads a table as a list of dicts (R: `read_db_decrypt`) |
| `write_db(conn, value, name="credentials", passphrase=None)` | overwrites a table; passwords not yet hashed are hashed (R: `write_db_encrypt`) |

`conn` is a path or a `sqlite3.Connection`.

### SQL: `create_sql_db`, `load_config`

| Function | Description |
|---|---|
| `create_sql_db(credentials_data, conf)` | creates the tables and inserts the credentials; `conf` is a YAML path or a dict |
| `load_config(conf)` | loads a YAML configuration (returns a dict as is) |

### `send_smtp_mail(to, subject, body, from_, host, ...)`

| Argument | Default | Description |
|---|---|---|
| `to`, `subject`, `body`, `from_` | | message (`from_`: `from` is reserved in Python) |
| `host`, `port` | `587` | SMTP server; 465 = TLS, otherwise STARTTLS when offered |
| `username`, `password` | `None` | SMTP authentication |
| `html` | `False` | the body is HTML |
| `timeout` | `30` | network timeout in seconds |
| `max_times` | `1` | number of attempts; with more than 1, only transient errors are retried |
| `pause_base` | `1` | pause before a new attempt, doubled each time (seconds) |

Raises an exception when sending fails.

### Labels: `use_language`, `get_labels`, `set_labels`

| Function | Description |
|---|---|
| `use_language(lan="en")` | returns a `Language` object: `get(label)`, `get_all()`, `get_language()`, `get_language_registered()`, `get_DT()`, `get_dateInput()`, `set_language(lan)` |
| `get_labels(language="en")` | all labels of a language (dict) |
| `set_labels(language, {...})` or `set_labels(language, **labels)` | replaces labels of a language |

### Other functions

| Function | Description |
|---|---|
| `generate_pwd(n=1)` | random password(s) of 8 characters |
| `fab_button(*actions, position, toggle, animation, input_id, label)` | floating action button; `actions` are dicts `{"id", "label", "icon"}` |
| `secure_app(ui_def, ...)`, `secure_server(check_credentials, ...)` | lower-level building blocks of `create_secure_app()` (always build the app with `create_secure_app()`: the login routes are installed there) |
| `auth_ui(id, ...)`, `auth_server(id, check_credentials, language)` | standalone reactive login module (without the cookie flow) |
| `pwd_ui(id, ...)`, `pwd_server(id, user, update_pwd, validate_pwd, language)` | standalone password change module |

### Shiny inputs

Two inputs are available in your server code:

```python
from shiny import reactive


@reactive.effect
def _():
    print(input.shinymanager_where())     # "authentication", "password", "admin", "application"
    print(input.shinymanager_language())
```

## Options

The R options `options("shinymanager.<name>")` are set in Python with
`settings.set_option("<name>", value)` (read with `settings.get_option`, restore the defaults with
`settings.reset_options()`), before creating the application.

| Option | Default | Effect |
|---|---|---|
| `pwd_validity` | `inf` | password validity in days |
| `pwd_failure_limit` | `inf` | wrong passwords before the account is locked |
| `reset_password` | `False` | self-service reset: `True` (username + email) or `"username"` |
| `reset_password_validity` | `None` | validity of the emailed temporary password, in minutes |
| `reset_password_cooldown` | `60` | seconds before a user can get a new reset email (`0`: no limit) |
| `reset_password_max` | `inf` | self-service resets allowed in a row |
| `reset_password_unlock` | `True` | a self-service reset unlocks a locked account |
| `email_column` | `"email"` | credentials column holding the email |
| `application` | current directory name | application name, checked against the `applications` column |
| `write_logs` | `True` | write connection logs |
| `show_logs` | `True` | show the logs tab in the admin panel |
| `download` | `["db", "logs", "users"]` | download buttons of the admin panel |
| `auto_sqlite_reader` | `1000` | refresh period of the admin tables with SQLite (ms) |
| `auto_sql_reader` | `inf` | refresh period of the admin tables with SQL (ms) |

## Differences with the R package

Features, options, labels and database format follow the R package. The differences below are
deliberate.

**Architecture**
- `create_secure_app()` assembles the application; in R you wrap the UI with `secure_app()` and
  call `secure_server()` in your server.
- The session token is stored in an `httponly`, `samesite=strict` cookie set by HTTP routes
  (`/sm-login`, `/sm-logout`, `/sm-set-language`, `/sm-reset-password`). In R it travels in the
  URL. Consequences: no `keep_token` argument (a page reload keeps the session, and the URL
  cannot be shared to reuse it), and the login page is static HTML (no reactive content).
- The credentials are read again at each login attempt, so a password change is effective
  immediately.
- The password reset email is sent in a background thread, so the server is not blocked.

**Security**
- Account locking and "new password different from the old one" fail closed: a database read
  error refuses access (R lets it through).
- The session timeout is refreshed by real user activity (mouse, keyboard, scroll), not by
  outputs being recalculated (in R, an auto-refreshing output keeps a session alive forever).
- A missing or wrong passphrase gives a clear error message.
- `enable_admin = False` is also enforced on the server: the admin module is never started.
- The reset cooldown is checked under a lock, as reset requests run in parallel threads.

**Configuration**
- Options are set with `settings.set_option()` instead of `options()`.
- The SQL configuration is declarative: no R/Python code (`!expr`) and no SQL templates in the
  YAML file; SQL is generated by SQLAlchemy (Databricks included, through its dialect).
- A warning is emitted when `reset_password` is enabled without `send_mail`.

**Interface**
- Admin tables are plain HTML tables (DataTables in R), with the same actions.
- Logs charts use plotly when installed, simple static charts otherwise (billboarder in R). The
  users overview of the logs tab is a plain HTML table, and without plotly the security events
  per day are shown as a table.
- A reset error is shown in the reset panel (a popup in R).
- Icons come from `faicons` (Font Awesome 6).
- Not ported: the waiting spinner during database writes, and the translation of the whole page
  when the language changes (labels of the login components are translated).

**Small behaviour differences**
- `send_smtp_mail()` uses the Python standard library instead of `emayili`, with `from_`,
  `timeout` and `max_times` arguments.
- Empty `start` / `expire` values are not replaced by default dates in `user_info()`.
- The DataTables translation of Chinese (`zh-CN`), missing in R, is provided.
- On the Python login page, the reset form is cleared after sending and closes when the language
  changes.

## Troubleshooting

The application works fine without `shinymanager` but you have trouble with it: the UI is not
served until authentication is successful, so some inputs may not exist yet when your reactive
code runs. Use `req()` in your reactive functions to validate the inputs:

```python
from shiny import req


@render.text
def txt():
    req(input.my_input())
    return input.my_input()
```

## Security

- Passwords are hashed with `scrypt` (format compatible with the R package).
- SQLite tables are encrypted with AES-256 when a passphrase is given; without it, restrict
  access to the database file.
- Session tokens are random (HMAC-SHA256), kept in server memory, never in the URL; the cookie
  cannot be read by JavaScript, and is `Secure` when the login is done over HTTPS. Behind a
  reverse proxy terminating TLS, run uvicorn with `--proxy-headers` (and `--forwarded-allow-ips`)
  so that the scheme is seen as `https`. Restarting the server logs everybody out.
- The admin and logs modules are only started for a valid admin session, and every admin
  change or download checks the session again.
- The password reset shows a generic message, except for the known limits described in
  [Self-service password reset](#self-service-password-reset-by-email).

If you have a concern about the methods used, please open an issue.

## Development

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows; on Linux / macOS: source .venv/bin/activate
pip install -e ".[encryption,plotly]"
pip install pytest pytest-cov ruff httpx

ruff check src tests
ruff format --check src tests
pytest -m "not oracle"        # unit tests
pytest -m oracle              # differential tests against the R package
```

The `oracle` tests run the R package as a reference: they require `Rscript` (with `pkgload`,
`scrypt`, `jsonlite`, `openssl`) and the R sources, located with the `SM_SOURCE_PATH`
environment variable (absolute path), or by default in `../source/shinymanager/` next to this
repository (a clone of the R repository, at the version listed above). They are skipped when R
or the sources are not found.

- `src/shinymanager/`: the package.
- `tests/`: unit and differential tests.
- `tools/oracle/`: R reference scripts of the differential tests.
- `examples/`: demo application.
- `docs/DOC_shinymanager.md`: functional documentation.

## License

GPL-3.0-or-later, derived work of the R package
[shinymanager](https://github.com/datastorm-open/shinymanager) by Benoit Thieurmel and Victor
Perrier.
