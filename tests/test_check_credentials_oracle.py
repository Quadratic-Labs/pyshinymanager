"""Phase 7 — validation differentielle de check_credentials (chemin data.frame) contre R.

Compare les 3 booleens du contrat (result/expired/authorized) sur 10 scenarios (clair, hash,
expiration, applications). L'appname est fixe des deux cotes pour etre deterministe.
"""

import pytest

from shinymanager import settings
from shinymanager.check_credentials import check_credentials


@pytest.fixture(autouse=True)
def _appname():
    settings.reset_options()
    settings.set_option("application", "myapp")
    yield
    settings.reset_options()


def _triple(auth):
    return {"result": auth["result"], "expired": auth["expired"], "authorized": auth["authorized"]}


@pytest.mark.oracle
def test_check_credentials_df_matches_r(require_r, tmp_path):
    r = require_r.run_r_script("oracle_check_credentials.R", tmp_path)
    hashed = r["hashed_value"]

    import datetime

    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

    clear = [{"user": "fanny", "password": "azerty"}]
    assert _triple(check_credentials(clear)("fanny", "azerty")) == r["clear_ok"]
    assert _triple(check_credentials(clear)("fanny", "wrong")) == r["clear_wrong"]
    assert _triple(check_credentials(clear)("ghost", "x")) == r["ghost"]

    hdf = [{"user": "fanny", "password": hashed, "is_hashed_password": "TRUE"}]
    assert _triple(check_credentials(hdf)("fanny", "azerty")) == r["hashed_ok"]
    assert _triple(check_credentials(hdf)("fanny", "nope")) == r["hashed_wrong"]

    exp_past = [{"user": "u", "password": "p", "expire": yesterday}]
    assert _triple(check_credentials(exp_past)("u", "p")) == r["expire_past"]
    exp_future = [{"user": "u", "password": "p", "expire": tomorrow}]
    assert _triple(check_credentials(exp_future)("u", "p")) == r["expire_future"]

    app_match = [{"user": "u", "password": "p", "applications": "myapp;other"}]
    assert _triple(check_credentials(app_match)("u", "p")) == r["app_match"]
    app_nomatch = [{"user": "u", "password": "p", "applications": "other;nope"}]
    assert _triple(check_credentials(app_nomatch)("u", "p")) == r["app_nomatch"]
