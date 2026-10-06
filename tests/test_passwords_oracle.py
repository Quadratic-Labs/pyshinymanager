"""Phase 7 — validation differentielle du module passwords contre le package R source.

Trois volets :
  1. R hache -> Python verifie (compat de format R -> Python).
  2. Python hache -> R verifie (compat de format Python -> R, bidirectionnelle).
  3. validate_pwd : parite de la politique sur un jeu de mots de passe.
"""

import json

import pytest

from shinymanager.passwords import hash_pwd, validate_pwd, verify_pwd


@pytest.mark.oracle
def test_scrypt_and_validate_parity_with_r(require_r, tmp_path):
    out = require_r.run_r_script("oracle_passwords.R", tmp_path)

    # 1. Hash produits par R -> verifiables cote Python.
    for item in out["r_hashes"]:
        assert verify_pwd(item["hash"], item["pwd"]) is True, f"R->Py echoue: {item['pwd']}"
        assert verify_pwd(item["hash"], item["pwd"] + "X") is False

    # 3. validate_pwd : meme verdict que R.
    for item in out["r_validate"]:
        assert validate_pwd(item["pwd"]) == item["valid"], f"validate divergent: {item['pwd']}"

    # 2. Hash produits par Python -> verifiables cote R (bidirectionnel).
    pwds = [item["pwd"] for item in out["r_hashes"]]
    py_hashes = [{"pwd": p, "hash": hash_pwd(p)} for p in pwds]
    py_file = tmp_path / "py_hashes.json"
    py_file.write_text(json.dumps(py_hashes), encoding="utf-8")
    out2 = require_r.run_r_script("oracle_passwords.R", tmp_path, extra_args=[str(py_file)])
    assert out2["py_verify"] is not None
    for item in out2["py_verify"]:
        assert item["verify_ok"] is True, f"R ne valide pas le hash Python: {item['pwd']}"
        assert item["verify_ko"] is False
