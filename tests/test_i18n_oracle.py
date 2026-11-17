"""Validation differentielle du module i18n contre le package R source.

Compare, pour les 12 langues, la sortie de `get_labels` cote Python a celle du package R.
Cross-validation reelle : les donnees Python ont ete extraites via `use_language()$get_all()`,
l'oracle interroge ici `get_labels()` — deux points d'entree R distincts qui doivent concorder.
"""

import pytest

from shinymanager.i18n import _CODES, get_labels


@pytest.mark.oracle
def test_labels_match_r_for_all_languages(require_r, tmp_path):
    cases = [{"id": code, "fn": "get_labels", "args": {"language": code}} for code in _CODES]
    out = require_r.run_r_cases(cases, tmp_path)
    assert not out["loaded_from"].startswith("installed:"), (
        f"oracle non charge depuis la source: {out['loaded_from']}"
    )

    for res in out["results"]:
        code = res["id"]
        assert res["ok"], f"erreur R sur {code}: {res['error']}"
        r_labels = res["value"]
        py_labels = get_labels(code)
        assert py_labels == r_labels, f"divergence labels pour {code}"
