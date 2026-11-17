"""Tests unitaires du store de tokens (comportement, sans dependance R)."""

import time

from shinymanager.tokens import TokenStore, _r_as_logical_is_true


def test_generate_is_64_hex_and_unique():
    tok = TokenStore()
    a = tok.generate("alice")
    b = tok.generate("alice")
    assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)
    assert a != b


def test_is_valid_is_one_shot_and_reset_restores_one_credit():
    tok = TokenStore()
    tok.add("t", {"user": "u"})
    assert tok.is_valid("t") is True
    assert tok.is_valid("t") is False
    tok.reset_count("t")
    assert tok.is_valid("t") is True
    assert tok.is_valid("t") is False


def test_is_valid_server_is_stable_and_independent_of_counter():
    tok = TokenStore()
    tok.add("t", {"user": "u"})
    assert [tok.is_valid_server("t") for _ in range(3)] == [True, True, True]
    # is_valid_server ne consomme pas le credit is_valid
    assert tok.is_valid("t") is True


def test_is_valid_none_and_unknown_return_false():
    tok = TokenStore()
    assert tok.is_valid(None) is False
    assert tok.is_valid("never") is False


def test_get_excludes_datetime_and_unknown_returns_none():
    tok = TokenStore()
    tok.add("t", {"user": "u", "admin": "TRUE"})
    got = tok.get("t")
    assert got == {"user": "u", "admin": "TRUE"}
    assert "shinymanager_datetime" not in got
    assert tok.get("never") is None


def test_is_admin_r_coercion():
    tok = TokenStore()
    tok.add("a", {"admin": "TRUE"})
    tok.add("b", {"admin": "FALSE"})
    tok.add("c", {"admin": True})
    tok.add("d", {"admin": "yes"})
    tok.add("e", {})
    assert tok.is_admin("a") is True
    assert tok.is_admin("c") is True
    assert tok.is_admin("b") is False
    assert tok.is_admin("d") is False
    assert tok.is_admin("e") is False
    assert tok.is_admin("unknown") is False


def test_r_as_logical_helper():
    assert _r_as_logical_is_true("TRUE") is True
    assert _r_as_logical_is_true("true") is True
    assert _r_as_logical_is_true("T") is True
    assert _r_as_logical_is_true(True) is True
    assert _r_as_logical_is_true("FALSE") is False
    assert _r_as_logical_is_true("1") is False
    assert _r_as_logical_is_true(None) is False


def test_remove_purges_everything():
    # Ecart volontaire : contrairement au source, remove purge tout (pas de fuite ni de
    # retention d'infos apres logout).
    tok = TokenStore()
    tok.add("t", {"user": "u"})
    tok.is_valid("t")  # alimente le compteur
    tok.remove("t")
    assert tok.is_valid_server("t") is False
    assert tok.get_user("t") is None
    assert tok.get("t") is None
    assert tok._tokens_count == []


def test_remove_on_empty_store_is_noop():
    tok = TokenStore()
    tok.remove("whatever")  # ne doit pas lever


def test_timeout_zero_always_valid():
    tok = TokenStore()
    tok.add("t", {"user": "u"})
    tok.set_timeout(0)
    assert tok.is_valid_timeout("t") is True


def test_timeout_expires_and_update_refreshes():
    tok = TokenStore()
    tok.add("t", {"user": "u"})
    tok.set_timeout(15)  # minutes
    # Force une derniere activite ancienne (16 min).
    tok._tokens_user["t"]["shinymanager_datetime"] = time.time() - 16 * 60
    assert tok.is_valid_timeout("t", update=False) is False
    # Activite recente : valide, et update repousse l'expiration.
    tok._tokens_user["t"]["shinymanager_datetime"] = time.time() - 1 * 60
    assert tok.is_valid_timeout("t", update=True) is True
    assert tok.is_valid_timeout("t", update=False) is True


def test_timeout_unknown_token_is_valid():
    # Un token inconnu (pas d'horodatage) n'est jamais expire par ce chemin.
    tok = TokenStore()
    tok.set_timeout(15)
    assert tok.is_valid_timeout("never") is True


def test_config_setters_getters():
    tok = TokenStore()
    tok.set_sqlite_path("/tmp/db.sqlite")
    tok.set_passphrase("secret")
    tok.set_timeout(15)
    tok.set_sql_config_db({"driver": "pg"})
    assert tok.get_sqlite_path() == "/tmp/db.sqlite"
    assert tok.get_passphrase() == "secret"
    assert tok.get_timeout() == 15
    assert tok.get_sql_config_db() == {"driver": "pg"}


# --- is_active et delai entre deux resets ---------------------------------------------------


def test_is_active_requires_a_known_and_not_timed_out_token(monkeypatch):
    store = TokenStore()
    assert not store.is_active(None)
    assert not store.is_active("unknown")
    store.add("t", {"user": "alice"})
    assert store.is_active("t")
    store.set_timeout(1)
    import time as time_mod

    now = time_mod.time()
    monkeypatch.setattr(time_mod, "time", lambda: now + 120)
    assert not store.is_active("t")
    store.remove("t")
    assert not store.is_active("t")


def test_claim_reset_blocks_a_second_reset_within_the_cooldown():
    store = TokenStore()
    assert store.claim_reset("alice", 60)
    assert not store.claim_reset("alice", 60)
    assert store.claim_reset("bob", 60)
    store.release_reset("alice")
    assert store.claim_reset("alice", 60)
    assert store.claim_reset("carol", 0) and store.claim_reset("carol", 0)
