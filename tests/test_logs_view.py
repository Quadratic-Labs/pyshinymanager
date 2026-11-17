"""Tests de l'onglet Logs (filtres + 2 graphes, AUCUNE table).

La preparation des donnees des graphes est faite par des fonctions pures : elles sont testees
ici sans session, contre le comportement du source (modules-logs.R).
"""

from datetime import date

import pytest

from shinymanager import logs_view, settings
from shinymanager.db import create_db
from shinymanager.tokens import _tok


@pytest.fixture(autouse=True)
def _reset():
    settings.reset_options()
    _tok.__init__()
    yield
    settings.reset_options()
    _tok.__init__()


def _log(user, day, app="demo", status="Success"):
    return {
        "user": user,
        "server_connected": f"{day} 10:00:00",
        "token": f"{user}-{day}",
        "logout": None,
        "app": app,
        "status": status,
    }


# --- filter_logs ---


def test_filter_logs_keeps_only_the_period():
    rows = [_log("a", "2026-07-01"), _log("a", "2026-07-10"), _log("a", "2026-07-20")]
    out = logs_view.filter_logs(rows, date(2026, 7, 5), date(2026, 7, 15))
    assert [r["server_connected"][:10] for r in out] == ["2026-07-10"]


def test_filter_logs_period_bounds_are_inclusive():
    rows = [_log("a", "2026-07-05"), _log("a", "2026-07-15")]
    out = logs_view.filter_logs(rows, date(2026, 7, 5), date(2026, 7, 15))
    assert len(out) == 2


def test_filter_logs_all_users_label_disables_the_user_filter():
    rows = [_log("a", "2026-07-10"), _log("b", "2026-07-10")]
    out = logs_view.filter_logs(
        rows, date(2026, 7, 1), date(2026, 7, 31), users=["All users"], all_users_label="All users"
    )
    assert len(out) == 2


def test_filter_logs_filters_by_user():
    rows = [_log("a", "2026-07-10"), _log("b", "2026-07-10")]
    out = logs_view.filter_logs(rows, date(2026, 7, 1), date(2026, 7, 31), users=["b"])
    assert [r["user"] for r in out] == ["b"]


def test_filter_logs_all_applications_disables_the_app_filter():
    rows = [_log("a", "2026-07-10", app="x"), _log("a", "2026-07-10", app="y")]
    out = logs_view.filter_logs(
        rows, date(2026, 7, 1), date(2026, 7, 31), apps=[logs_view.ALL_APPS]
    )
    assert len(out) == 2


def test_filter_logs_filters_by_app():
    rows = [_log("a", "2026-07-10", app="x"), _log("a", "2026-07-10", app="y")]
    out = logs_view.filter_logs(rows, date(2026, 7, 1), date(2026, 7, 31), apps=["y"])
    assert [r["app"] for r in out] == ["y"]


def test_filter_logs_ignores_unparsable_dates():
    rows = [_log("a", "2026-07-10"), {"user": "b", "server_connected": None}]
    out = logs_view.filter_logs(rows, date(2026, 7, 1), date(2026, 7, 31))
    assert len(out) == 1


# --- count_by_user (graphe 1) ---


def test_count_by_user_sorts_by_count_descending():
    rows = [_log("a", "2026-07-01"), _log("b", "2026-07-01"), _log("b", "2026-07-02")]
    assert logs_view.count_by_user(rows) == [("b", 2), ("a", 1)]


def test_count_by_user_on_empty_input():
    assert logs_view.count_by_user([]) == []


# --- count_by_day (graphe 2) ---


def test_count_by_day_pads_missing_days_with_zero():
    rows = [_log("a", "2026-07-10"), _log("a", "2026-07-12")]
    out = logs_view.count_by_day(rows)
    # Source : de (min - 1 jour) a (max + 1 jour), jours manquants a 0.
    assert out == [
        (date(2026, 7, 9), 0),
        (date(2026, 7, 10), 1),
        (date(2026, 7, 11), 0),
        (date(2026, 7, 12), 1),
        (date(2026, 7, 13), 0),
    ]


def test_count_by_day_counts_several_logs_the_same_day():
    rows = [_log("a", "2026-07-10"), _log("b", "2026-07-10")]
    assert (date(2026, 7, 10), 2) in logs_view.count_by_day(rows)


def test_count_by_day_on_empty_input():
    assert logs_view.count_by_day([]) == []


# --- Graphes (structure plotly, si installe) ---


def test_bar_figure_is_horizontal_and_uses_the_source_color():
    pytest.importorskip("plotly")
    fig = logs_view._bar_figure([("b", 2), ("a", 1)], "Total")
    trace = fig.data[0]
    assert trace.orientation == "h"
    assert trace.marker.color == "#4582ec"
    # Trie decroissant, inverse pour que le maximum se lise en haut.
    assert list(trace.y) == ["a", "b"]
    assert fig.layout.showlegend is False
    assert fig.layout.xaxis.title.text == "Total"


def test_step_area_figure_uses_step_shape_and_fill():
    pytest.importorskip("plotly")
    fig = logs_view._step_area_figure([(date(2026, 7, 10), 1), (date(2026, 7, 11), 0)], "Total")
    trace = fig.data[0]
    assert trace.line.shape == "hv"  # aire en escalier (source : type "area-step")
    assert trace.fill == "tozeroy"
    assert trace.line.color == "#4582ec"
    assert fig.layout.showlegend is False


def test_plotly_html_is_a_self_contained_snippet_with_its_dependency():
    pytest.importorskip("plotly")
    out = str(logs_view._plotly_html(logs_view._bar_figure([("a", 1)], "Total"), height=300))
    assert "Plotly.newPlot" in out and "<html" not in out


# --- Graphes sans plotly (HTML / SVG statiques) ---


def test_bar_html_scales_bars_to_the_maximum():
    out = str(logs_view._bar_html([("bob", 4), ("ann", 1)], "Total"))
    assert out.index("bob") < out.index("ann")  # maximum en haut
    assert "width:100.0%" in out and "width:25.0%" in out
    assert "#4582ec" in out and "Total" in out


def test_step_area_svg_draws_a_step_polygon_with_axis_labels():
    data = [(date(2026, 7, 10), 2), (date(2026, 7, 11), 0), (date(2026, 7, 12), 1)]
    out = str(logs_view._step_area_svg(data, "Total"))
    assert "<polygon" in out and "2026-07-10" in out and "2026-07-12" in out
    assert ">2</text>" in out  # maximum sur l'axe


# --- Integration backend : lecture + export ---


def test_success_logs_excludes_failures(tmp_path):
    from shinymanager import logs as logs_mod

    path = str(tmp_path / "c.sqlite")
    create_db([{"user": "alice", "password": "Secret1"}], path)
    _tok.set_sqlite_path(path)
    _tok.add("tok", {"user": "alice"})
    logs_mod.save_logs("tok")
    logs_mod.save_logs_failed("alice", "Wrong pwd")
    out = logs_view.success_logs()
    assert all(r["status"] == "Success" for r in out)
    assert any(r["user"] == "alice" for r in out)


def test_logs_csv_drops_token_and_sorts_desc(tmp_path):
    from shinymanager import logs as logs_mod

    path = str(tmp_path / "c.sqlite")
    create_db([{"user": "alice", "password": "Secret1"}], path)
    _tok.set_sqlite_path(path)
    _tok.add("tok", {"user": "alice"})
    logs_mod.save_logs("tok")
    csv_text = logs_view.logs_csv()
    assert "token" not in csv_text.splitlines()[0]
    assert "Secret1" not in csv_text
    assert ";" in csv_text.splitlines()[0]


# --- Surface : structure de l'UI ---


def test_logs_ui_exposes_source_input_ids_and_no_table(tmp_path):
    path = str(tmp_path / "c.sqlite")
    create_db([{"user": "alice", "password": "Secret1"}], path)
    _tok.set_sqlite_path(path)
    html = logs_view.logs_ui("logs").get_html_string()
    for input_id in (
        "logs-user",
        "logs-overview_period",
        "logs-last_week",
        "logs-last_month",
        "logs-all_period",
        "logs-graph_conn_users",
        "logs-graph_conn_days",
        "logs-download_logs",
        "logs-table_users_overview",
        "logs-security_kpis",
        "logs-graph_security_days",
    ):
        assert input_id in html, input_id
    # Les tables (vue par utilisateur) sont rendues cote serveur, pas dans l'UI statique.
    assert "<table" not in html


def test_logs_ui_download_follows_the_option(tmp_path):
    path = str(tmp_path / "c.sqlite")
    create_db([{"user": "alice", "password": "Secret1"}], path)
    _tok.set_sqlite_path(path)
    settings.set_option("download", [])
    assert "logs-download_logs" not in logs_view.logs_ui("logs").get_html_string()


# --- Tableau de bord (vue par utilisateur, evenements de securite) ---


def test_app_choices_include_failures_and_current_app():
    # Corrige le bug unique(x, incomparables) d'une ancienne version du source.
    settings.set_option("application", "current")
    rows = [_log("a", "2024-01-01", app="x", status="Wrong pwd"), _log("b", "2024-01-02", app="y")]
    assert logs_view.app_choices(rows) == ["x", "y", "current"]
    assert logs_view.app_choices([]) == ["current"]


def test_security_category():
    cat = logs_view.security_category
    assert cat("Wrong pwd") == "Wrong passwords"
    assert cat("Unknown user") == "Unknown users"
    assert cat("Account locked") == cat("Locked Account") == "Locked accounts"
    assert cat("Reset password: expired") == "Expired / unauthorized"
    assert cat("Reset password") == "Password resets"
    assert cat("Reset password: too soon") == "Failed password resets"
    assert cat("Success") is None and cat(None) is None


def test_security_kpi_counts_and_locked_accounts():
    logs = [
        _log("a", "2024-01-01", status="Wrong pwd"),
        _log("ghost", "2024-01-01", status="Unknown user"),
        _log("a", "2024-01-02", status="Reset password: mail failed"),
        _log("a", "2024-01-02", status="Reset password"),
    ]
    pwd = [{"user": "a", "n_wrong_pwd": 3}, {"user": "b", "n_wrong_pwd": 0}]
    kpis = logs_view.security_kpi_counts(logs, pwd, float("inf"))
    assert "Locked accounts" not in kpis
    assert kpis == {
        "Wrong passwords": 1,
        "Unknown users": 1,
        "Password resets": 1,
        "Failed password resets": 1,
        "Reset errors": 1,
    }
    kpis = logs_view.security_kpi_counts(logs, pwd, 3)
    assert next(iter(kpis)) == "Locked accounts" and kpis["Locked accounts"] == 1


def test_security_events_per_day_fills_the_range():
    logs = [
        _log("a", "2024-01-01", status="Wrong pwd"),
        _log("a", "2024-01-03", status="Wrong pwd"),
        _log("a", "2024-01-03", status="Reset password"),
        _log("a", "2024-01-03"),
    ]
    categories, data = logs_view.security_events_per_day(logs)
    assert categories == ["Wrong passwords", "Password resets"]
    assert [d for d, _ in data] == ["2024-01-01", "2024-01-02", "2024-01-03"]
    assert data[2][1] == {"Wrong passwords": 1, "Password resets": 1}
    assert logs_view.security_events_per_day([_log("a", "2024-01-01")]) == ([], [])


def test_users_overview():
    success = [_log("a", "2024-01-01", app="x"), _log("a", "2024-01-05", app="y")]
    period = [*success, _log("a", "2024-01-02", status="Wrong pwd")]
    rows = logs_view.users_overview(
        ["a", "b"], success, period, [{"user": "a", "n_wrong_pwd": 5}], 3
    )
    assert rows[0] == {
        "user": "a",
        "last_connection": "2024-01-05 10:00:00",
        "last_app": "y",
        "n_success": 2,
        "n_wrong_pwd": 1,
        "n_reset": 0,
        "locked": True,
    }
    assert rows[1]["last_connection"] is None and rows[1]["locked"] is False
    html = logs_view.users_overview_html(rows).get_html_string()
    assert "Last connection" in html and ">Yes<" in html
    assert "locked" not in logs_view.users_overview(["a"], success, period)[0]


def test_failures_of_the_same_day_are_all_kept(tmp_path):
    from shinymanager.logs import save_logs_failed

    path = str(tmp_path / "c.sqlite")
    create_db([{"user": "alice", "password": "Secret1"}], path)
    _tok.set_sqlite_path(path)
    _tok.add("t", {"user": "alice"})
    from shinymanager.logs import save_logs

    save_logs("t")
    save_logs("t")  # idempotent
    for _ in range(3):
        save_logs_failed("alice", "Wrong pwd")
    statuses = [r["status"] for r in logs_view.all_logs()]
    assert statuses.count("Wrong pwd") == 3
