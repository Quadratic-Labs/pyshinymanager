"""Envoi d'un email par SMTP (source R/send-mail.R, 1.1.1.1).

Helper optionnel, equivalent de `send_smtp_mail()` du source (base sur le package R `emayili`,
en Suggests). Ici : bibliotheque standard (`smtplib` + `email.message`), donc aucune dependance.
Ce n'est qu'un raccourci : on peut l'appeler dans la fonction `send_mail` passee a
`secure_server` / `create_secure_app`, ou fournir sa propre fonction d'envoi (n'importe quel
backend).

Ecarts de signature : `from` est un mot reserve en Python -> parametre `from_` ; parametre
`timeout` ajoute (sans lui, un serveur SMTP muet bloquerait l'appel indefiniment) ; tentatives
`max_times` / `pause_base` reprises des parametres du meme nom de `emayili::server()`. Defauts
alignes sur la recommandation faite pour le package R : `timeout = 30`, `max_times = 1` (une
seule tentative, pas de nouvel essai sauf demande explicite).
"""

from __future__ import annotations

import smtplib
import ssl
import time
from email.message import EmailMessage


def send_smtp_mail(
    to: str,
    subject: str,
    body: str,
    from_: str,
    host: str,
    port: int = 587,
    username: str | None = None,
    password: str | None = None,
    html: bool = False,
    timeout: float = 30,
    max_times: int = 1,
    pause_base: float = 1,
) -> bool:
    """Envoie un email via un serveur SMTP.

    Port 465 : connexion TLS directe (SMTPS). Autres ports : STARTTLS si le serveur le propose.
    Authentification si `username` est fourni.

    Args:
        to: adresse du destinataire.
        subject: objet du mail.
        body: corps du mail (texte brut, ou HTML si `html=True`).
        from_: adresse de l'expediteur.
        host: hote du serveur SMTP.
        port: port du serveur SMTP (defaut 587).
        username: identifiant SMTP (optionnel).
        password: mot de passe SMTP (optionnel).
        html: le corps est-il du HTML ?
        timeout: delai maximal (secondes) des operations reseau SMTP (defaut 30).
        max_times: nombre maximal de tentatives (defaut 1 : pas de nouvel essai). Si > 1,
            seules les erreurs transitoires
            (timeout, connexion coupee, refus SMTP 4xx) sont retentees ; une erreur definitive
            (authentification, destinataire refuse, 5xx) est levee tout de suite.
        pause_base: pause (secondes) avant la 2e tentative, doublee ensuite (1 s, 2 s, ...).
            Duree maximale d'un appel ~ max_times * timeout + pauses (defaut ~ 30 s).

    Returns:
        True en cas de succes. Leve une exception si l'envoi echoue (comportement attendu par
        le reset : un echec ne doit pas changer le mot de passe).

    Example:
        ```python
        create_secure_app(
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
    """
    msg = EmailMessage()
    msg["From"] = from_
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body, subtype="html" if html else "plain")

    context = ssl.create_default_context()
    for attempt in range(1, max(1, max_times) + 1):
        try:
            if port == 465:
                smtp: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
            else:
                smtp = smtplib.SMTP(host, port, timeout=timeout)
            with smtp:
                if port != 465:
                    smtp.ehlo()
                    if smtp.has_extn("starttls"):
                        smtp.starttls(context=context)
                        smtp.ehlo()
                if username:
                    smtp.login(username, password or "")
                smtp.send_message(msg)
            return True
        except Exception as exc:
            if attempt >= max_times or not _is_transient(exc):
                raise
            time.sleep(pause_base * 2 ** (attempt - 1))
    return True  # pragma: no cover (boucle toujours conclue par return ou raise)


def _is_transient(exc: Exception) -> bool:
    """Erreur qui merite une nouvelle tentative : reseau (timeout, connexion) ou refus SMTP 4xx.

    Les erreurs definitives (authentification, destinataire refuse, certificat, 5xx) ne sont pas
    retentees : recommencer ne changerait rien et rallongerait l'attente.
    """
    if isinstance(exc, smtplib.SMTPResponseException):
        return 400 <= exc.smtp_code < 500
    if isinstance(exc, ssl.SSLError):
        return False
    return isinstance(exc, (smtplib.SMTPServerDisconnected, TimeoutError, ConnectionError))
