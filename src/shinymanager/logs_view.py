"""Onglet Logs du panneau admin (source R/modules-logs.R).

Reconstruction FIDELE contre `docs/migration/comprehension/surface/admin-panel.md` §6 :
filtres (utilisateur, periode, raccourcis, application), DEUX graphes, et AUCUNE table.

Ecarts assumes vs le source (registre de fidelite) :
  - billboarder (billboard.js) -> plotly + shinywidgets (decision Jeremy, 2026-07-15) : meme
    lecture (barres horizontales triees, aire en escalier), interactivite conservee. Le zoom
    "drag" + bouton "Unzoom" du source devient la barre d'outils plotly native.
  - `conditionalPanel(output$print_app_input_js)` (+ outputOptions suspendWhenHidden=FALSE, sans
    equivalent py-shiny) -> le selecteur d'application est rendu par un output_ui, present
    uniquement si plusieurs applications existent. Meme regle d'affichage.
  - la deduplication des vieux logs passe par `logs.read_logs` (dedup D5 corrigee).

Quirks du source REPRODUITS :
  - le label du selecteur d'application est "Application :" EN DUR (non traduit) ;
  - le filtre "toutes applications" repose sur la valeur "All applications", absente du
    dictionnaire i18n -> texte brut ;
  - seuls les logs `status == "Success"` alimentent les graphes.

La preparation des donnees est faite par des fonctions PURES (`filter_logs`, `count_by_user`,
`count_by_day`), testables sans session ; seul le cablage reactif est `# pragma: no cover`.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from typing import Any

from faicons import icon_svg
from shiny import Inputs, Outputs, Session, module, reactive, render, req, ui
from shinywidgets import output_widget, render_widget

from shinymanager import settings
from shinymanager.admin import list_users
from shinymanager.i18n import use_language
from shinymanager.logs import read_logs

#: Couleur des deux graphes (source : bb_bar_color_manual / bb_colors_manual).
_COLOR = "#4582ec"
#: Valeur "toutes applications" du source (absente du dictionnaire i18n : texte brut).
ALL_APPS = "All applications"


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

    Retourne une `go.Figure` (et non une `go.FigureWidget`, qui exigerait une session Shiny
    active) : `render_widget` la convertit au rendu. Rend la fabrique testable hors session.

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
                output_widget("graph_conn_users", height="600px"),
                ui.tags.br(),
                ui.tags.h3(
                    icon_svg("calendar-days"),
                    " " + lan.get("Number of connections per day"),
                    class_="text-primary",
                ),
                ui.tags.hr(),
                output_widget("graph_conn_days"),
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
    reactif est ici (approche DU1).

    Args:
        input: inputs de la session (argument standard d'un module shiny).
        output: outputs de la session (argument standard d'un module shiny).
        session: session shiny (argument standard d'un module shiny).
        language: code de langue.
        file_encoding: encodage du CSV de logs telecharge (port de `fileEncoding` du source ;
            "" = defaut de la plateforme, comme R).
    """
    lan = use_language(language)
    all_users = lan.get("All users")

    @reactive.calc
    def _logs() -> list[dict[str, Any]]:
        return success_logs()

    @reactive.calc
    def _apps() -> list[str]:
        # QUIRK DU SOURCE REPRODUIT (modules-logs.R:158) :
        #   app_choices <- unique(unique(logs$app), get_appname())
        # le 2e argument positionnel de unique() est `incomparables`, que R IGNORE : le nom
        # d'application courant n'est donc PAS ajoute aux choix. On reproduit (regle figee
        # "comportement source fait foi"). Ajouter get_appname() changerait la regle de
        # visibilite du selecteur. A confirmer au gate.
        seen: list[str] = []
        for r in _logs():
            app = r.get("app")
            if app and str(app) not in seen:
                seen.append(str(app))  # ordre d'apparition, comme unique() en R
        return seen

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

    @reactive.calc
    def _period_logs() -> list[dict[str, Any]]:
        # `req(input$user)` du source : aucun utilisateur selectionne => rien a montrer (et NON
        # "tous les utilisateurs", ce que donnerait un filtre a None).
        req(input.user())
        period = input.overview_period()
        if not period or period[0] is None or period[1] is None:
            return []
        return filter_logs(
            _logs(),
            period[0],
            period[1],
            users=list(input.user()),
            apps=list(input.app()) if "app" in input else None,
            all_users_label=all_users,
        )

    @render_widget
    def graph_conn_users() -> Any:
        data = count_by_user(_period_logs())
        req(len(data) > 0)  # source : req(nrow(logs_period) > 0) -> aucun graphe rendu
        return _bar_figure(data, lan.get("Total number of connection"), lan.get("Nb logged"))

    @render_widget
    def graph_conn_days() -> Any:
        data = count_by_day(_period_logs())
        req(len(data) > 0)
        return _step_area_figure(data, lan.get("Total number of connection"), lan.get("Nb logged"))

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
        if "logs" not in (settings.get_option("download") or []):
            return
        text = logs_csv()
        # `fileEncoding` du source : "" = defaut de la plateforme (R). On n'encode
        # explicitement que si un encodage est demande.
        yield text.encode(file_encoding) if file_encoding else text
