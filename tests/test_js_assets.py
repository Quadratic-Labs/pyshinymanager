"""Phase 8 — le JS produit et embarque doit au minimum PARSER.

Ne DECOUVRE pas ce test : il est ne d'un vrai defaut. `timeout_script` generait
`['mousemove' 'keydown' ...]` (join sur une espace au lieu d'une virgule) : une erreur de
syntaxe qui faisait echouer TOUT le script, donc aucun heartbeat d'activite, donc aucune
expiration de session. Les 4 tests du module 10 verifiaient la structure de la balise et
etaient verts : aucun ne parsait le JS.

Ces tests exigent `node` (present sur la machine de dev, sinon ils sont ignores).
"""

import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

from shinymanager.fab import timeout_script

_NODE = shutil.which("node")
requires_node = pytest.mark.skipif(_NODE is None, reason="node absent")


def _check_syntax(js: str) -> None:
    """Verifie que `js` parse (node --check), sinon fait echouer avec l'erreur du moteur."""
    proc = subprocess.run([_NODE, "--check", "-"], input=js, capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(f"JS invalide :\n{proc.stderr}\n--- source ---\n{js}")


def _script_text(tag) -> str:
    html = tag.get_html_string()
    return html.split(">", 1)[1].rsplit("<", 1)[0]


@requires_node
def test_timeout_script_is_valid_javascript():
    _check_syntax(_script_text(timeout_script()))


@requires_node
def test_timeout_script_declares_a_proper_event_array():
    js = _script_text(timeout_script())
    # Le defaut historique : les evenements colles sans virgule.
    assert "'mousemove', 'keydown'" in js
    assert "'mousemove' 'keydown'" not in js


@requires_node
@pytest.mark.parametrize("name", ["shiny-utils.js", "bindEnter.js"])
def test_embedded_assets_are_valid_javascript(name):
    path = Path(str(files("shinymanager").joinpath("www", name)))
    _check_syntax(path.read_text(encoding="utf-8"))
