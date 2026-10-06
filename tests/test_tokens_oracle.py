"""Phase 7 — validation differentielle du store de tokens contre le package R source.

Rejoue cote Python les memes sequences deterministes que `tools/oracle/oracle_tokens.R` et
compare les sorties observables (anti-rejeu, is_admin/coercition, get, remove).
"""

import pytest

from shinymanager.tokens import TokenStore


@pytest.mark.oracle
def test_token_sequences_match_r(require_r, tmp_path):
    r = require_r.run_r_script("oracle_tokens.R", tmp_path)

    tok = TokenStore()

    # S1 : anti-rejeu is_valid + reset_count
    tok.add("t1", {"user": "alice", "admin": "TRUE"})
    s1 = [tok.is_valid("t1"), tok.is_valid("t1")]
    tok.reset_count("t1")
    s1_reset = [tok.is_valid("t1"), tok.is_valid("t1")]
    assert s1 == r["s1_is_valid_seq"]
    assert s1_reset == r["s1_after_reset"]

    # S2 : is_valid_server stable
    s2 = [tok.is_valid_server("t1") for _ in range(3)]
    assert s2 == r["s2_server_seq"]

    # S3 : is_admin coercition
    tok.add("adm_true", {"user": "a", "admin": "TRUE"})
    tok.add("adm_false", {"user": "b", "admin": "FALSE"})
    tok.add("adm_t", {"user": "c", "admin": "T"})
    tok.add("adm_yes", {"user": "d", "admin": "yes"})
    tok.add("adm_one", {"user": "e", "admin": "1"})
    tok.add("adm_missing", {"user": "f"})
    py_admin = {
        "adm_true": tok.is_admin("adm_true"),
        "adm_false": tok.is_admin("adm_false"),
        "adm_t": tok.is_admin("adm_t"),
        "adm_yes": tok.is_admin("adm_yes"),
        "adm_one": tok.is_admin("adm_one"),
        "adm_missing": tok.is_admin("adm_missing"),
        "adm_unknown": tok.is_admin("jamais-vu"),
    }
    assert py_admin == r["s3_is_admin"]

    # S4 : get exclut l'horodatage ; get_user
    assert sorted(tok.get("t1").keys()) == r["s4_get_keys"]
    assert tok.get_user("t1") == r["s4_get_user"]

    # S5 : is_valid sur inconnu
    assert tok.is_valid("jamais-vu") == r["s5_unknown"]

    # S6 : remove. is_valid_server concorde avec R (False des deux cotes). En revanche get_user
    # est une DEVIATION ASSUMEE (DT1) : le source fuit et renvoie encore "alice", Python purge
    # et renvoie None. On verifie donc explicitement la deviation, sans exiger l'egalite avec R.
    tok.remove("t1")
    assert tok.is_valid_server("t1") == r["s6_valid_server_after_remove"]
    assert r["s6_get_user_after_remove"] == "alice"  # comportement source (fuite)
    assert tok.get_user("t1") is None  # comportement Python corrige (ecart assume DT1)

    # S7 : timeout <= 0 -> toujours valide
    tok.set_timeout(0)
    assert tok.is_valid_timeout("adm_true") == r["s7_timeout_zero"]
