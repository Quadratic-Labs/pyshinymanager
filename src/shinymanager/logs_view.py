"""Onglet Logs du panneau admin (source R/modules-logs.R).

Reconstruction FIDELE de l'onglet Logs du source :
filtres (utilisateur, periode, raccourcis, application), DEUX graphes de connexions, puis
une vue par utilisateur et les evenements de securite (compteurs + graphe par jour).

Ecarts assumes vs le source :
  - billboarder (billboard.js) -> plotly OPTIONNEL :
    avec plotly installe (`shinymanager[plotly]`), graphes interactifs rendus en HTML direct (sans
    shinywidgets ni la chaine Jupyter) ; sans plotly, memes graphes en HTML/SVG statique. Meme
    lecture dans les deux cas (barres horizontales triees, aire en escalier, couleur du source).
  - `conditionalPanel(output$print_app_input_js)` (+ outputOptions suspendWhenHidden=FALSE, sans
    equivalent py-shiny) -> le selecteur d'application est rendu par un output_ui, present
    uniquement si plusieurs applications existent. Meme regle d'affichage.
  - la deduplication des vieux logs passe par `logs.read_logs` (dedup corrigee).
  - vue par utilisateur : DT -> table HTML (comme l'admin), triee sur la derniere connexion ;
    graphe des evenements de securite sans plotly : table jour x categorie.

Quirks du source REPRODUITS :
  - le label du selecteur d'application est "Application :" EN DUR (non traduit) ;
  - le filtre "toutes applications" repose sur la valeur "All applications", absente du
    dictionnaire i18n -> texte brut ;
  - seuls les logs `status == "Success"` alimentent les graphes de connexions.

La preparation des donnees est faite par des fonctions PURES (`filter_logs`, `count_by_user`,
`count_by_day`, `users_overview`, `security_kpi_counts`, `security_events_per_day`), testables sans
session ; seul le cablage reactif est `# pragma: no cover`.
"""

from __future__ import annotations

import csv
import html
import importlib.util
import io
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from faicons import icon_svg
from htmltools import HTMLDependency
from shiny import Inputs, Outputs, Session, module, reactive, render, req, ui

from shinymanager import pwd_lifecycle, settings
from shinymanager.admin import list_users
from shinymanager.i18n import use_language
from shinymanager.logs import read_logs
from shinymanager.tokens import _tok

#: Couleur des deux graphes (source : bb_bar_color_manual / bb_colors_manual).
_COLOR = "#4582ec"
#: Valeur "toutes applications" du source (absente du dictionnaire i18n : texte brut).
ALL_APPS = "All applications"
#: plotly est optionnel (extra `plotly`) : sans lui, graphes HTML/SVG statiques.
HAS_PLOTLY = importlib.util.find_spec("plotly") is not None


def _plotly_dependency() -> HTMLDependency:
    """plotly.js embarque par le paquet plotly, servi une fois (pas de CDN, pas de Jupyter)."""
    import plotly

    return HTMLDependency(
        "plotly",
        plotly.__version__,
        source={"subdir": str(Path(plotly.__file__).parent / "package_data")},
        script={"src": "plotly.min.js"},
    )


def _plotly_html(fig: Any, height: int) -> ui.TagList:
    """Rend une figure plotly en HTML direct (div + script) ; plotly.js vient de la dependance."""
    fig.update_layout(height=height)
    snippet = fig.to_html(full_html=False, include_plotlyjs=False, config={"displaylogo": False})
    return ui.TagList(_plotly_dependency(), ui.HTML(snippet))


def _bar_html(data: list[tuple[str, int]], axis_title: str) -> ui.Tag:
    """Graphe 1 sans plotly : barres horizontales en HTML/CSS, maximum en haut."""
    top = max((n for _, n in data), default=0) or 1
    rows = [
        ui.tags.div(
            ui.tags.div(user, class_="sm-bar-label", title=user),
            ui.tags.div(
                ui.tags.div(class_="sm-bar", style=f"width:{100 * n / top:.1f}%;"),
                class_="sm-bar-track",
            ),
            ui.tags.div(str(n), class_="sm-bar-value"),
            class_="sm-bar-row",
        )
        for user, n in data
    ]
    style = ui.tags.style(
        ".sm-bar-row{display:flex;align-items:center;margin:2px 0;}"
        ".sm-bar-label{width:20%;text-align:right;padding-right:8px;overflow:hidden;"
        "text-overflow:ellipsis;white-space:nowrap;}"
        ".sm-bar-track{flex:1;background:#f4f4f4;height:18px;}"
        f".sm-bar{{background:{_COLOR};height:100%;}}"
        ".sm-bar-value{width:3.5em;padding-left:6px;}"
    )
    return ui.tags.div(
        style, *rows, ui.tags.div(axis_title, style="text-align:center;color:#666;margin-top:6px;")
    )


def _step_area_svg(data: list[tuple[date, int]], axis_title: str) -> ui.Tag:
    """Graphe 2 sans plotly : aire en escalier par jour en SVG (axes : 1er/dernier jour, max)."""
    width, height, pad = 800.0, 300.0, 40.0
    n = len(data)
    top = max((c for _, c in data), default=0) or 1
    step = (width - 2 * pad) / max(n, 1)

    def y(count: int) -> float:
        return height - pad - (height - 2 * pad) * count / top

    points = [f"{pad:.1f},{height - pad:.1f}"]
    for i, (_, count) in enumerate(data):
        x0, x1 = pad + i * step, pad + (i + 1) * step
        points += [f"{x0:.1f},{y(count):.1f}", f"{x1:.1f},{y(count):.1f}"]
    points.append(f"{pad + n * step:.1f},{height - pad:.1f}")
    first = data[0][0].isoformat() if data else ""
    last = data[-1][0].isoformat() if data else ""
    svg = (
        f'<svg viewBox="0 0 {width:.0f} {height:.0f}" width="100%" role="img" '
        f'aria-label="{html.escape(axis_title)}">'
        f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" '
        'stroke="#ccc"/>'
        f'<polygon points="{" ".join(points)}" fill="{_COLOR}" fill-opacity="0.6" '
        f'stroke="{_COLOR}"/>'
        f'<text x="{pad}" y="{height - pad + 18}" font-size="12">{first}</text>'
        f'<text x="{width - pad}" y="{height - pad + 18}" font-size="12" '
        f'text-anchor="end">{last}</text>'
        f'<text x="{pad - 6}" y="{pad + 4}" font-size="12" text-anchor="end">{top}</text>'
        f'<text x="{pad - 6}" y="{height - pad + 4}" font-size="12" text-anchor="end">0</text>'
        f'<text x="12" y="{height / 2}" font-size="12" text-anchor="middle" '
        f'transform="rotate(-90 12 {height / 2})">{html.escape(axis_title)}</text>'
        "</svg>"
    )
    return ui.tags.div(ui.HTML(svg))


def _day(row: dict[str, Any]) -> str:
    """Jour d'un log (10 premiers caracteres de server_connected, comme le substr du source)."""
    return str(row.get("server_connected") or "")[:10]


def _columns(rows: list[dict[str, Any]]) -> list[str]:
    """Colonnes dans l'ordre d'apparition (ordre des colonnes du data.frame R)."""
    columns: list[str] = []
    for r in rows:
        for c in r:
            if c not in columns:
                columns.append(c)
    return columns


def success_logs() -> list[dict[str, Any]]:
    """Logs de connexion reussie, dedupliques (source : filtre status == "Success")."""
    return read_logs(status="Success")


def all_logs() -> list[dict[str, Any]]:
    """Tous les logs (echecs compris), dedupliques sans fusionner les echecs sans token."""
    return read_logs(keep_untokened=True)


def app_choices(rows: list[dict[str, Any]]) -> list[str]:
    """Applications des logs (tous statuts) puis l'application courante, sans doublon.

    Corrige `unique(x, get_appname())` d'un ancien source, ou le 2e argument etait `incomparables`.
    """
    seen: list[str] = []
    for app in [*(r.get("app") for r in rows), settings.get_option("application")]:
        if app and str(app) not in seen:
            seen.append(str(app))  # ordre d'apparition, comme unique() en R
    return seen


# Evenements de securite (modules-logs.R)
# ----------------------------------------
# Statuts d'echec ecrits par save_logs_failed() / save_reset_logs(), regroupes en categories
# (les noms sont aussi les cles i18n). None pour les autres statuts (connexions reussies).

SECURITY_CATEGORIES = (
    "Wrong passwords",
    "Unknown users",
    "Locked accounts",
    "Expired / unauthorized",
    "Password resets",
    "Failed password resets",
)
#: Statuts d'un reset qui n'a pas pu envoyer le mail (ou enregistrer le mot de passe).
SECURITY_MAIL_STATUS = (
    "Reset password: mail failed",
    "Reset password: db error",
    "Reset password: config error",
)


def security_category(status: Any) -> str | None:
    """Categorie de securite d'un statut de log, ou None."""
    status = "" if status is None else str(status)
    if status == "Wrong pwd":
        return "Wrong passwords"
    if status == "Unknown user":
        return "Unknown users"
    if status in ("Locked Account", "Account locked"):
        return "Locked accounts"
    if status in ("Expired", "Unauthorized", "Reset password: expired"):
        return "Expired / unauthorized"
    if status == "Reset password":
        return "Password resets"
    if status.startswith("Reset password: "):
        return "Failed password resets"
    return None


def _locked_users(pwd_mngt: list[dict[str, Any]] | None, limit: Any) -> dict[str, bool] | None:
    """User -> verrouille, si une limite d'echecs est posee et n_wrong_pwd existe ; sinon None."""
    if not settings.is_finite_limit(limit) or not pwd_mngt:
        return None
    if not any("n_wrong_pwd" in r for r in pwd_mngt):
        return None
    out = {}
    for r in pwd_mngt:
        n = r.get("n_wrong_pwd")
        out[str(r.get("user"))] = n is not None and float(n) >= float(limit)
    return out


def security_kpi_counts(
    logs_period: list[dict[str, Any]], pwd_mngt: list[dict[str, Any]] | None, limit: Any
) -> dict[str, int]:
    """Compteurs de securite sur la periode (cle i18n -> nombre).

    "Locked accounts" (etat courant, pas limite a la periode) en premier, seulement si une limite
    d'echecs est posee. Les noms d'utilisateurs inconnus ne sont que comptes, jamais affiches :
    on tape parfois son mot de passe dans le champ utilisateur.
    """
    categories = [security_category(r.get("status")) for r in logs_period]
    kpis: dict[str, int] = {}
    locked = _locked_users(pwd_mngt, limit)
    if locked is not None:
        kpis["Locked accounts"] = sum(locked.values())
    for cat in ("Wrong passwords", "Unknown users", "Password resets", "Failed password resets"):
        kpis[cat] = categories.count(cat)
    kpis["Reset errors"] = sum(r.get("status") in SECURITY_MAIL_STATUS for r in logs_period)
    return kpis


def security_events_per_day(
    logs_period: list[dict[str, Any]],
) -> tuple[list[str], list[tuple[str, dict[str, int]]]]:
    """Evenements de securite par jour (tous les jours de la plage) et par categorie presente.

    Returns:
        (categories presentes, [(jour, {categorie: nombre})]) ; listes vides sans evenement.
    """
    events = []
    for r in logs_period:
        cat = security_category(r.get("status"))
        if cat is None:
            continue
        try:
            events.append((date.fromisoformat(_day(r)), cat))
        except ValueError:
            continue
    if not events:
        return [], []
    categories = [c for c in SECURITY_CATEGORIES if any(cat == c for _, cat in events)]
    first, last = min(d for d, _ in events), max(d for d, _ in events)
    out = []
    current = first
    while current <= last:
        counts = {c: sum(1 for d, cat in events if d == current and cat == c) for c in categories}
        out.append((current.isoformat(), counts))
        current += timedelta(days=1)
    return categories, out


def users_overview(
    users: list[str],
    logs: list[dict[str, Any]],
    logs_period: list[dict[str, Any]],
    pwd_mngt: list[dict[str, Any]] | None = None,
    pwd_failure_limit: Any = float("inf"),
) -> list[dict[str, Any]]:
    """Une ligne par utilisateur (comme le source).

    Derniere connexion et application sur `logs` (connexions reussies, toutes periodes et
    applications) ; nombres de connexions / mauvais mots de passe / resets sur `logs_period`
    (tous statuts) ; `locked` quand une limite d'echecs est posee.
    """
    last: dict[str, dict[str, Any]] = {}
    for r in sorted(logs, key=lambda r: str(r.get("server_connected") or ""), reverse=True):
        last.setdefault(str(r.get("user")), r)

    def count(user: str, status: str) -> int:
        return sum(
            1
            for r in logs_period
            if str(r.get("user")) == user and (r.get("status") or "Success") == status
        )

    locked = _locked_users(pwd_mngt, pwd_failure_limit)
    out = []
    for user in users:
        row = {
            "user": user,
            "last_connection": (last.get(user) or {}).get("server_connected"),
            "last_app": (last.get(user) or {}).get("app"),
            "n_success": count(user, "Success"),
            "n_wrong_pwd": count(user, "Wrong pwd"),
            "n_reset": count(user, "Reset password"),
        }
        if locked is not None:
            row["locked"] = locked.get(user, False)
        out.append(row)
    return out


#: Colonne de `users_overview` -> cle i18n de l'en-tete.
OVERVIEW_LABELS = {
    "user": "user",
    "last_connection": "Last connection",
    "last_app": "Last application",
    "n_success": "Nb logged",
    "n_wrong_pwd": "Wrong passwords",
    "n_reset": "Password resets",
    "locked": "Locked",
}


def users_overview_html(overview: list[dict[str, Any]], language: str = "en") -> ui.Tag:
    """Table de la vue par utilisateur, triee sur la derniere connexion (plus recente d'abord)."""
    lan = use_language(language)
    rows = sorted(overview, key=lambda r: str(r.get("last_connection") or ""), reverse=True)
    columns = list(overview[0]) if overview else list(OVERVIEW_LABELS)[:-1]
    body = []
    for r in rows:
        cells = []
        for c in columns:
            value = r.get(c)
            if c == "locked":
                value = lan.get("Yes") if value else lan.get("No")
            cells.append(ui.tags.td("" if value is None else str(value)))
        body.append(ui.tags.tr(*cells))
    table = ui.tags.table(
        ui.tags.thead(ui.tags.tr(*[ui.tags.th(lan.get(OVERVIEW_LABELS[c])) for c in columns])),
        ui.tags.tbody(*body),
        class_="table table-condensed table-hover",
    )
    style = "max-height:500px;overflow-y:auto;" if len(rows) > 10 else None
    return ui.tags.div(table, style=style)


def security_kpis_html(kpis: dict[str, int], language: str = "en") -> ui.Tag:
    """Compteurs de securite en cartes (source : `well well-sm` sur 2 colonnes)."""
    lan = use_language(language)
    return ui.tags.div(
        *[
            ui.tags.div(
                ui.tags.div(
                    ui.tags.h3(str(n), style="margin-top: 5px;"),
                    ui.tags.span(lan.get(key)),
                    class_="card card-body text-center mb-2",
                ),
                class_="col-sm-2",
            )
            for key, n in kpis.items()
        ],
        class_="row",
    )


def _security_figure(
    categories: list[str], data: list[tuple[str, dict[str, int]]], lan: Any
) -> Any:
    """Barres empilees par jour et categorie (source : bb_barchart stacked=TRUE)."""
    import plotly.graph_objects as go

    days = [d for d, _ in data]
    fig = go.Figure(
        [go.Bar(x=days, y=[c[cat] for _, c in data], name=lan.get(cat)) for cat in categories]
    )
    fig.update_layout(
        barmode="stack",
        title=lan.get("Number of security events per day"),
        margin={"l": 10, "r": 10, "t": 40, "b": 40},
        plot_bgcolor="white",
    )
    fig.update_xaxes(type="date")
    fig.update_yaxes(showgrid=True, gridcolor="#eee")
    return fig


def _security_table(categories: list[str], data: list[tuple[str, dict[str, int]]], lan: Any) -> Any:
    """Sans plotly : table jour x categorie (jours sans evenement omis)."""
    body = [
        ui.tags.tr(ui.tags.td(d), *[ui.tags.td(str(c[cat])) for cat in categories])
        for d, c in data
        if any(c.values())
    ]
    return ui.tags.div(
        ui.tags.h4(lan.get("Number of security events per day")),
        ui.tags.table(
            ui.tags.thead(
                ui.tags.tr(ui.tags.th(""), *[ui.tags.th(lan.get(cat)) for cat in categories])
            ),
            ui.tags.tbody(*body),
            class_="table table-condensed",
        ),
    )


def filter_logs(
    rows: list[dict[str, Any]],
    start: date,
    end: date,
    users: list[str] | None = None,
    apps: list[str] | None = None,
    all_users_label: str = "All users",
) -> list[dict[str, Any]]:
    """Filtre les logs par periode, utilisateur(s) et application(s).

    Args:
        rows: logs (deja restreints aux succes).
        start: debut de periode (inclus).
        end: fin de periode (incluse).
        users: utilisateurs retenus ; None ou contenant `all_users_label` => tous.
        apps: applications retenues ; None/vide ou contenant "All applications" => toutes.
        all_users_label: libelle i18n de "All users" (le filtre du source compare a ce libelle).

    Returns:
        Les logs retenus.
    """
    out = []
    for r in rows:
        day = _day(r)
        if not day:
            continue
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            continue
        if parsed < start or parsed > end:
            continue
        if users and all_users_label not in users and r.get("user") not in users:
            continue
        if apps and ALL_APPS not in apps and r.get("app") not in apps:
            continue
        out.append(r)
    return out


def count_by_user(rows: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """Nombre de connexions par utilisateur, trie par compte DECROISSANT (source : order desc)."""
    counts: dict[str, int] = {}
    for r in rows:
        user = str(r.get("user") or "")
        counts[user] = counts.get(user, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)


def count_by_day(rows: list[dict[str, Any]]) -> list[tuple[date, int]]:
    """Nombre de connexions par jour, jours manquants a 0.

    Comme le source : la plage va de (premier jour - 1) a (dernier jour + 1), et les jours sans
    connexion valent 0 (merge all.x puis NA -> 0).
    """
    counts: dict[date, int] = {}
    for r in rows:
        day = _day(r)
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            continue
        counts[parsed] = counts.get(parsed, 0) + 1
    if not counts:
        return []
    first = min(counts) - timedelta(days=1)
    last = max(counts) + timedelta(days=1)
    out = []
    current = first
    while current <= last:
        out.append((current, counts.get(current, 0)))
        current += timedelta(days=1)
    return out


def logs_csv() -> str:
    """Export CSV des logs (token retire), joints aux utilisateurs, tri desc sur server_connected.

    Reproduit le contenu du `download_logs` du source : sep ';', NA vide, colonnes start/expire
    retirees si entierement vides, jointure a gauche sur `user` (R place la colonne de jointure
    en PREMIER), tri decroissant sur `server_connected`.
    """
    # Export : dedup CSV du source (conserve toutes les lignes sans token, cf. read_logs).
    rows = [dict(r) for r in read_logs(keep_untokened=True)]
    users = {u["user"]: u for u in list_users()}
    for r in rows:
        r.pop("token", None)
        # `id` est la cle primaire propre a la cible (absente du schema R) : elle n'a pas a
        # sortir dans un export cense reproduire celui du source.
        r.pop("id", None)
    # Colonnes start/expire retirees si toutes vides (source).
    user_cols = [c for c in _columns(list(users.values())) if c != "user"]
    for col in ("start", "expire"):
        if col in user_cols and all(not u.get(col) for u in users.values()):
            user_cols.remove(col)
    for r in rows:
        extra = users.get(str(r.get("user")), {})
        for col in user_cols:
            r[col] = extra.get(col)
    rows.sort(key=lambda r: str(r.get("server_connected") or ""), reverse=True)
    # merge() de R met la colonne de jointure en premier.
    columns: list[str] = ["user"]
    for r in rows:
        for c in r:
            if c not in columns:
                columns.append(c)
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=columns, delimiter=";", restval="", quoting=csv.QUOTE_NONNUMERIC
    )
    writer.writeheader()
    for r in rows:
        writer.writerow({c: "" if r.get(c) is None else r.get(c) for c in columns})
    return buf.getvalue()


def _bar_figure(data: list[tuple[str, int]], axis_title: str, series_name: str = "") -> Any:
    """Graphe 1 : barres HORIZONTALES par utilisateur (source : bb_barchart rotated=TRUE).

    Retourne une `go.Figure`, rendue en HTML par `_plotly_html` (plotly installe uniquement).

    Args:
        data: (utilisateur, nombre de connexions), deja trie decroissant.
        axis_title: titre de l'axe des comptes.
        series_name: nom de la serie (source : `bb_data(names = list(Freq = get("Nb logged")))`,
            visible dans l'infobulle, la legende etant masquee).
    """
    import plotly.graph_objects as go

    # Trie decroissant : plotly empile de bas en haut, on inverse pour lire le max en haut.
    users = [u for u, _ in data][::-1]
    counts = [n for _, n in data][::-1]
    fig = go.Figure(
        go.Bar(x=counts, y=users, orientation="h", marker_color=_COLOR, name=series_name)
    )
    fig.update_layout(
        showlegend=False,
        xaxis_title=axis_title,
        margin={"l": 10, "r": 10, "t": 20, "b": 40},
        plot_bgcolor="white",
    )
    fig.update_xaxes(showgrid=True, gridcolor="#eee")
    return fig


def _step_area_figure(data: list[tuple[date, int]], axis_title: str, series_name: str = "") -> Any:
    """Graphe 2 : aire en escalier par jour (source : bb_linechart type="area-step").

    Args:
        data: (jour, nombre de connexions), jours manquants deja combles a 0.
        axis_title: titre de l'axe des comptes.
        series_name: nom de la serie (cf. `_bar_figure`).
    """
    import plotly.graph_objects as go

    days = [d.isoformat() for d, _ in data]
    counts = [n for _, n in data]
    fig = go.Figure(
        go.Scatter(
            x=days,
            y=counts,
            mode="lines",
            line={"shape": "hv", "color": _COLOR},
            fill="tozeroy",
            fillcolor=_COLOR,
            name=series_name,
        )
    )
    fig.update_layout(
        showlegend=False,
        yaxis_title=axis_title,
        margin={"l": 10, "r": 10, "t": 20, "b": 40},
        plot_bgcolor="white",
    )
    fig.update_xaxes(type="date", showgrid=True, gridcolor="#eee")
    fig.update_yaxes(showgrid=True, gridcolor="#eee")
    return fig


@module.ui
def logs_ui(language: str = "en") -> ui.TagList:
    """UI de l'onglet Logs (port de `logs_ui` R) : filtres + 2 graphes, aucune table."""
    lan = use_language(language)
    download = settings.get_option("download") or []
    today = date.today()
    return ui.TagList(
        ui.tags.div(
            ui.tags.div(
                ui.tags.div(
                    ui.tags.div(
                        ui.input_select(
                            "user",
                            lan.get("User:"),
                            choices=[lan.get("All users")],
                            selected=lan.get("All users"),
                            multiple=True,
                            width="100%",
                        ),
                        class_="col-sm-3",
                    ),
                    ui.tags.div(
                        ui.input_date_range(
                            "overview_period",
                            lan.get("Period:"),
                            start=today - timedelta(days=31),
                            end=today,
                            width="100%",
                        ),
                        class_="col-sm-3",
                    ),
                    ui.tags.div(
                        ui.input_action_button(
                            "last_week",
                            lan.get("Last week"),
                            class_="btn-primary btn-sm btn-margin",
                        ),
                        ui.input_action_button(
                            "last_month",
                            lan.get("Last month"),
                            class_="btn-primary btn-sm btn-margin",
                        ),
                        ui.input_action_button(
                            "all_period",
                            lan.get("All period"),
                            class_="btn-primary btn-sm btn-margin",
                        ),
                        class_="col-sm-6",
                    ),
                    class_="row",
                ),
                # Selecteur d'application : rendu seulement si plusieurs applications (source :
                # conditionalPanel sur output$print_app_input_js).
                ui.output_ui("app_selector"),
                ui.tags.h3(
                    icon_svg("users"),
                    " " + lan.get("Number of connections per user"),
                    class_="text-primary",
                ),
                ui.tags.hr(),
                ui.output_ui("graph_conn_users"),
                ui.tags.br(),
                ui.tags.h3(
                    icon_svg("calendar-days"),
                    " " + lan.get("Number of connections per day"),
                    class_="text-primary",
                ),
                ui.tags.hr(),
                ui.output_ui("graph_conn_days"),
                ui.tags.br(),
                ui.tags.h3(
                    icon_svg("address-card"),
                    " " + lan.get("Users overview"),
                    class_="text-primary",
                ),
                ui.tags.hr(),
                ui.output_ui("table_users_overview"),
                ui.tags.br(),
                ui.tags.h3(
                    icon_svg("shield-halved"),
                    " " + lan.get("Security events"),
                    class_="text-primary",
                ),
                ui.tags.hr(),
                ui.output_ui("security_kpis"),
                ui.output_ui("graph_security_days"),
                (
                    ui.TagList(
                        ui.tags.br(),
                        ui.tags.br(),
                        ui.download_button(
                            "download_logs",
                            ui.TagList(
                                icon_svg("download"),
                                " " + lan.get("Download logs database"),
                            ),
                            class_="btn-primary center-block",
                        ),
                    )
                    if "logs" in download
                    else None
                ),
                ui.tags.br(),
                class_="col-sm-10 offset-sm-1",
            ),
            class_="row",
        ),
    )


@module.server
def logs_server(
    input: Inputs,
    output: Outputs,
    session: Session,
    language: str = "en",
    file_encoding: str = "",
) -> None:  # pragma: no cover
    """Serveur de l'onglet Logs (port de la fonction `logs` de modules-logs.R).

    La preparation des donnees est deleguee a des fonctions pures testees ; seul le cablage
    reactif est ici.

    Args:
        input: inputs de la session (argument standard d'un module shiny).
        output: outputs de la session (argument standard d'un module shiny).
        session: session shiny (argument standard d'un module shiny).
        language: code de langue.
        file_encoding: encodage du CSV de logs telecharge (port de `fileEncoding` du source ;
            "" = defaut de la plateforme, comme R).
    """
    from shinymanager.secure_app import COOKIE_NAME

    lan = use_language(language)
    all_users = lan.get("All users")
    root_conn = session.root_scope().http_conn
    token_start = root_conn.cookies.get(COOKIE_NAME) if root_conn else None

    @reactive.calc
    def _logs() -> list[dict[str, Any]]:
        return success_logs()

    @reactive.calc
    def _all_logs() -> list[dict[str, Any]]:
        return all_logs()

    @reactive.calc
    def _pwd_mngt() -> list[dict[str, Any]] | None:
        kind, handle = pwd_lifecycle._active()
        if kind is None:
            return None
        try:
            return pwd_lifecycle._read(kind, handle, "pwd_mngt")
        except Exception:
            return None

    @reactive.calc
    def _apps() -> list[str]:
        return app_choices(_all_logs())

    @reactive.effect
    def _fill_user_choices() -> None:
        ui.update_select(
            "user",
            choices=[all_users, *[str(u["user"]) for u in list_users()]],
            selected=all_users,
        )

    @render.ui
    def app_selector() -> Any:
        # Le selecteur est TOUJOURS dans le DOM (le conditionalPanel du source ne fait que le
        # MASQUER) : son `selected` alimente le filtre meme quand il est invisible. Le rendre
        # conditionnellement supprimerait le filtre, ce que le source ne fait pas.
        apps = _apps()
        return ui.tags.div(
            ui.tags.div(
                ui.input_select(
                    "app",
                    "Application :",  # label EN DUR dans le source (non traduit)
                    choices=[ALL_APPS, *apps],
                    selected=str(settings.get_option("application")),
                    multiple=True,
                    width="100%",
                ),
                class_="col-sm-12",
            ),
            class_="row",
            style=None if len(apps) > 1 else "display: none;",
        )

    def _filter(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # `req(input$user)` du source : aucun utilisateur selectionne => rien a montrer (et NON
        # "tous les utilisateurs", ce que donnerait un filtre a None).
        req(input.user())
        period = input.overview_period()
        if not period or period[0] is None or period[1] is None:
            return []
        return filter_logs(
            rows,
            period[0],
            period[1],
            users=list(input.user()),
            apps=list(input.app()) if "app" in input else None,
            all_users_label=all_users,
        )

    @reactive.calc
    def _period_logs() -> list[dict[str, Any]]:
        return _filter(_logs())

    @reactive.calc
    def _all_period_logs() -> list[dict[str, Any]]:
        return _filter(_all_logs())

    @render.ui
    def table_users_overview() -> Any:
        users = [str(u["user"]) for u in list_users()]
        selected = list(input.user())
        if all_users not in selected:
            users = [u for u in users if u in selected]
        overview = users_overview(
            users, _logs(), _all_period_logs(), _pwd_mngt(), settings.get_pwd_failure_limit()
        )
        return users_overview_html(overview, language)

    @render.ui
    def security_kpis() -> Any:
        limit = settings.get_pwd_failure_limit()
        kpis = security_kpi_counts(_all_period_logs(), _pwd_mngt(), limit)
        return security_kpis_html(kpis, language)

    @render.ui
    def graph_security_days() -> Any:
        categories, data = security_events_per_day(_all_period_logs())
        req(len(data) > 0)
        if HAS_PLOTLY:
            return _plotly_html(_security_figure(categories, data, lan), height=400)
        return _security_table(categories, data, lan)

    @render.ui
    def graph_conn_users() -> Any:
        data = count_by_user(_period_logs())
        req(len(data) > 0)  # source : req(nrow(logs_period) > 0) -> aucun graphe rendu
        axis = lan.get("Total number of connection")
        if HAS_PLOTLY:
            return _plotly_html(_bar_figure(data, axis, lan.get("Nb logged")), height=600)
        return _bar_html(data, axis)

    @render.ui
    def graph_conn_days() -> Any:
        data = count_by_day(_period_logs())
        req(len(data) > 0)
        axis = lan.get("Total number of connection")
        if HAS_PLOTLY:
            return _plotly_html(_step_area_figure(data, axis, lan.get("Nb logged")), height=400)
        return _step_area_svg(data, axis)

    @reactive.effect
    @reactive.event(input.last_week)
    def _last_week() -> None:
        ui.update_date_range(
            "overview_period", start=date.today() - timedelta(days=7), end=date.today()
        )

    @reactive.effect
    @reactive.event(input.last_month)
    def _last_month() -> None:
        ui.update_date_range(
            "overview_period", start=date.today() - timedelta(days=31), end=date.today()
        )

    @reactive.effect
    @reactive.event(input.all_period)
    def _all_period() -> None:
        days = [_day(r) for r in _logs() if _day(r)]
        start = min(days) if days else date.today().isoformat()
        ui.update_date_range("overview_period", start=start, end=date.today())

    @render.download(filename=lambda: f"shinymanager-logs-{date.today()}.csv")
    def download_logs():
        # Le token admin est reverifie au telechargement (revoque / expire depuis, comme le source).
        if not (_tok.is_active(token_start) and _tok.is_admin(token_start)):
            return
        if "logs" not in (settings.get_option("download") or []):
            return
        text = logs_csv()
        # `fileEncoding` du source : "" = defaut de la plateforme (R). On n'encode
        # explicitement que si un encodage est demande.
        yield text.encode(file_encoding) if file_encoding else text
