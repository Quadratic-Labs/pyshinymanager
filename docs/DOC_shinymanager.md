# Documentation - shinymanager (Python)

> **Context:** authentication layer for Shiny for Python applications.
> **Audience:** integrators and application owners who want to understand what the package
> does, without reading the code.
> **Last updated:** 2026-11-17

---

## 1. Data sources

The package does not produce any analysis: it manages **accounts** and **connections**. It relies
on a credentials database, in one of the following formats, chosen by the integrator.

| Source | Type | Description | Notes |
|---|---|---|---|
| list of accounts | in memory | accounts given directly in the code | prototyping / tests only |
| `credentials.sqlite` | SQLite file | local accounts database | simple production setup, one file to protect |
| external SQL database | Postgres / MySQL / MSSQL / Databricks | company database, described by a configuration file | secrets through environment variables |

Each database holds three tables: **credentials** (the accounts: user name, hashed password,
admin rights, start and end of validity), **pwd_mngt** (the password state: change to force,
number of failures, date of the last change) and **logs** (the connection history: who, when,
from which application, logout).

---

## 2. How it works

### 2.1 Protecting the application
The integrator wraps their application. As long as the user is not authenticated, they only see
a login page. Once logged in, they reach the application; the connection information is kept in a
secure cookie, which the page code cannot read (never in the address).

### 2.2 Password management
A password must follow a complexity policy (at least one digit, one lowercase letter, one
uppercase letter, six characters). An account can be forced to change its password at the next
login. A password can expire after a given period. After too many failures, the account is locked
at the attempt that reaches the limit, and the user is told so. Passwords are never stored in
clear text: they are hashed.

As an option, a "Forgot password?" link on the login page lets a user receive a temporary
password by email (by giving their user name and email, or only their user name, depending on the
setting). They must change it at their next login, and this password can have a limited validity.
The page shows a generic message, so as not to reveal which accounts exist; the only exceptions
(as in the R package): a blocked account (locked, too many requests) gets a dedicated message, and
a sending or saving error is reported. An expired or unauthorized account receives nothing. The
same user does not receive two emails less than one minute apart (configurable), the number of
requests in a row can be limited, and unlocking can be reserved to the administrator. Every request
is recorded in the admin logs. Sending the email is delegated to a function provided by the
application (a ready-to-use SMTP sender is included); the link is hidden, with a warning, when no
function is provided, when the accounts are not in a database, or when the email column is
missing.

### 2.3 Session expiration
A session inactive beyond a configurable delay (15 minutes by default) expires automatically: the
user is sent back to the login page. Real activity (mouse, keyboard, scrolling) resets the timer.

### 2.4 Administration
Accounts flagged as administrators reach a dedicated panel: list of users, add, edit, delete,
password reset, and a connection statistics tab. The panel is only active on the server for a
valid administrator session: every operation and every download checks the session again.

---

## 3. What you get

| Output | Format | Description |
|---|---|---|
| Login page | screen | centered panel, 12 languages, precise error message (credentials, locked, expired or unauthorized account) |
| Password change screen | screen | required at the first login or after a reset |
| Admin panel - Users | interactive table | add / edit / delete / reset, with safeguards |
| Admin panel - Statistics | charts + table | connections per user (bars) and per day (area), per-user overview (last connection, failures, resets, lock), security events (counters and chart per day) |
| Exports | CSV / SQLite files | users table, SQLite database, connection log |
| Logged-in user information | data | available to the application (user name, rights, authorized applications) |

---

## 4. Design choices

> This section explains *why* some decisions were made. They deliberately differ from the
> original R package; details are in the README, section "Differences with the R package".

- **Cookie rather than address (URL)**: the session token no longer travels in the page address
  but in a secure cookie. It is safer (the token cannot be exposed or copied through a link).
- **Deny by default when in doubt**: if the system cannot check the state of an account (read
  error), it denies access rather than letting the user in. The R package did the opposite.
- **Encrypted SQLite database, in the same format as the R package**: with a passphrase, each
  table is encrypted (AES-256) exactly as in the original package; without it, the tables are in
  clear text. Passwords are hashed in every case.
- **Declarative configuration**: the connection to an external SQL database is described in a
  simple configuration file (no executable code in the configuration, for security reasons).

---

## 5. Limits and points of attention

- **Company SQL databases**: the behavior is validated on SQLite; Postgres, MySQL, MSSQL and
  Databricks rely on the same mechanism but have not been tried on a real infrastructure (table
  creation is checked for MySQL, MSSQL and Postgres, without a server).
- **Compatibility with an existing R database**: complete. A SQLite database created by the R
  package (encrypted or not) works as is in Python, with the same passphrase and the same
  passwords, and the other way round. To avoid: running an R application and a Python
  application on the same database at the same time (simultaneous writes would overwrite each
  other).
- **Database without a passphrase**: its tables can be read by anyone who can access the file;
  restrict access to the file, or use a passphrase.
- **Minor cosmetic differences, accepted**: a few visual details of the original package are not
  reproduced exactly (waiting indicator during a write, instant re-translation of the whole page
  when the language changes). No effect on security or behavior.
- **Forgot password**: the email is sent before the password is changed; if sending fails,
  nothing changes, and the save is done in a single operation (all or nothing), cancelled if the
  account was modified in the meantime. The response always takes at least 5 seconds, so that the
  response time does not reveal whether an account exists (unless sending takes longer). The
  temporary password replaces the current one as soon as it is sent: knowing a user name is enough
  to invalidate its password, hence the recommendation of the "user name + email" mode. With a SQL
  database, the validity period and the request limit require adding columns to the passwords
  table.
- **Optional dependencies**: the interactive charts of the statistics tab need plotly
  (`pip install "shinymanager[plotly]"`); without it, the same charts are shown in a simple,
  non-interactive version. Encrypted databases need `shinymanager[encryption]`.
