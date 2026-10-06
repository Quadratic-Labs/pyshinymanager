"""Phase 8 — tests unitaires du module i18n (comportement, sans dependance R)."""

import copy

import pytest

from shinymanager import i18n
from shinymanager.i18n import Language, get_labels, set_labels, use_language

REGISTERED_CODES = ["en", "fr", "pt-BR", "es", "de", "pl", "ja", "el", "id", "zh-CN", "no", "it"]


@pytest.fixture(autouse=True)
def _restore_global_labels():
    """Restaure l'etat global des labels apres chaque test (set_labels mute _LABELS)."""
    snapshot = copy.deepcopy(i18n._LABELS)
    yield
    i18n._LABELS.clear()
    i18n._LABELS.update(snapshot)


def test_default_language_is_english():
    lan = use_language()
    assert lan.get_language() == "en"


def test_get_unknown_key_returns_key_itself():
    lan = use_language("fr")
    assert lan.get("Cle vraiment inexistante") == "Cle vraiment inexistante"


def test_get_matches_get_all_for_every_key():
    # Invariant du test R existant (test-language.R) porte a l'identique.
    for code in ("en", "fr"):
        lan = use_language(code)
        all_labels = lan.get_all()
        for key in all_labels:
            assert lan.get(key) == all_labels[key]


def test_set_language_unknown_raises():
    lan = Language()
    with pytest.raises(ValueError, match="Unsupported language"):
        lan.set_language("bad")


def test_use_language_unknown_raises():
    with pytest.raises(ValueError, match="Unsupported language"):
        use_language("bad")


def test_get_labels_unknown_warns_and_falls_back_to_en():
    with pytest.warns(UserWarning, match="falling back to 'en'"):
        labels = get_labels("bad")
    assert labels == get_labels("en")


def test_registered_has_the_twelve_codes():
    reg = use_language().get_language_registered()
    assert set(reg.keys()) == set(REGISTERED_CODES)
    assert reg["en"] == "English"
    assert reg["no"] == "Norsk"
    assert reg["it"] == "Italiano"


def test_norwegian_max_users_key_fixed_in_1_1_1():
    # 1.1.1 : la cle typo "Maximum number of users : %s" est corrigee dans le source.
    lan = use_language("no")
    assert lan.get("Maximum number of users: %s") == "Maximalt antall brukere: %s"
    assert "Maximum number of users : %s" not in lan.get_all()


def test_get_dateinput_identity_for_all_languages():
    for code in REGISTERED_CODES:
        assert use_language(code).get_dateInput() == code


def test_get_dt_present_for_all_languages_including_fixed_zhcn():
    # ECART ASSUME (decision Jeremy, gate module 1) : le source ne fournissait pas de traduction
    # DataTables pour zh-CN (cle stockee "cn", jamais atteinte). On la restaure. Les 12 langues
    # ont donc desormais une traduction DT.
    for code in REGISTERED_CODES:
        assert use_language(code).get_DT() is not None, f"DT manquant pour {code}"


def test_set_labels_kwargs_mutates_globally():
    set_labels("en", **{"Please authenticate": "You have to login"})
    assert use_language("en").get("Please authenticate") == "You have to login"


def test_set_labels_dict_form_equivalent():
    set_labels("en", {"Login": "Sign in please"})
    assert use_language("en").get("Login") == "Sign in please"


def test_set_labels_unknown_language_raises():
    with pytest.raises(ValueError, match="Unsupported language"):
        set_labels("bad", x="y")


def test_set_labels_rejects_dict_and_kwargs_together():
    with pytest.raises(ValueError, match="either a single dict or named labels"):
        set_labels("en", {"a": "b"}, c="d")
