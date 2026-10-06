"""Test de fumee du harnais : prouve que la chaine R (clone source) -> Python tourne.

Cas trivial : `generate_pwd()` cote R doit retourner une chaine non vide. On ne compare pas
encore a une implementation Python (module 3 non implemente) : on valide seulement que le
runner charge le source R (1.1.1.1) et evalue une fonction du package.
"""

import pytest


@pytest.mark.oracle
def test_generate_pwd_runs_in_r(require_r, tmp_path):
    out = require_r.run_r_cases([{"id": "gen-01", "fn": "generate_pwd", "args": {}}], tmp_path)
    assert "source" in out["loaded_from"] or "installed" in out["loaded_from"]
    res = out["results"][0]
    assert res["ok"], f"erreur R: {res['error']}"
    assert isinstance(res["value"], str)
    assert len(res["value"]) > 0
