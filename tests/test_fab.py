"""Phase 8 — tests du bouton flottant et du heartbeat de timeout (approche DU1, structure)."""

from shinymanager.fab import _ACTIVITY_EVENTS, fab_button, timeout_script


def test_fab_button_structure_and_children():
    # Le contrat des actions : `icon` = nom FA6 nu (rendu via faicons/icon_svg).
    fab = fab_button(
        {"id": "logout", "label": "Logout", "icon": "right-from-bracket"},
        {"id": "admin", "label": "Admin", "icon": "gear"},
        position="bottom-right",
        input_id="fab",
    )
    html = fab.get_html_string()
    assert "mfb-component--br" in html
    assert 'id="fab"' in html
    assert 'id="logout"' in html
    assert 'id="admin"' in html
    assert "mfb-component__button--child action-button" in html
    # Icones rendues en SVG (faicons) et classe de positionnement mfb preservee.
    assert "<svg" in html
    assert "mfb-component__child-icon" in html


def test_fab_button_position_none_returns_none():
    assert fab_button({"id": "x", "label": "X"}, position="none") is None


def test_fab_button_positions_map():
    assert "mfb-component--tl" in fab_button({"id": "a"}, position="top-left").get_html_string()
    assert "mfb-component--tr" in fab_button({"id": "a"}, position="top-right").get_html_string()
    assert "mfb-component--bl" in fab_button({"id": "a"}, position="bottom-left").get_html_string()


def test_timeout_script_uses_real_user_events_not_recalculating():
    html = timeout_script("shinymanager_timeout").get_html_string()
    # Le fix : plus de shiny:recalculating (cause des sessions immortelles).
    assert "shiny:recalculating" not in html
    # Les vrais evenements utilisateur sont ecoutes.
    for event in _ACTIVITY_EVENTS:
        assert event in html
    assert "shinymanager_timeout" in html
