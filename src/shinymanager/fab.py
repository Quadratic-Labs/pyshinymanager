"""Bouton flottant (logout / admin / go to app) et heartbeat de timeout.

Sources R/fab_button.R et inst/assets/timeout.js.
Module 10 du plan. Fidelite IDIOMATIQUE (fab_button) + REDESIGN du heartbeat de timeout.

REDESIGN (corrige la zone de flou "sessions immortelles", assets-js ZF-2) : le heartbeat du
source ecoutait `shiny:recalculating`, donc tout output qui se recalcule (rafraichissement
automatique) comptait comme activite et empechait l'expiration. Ici, l'activite est liee aux
VRAIS evenements utilisateur (mousemove, keydown, click, scroll, touchstart). Le polyfill
Object.assign du source n'est pas porte (navigateurs modernes, assets-js ZF-3).
"""

from __future__ import annotations

from typing import Any

from faicons import icon_svg
from shiny import ui

_POSITIONS = {
    "bottom-right": "br",
    "top-right": "tr",
    "bottom-left": "bl",
    "top-left": "tl",
}

#: Evenements client consideres comme activite utilisateur reelle (remplace shiny:recalculating).
_ACTIVITY_EVENTS = ("mousemove", "keydown", "click", "scroll", "touchstart")

TIMEOUT_INPUT = "shinymanager_timeout"


def _mfb_label(label: str | None) -> dict[str, str]:
    """Attribut data-mfb-label, OMIS si vide.

    Le CSS mfb affiche une bulle `content:attr(data-mfb-label)` au survol : un attribut present
    mais vide donne une bulle VIDE. Le source (R) passe `label=NULL`, qu'htmltools omet ; on
    reproduit en n'emettant l'attribut que s'il y a un libelle.
    """
    return {"data-mfb-label": label} if label else {}


def _child(action: dict[str, Any]) -> ui.Tag:
    icon = None
    if action.get("icon"):
        icon = icon_svg(action["icon"]).add_class("mfb-component__child-icon")
    return ui.tags.li(
        ui.tags.a(
            icon,
            id=action["id"],
            class_="mfb-component__button--child action-button",
            **_mfb_label(action.get("label")),
        )
    )


def fab_button(
    *actions: dict[str, Any],
    position: str = "bottom-right",
    toggle: str = "hover",
    animation: str = "slidein",
    input_id: str | None = None,
    label: str | None = None,
) -> ui.Tag | None:
    """Construit un bouton flottant avec des actions enfants (equivalent fab_button R).

    Args:
        *actions: specs d'actions ``{"id", "label", "icon"}``.
        position: coin de l'ecran ("bottom-right", "top-right", "bottom-left", "top-left",
            "none" pour ne rien afficher).
        toggle: "hover" ou "click".
        animation: animation d'apparition des boutons enfants.
        input_id: id du bouton principal (agit comme un actionButton).
        label: libelle du bouton principal.

    Returns:
        La structure du bouton flottant, ou None si position == "none".
    """
    if position == "none":
        return None
    pos = _POSITIONS[position]
    return ui.tags.ul(
        ui.tags.li(
            ui.tags.a(
                icon_svg("plus").add_class("mfb-component__main-icon--resting"),
                icon_svg("xmark").add_class("mfb-component__main-icon--active"),
                id=input_id,
                class_="mfb-component__button--main action-button",
                **_mfb_label(label),
            ),
            ui.tags.ul(*[_child(a) for a in actions], class_="mfb-component__list"),
            class_="mfb-component__wrap",
        ),
        class_=f"mfb-component--{pos} mfb-{animation}",
        **{"data-mfb-toggle": toggle},
    )


def timeout_script(input_id: str = TIMEOUT_INPUT) -> ui.Tag:
    """Retourne le script de heartbeat d'activite (REDESIGN : vrais evenements utilisateur).

    Envoie un compteur croissant a l'input `input_id` sur chaque evenement utilisateur reel,
    ce qui permet au serveur de rafraichir l'horodatage d'activite du token (is_valid_timeout).
    N'ecoute PAS shiny:recalculating (cause des sessions immortelles du source).

    Embarque aussi le handler `smReload` : py-shiny n'a pas d'equivalent de `session$reload()`,
    utilise par le source pour renvoyer au login quand le token expire (secure-app.R:364-391).
    """
    # ", " et non " " : un tableau JS sans virgules est une ERREUR DE SYNTAXE, qui faisait
    # echouer tout le script -> aucun ecouteur d'activite, donc aucune expiration de session.
    events = ", ".join(f"'{e}'" for e in _ACTIVITY_EVENTS)
    js = f"""
$(function() {{
  var timeout_cpt = 1;
  var events = [{events}];
  events.forEach(function(evt) {{
    document.addEventListener(evt, function() {{
      Shiny.setInputValue('{input_id}', timeout_cpt);
      timeout_cpt = timeout_cpt + 1;
    }}, {{passive: true}});
  }});
}});

// Equivalent de session$reload() du source (pas d'API py-shiny).
Shiny.addCustomMessageHandler('smReload', function(data) {{
  window.location.reload();
}});
"""
    return ui.tags.script(js)
