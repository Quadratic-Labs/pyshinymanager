"""Phase 8 — tests unitaires du module passwords (comportement, sans dependance R)."""

import base64

import pytest

from shinymanager.passwords import generate_pwd, hash_pwd, validate_pwd, verify_pwd


def test_generate_pwd_single_is_8_char_base64():
    pwd = generate_pwd()
    assert isinstance(pwd, str)
    assert len(pwd) == 8
    # 8 caracteres base64 = 6 octets.
    assert len(base64.b64decode(pwd)) == 6


def test_generate_pwd_multiple_returns_list_of_distinct():
    pwds = generate_pwd(3)
    assert isinstance(pwds, list)
    assert len(pwds) == 3
    assert len(set(pwds)) == 3


def test_generate_pwd_two_calls_differ():
    assert generate_pwd() != generate_pwd()


@pytest.mark.parametrize(
    ("pwd", "expected"),
    [
        ("Abc123", True),
        ("abc123", False),  # pas de majuscule
        ("ABC123", False),  # pas de minuscule
        ("Abcdef", False),  # pas de chiffre
        ("Ab1", False),  # < 6 caracteres
        ("Abcde1", True),
    ],
)
def test_validate_pwd(pwd, expected):
    assert validate_pwd(pwd) is expected


def test_hash_then_verify_roundtrip():
    h = hash_pwd("S3cret!")
    assert verify_pwd(h, "S3cret!") is True
    assert verify_pwd(h, "s3cret!") is False


def test_hash_uses_random_salt():
    assert hash_pwd("same") != hash_pwd("same")


def test_verify_malformed_hash_returns_false():
    assert verify_pwd("pas-du-base64-valide!!", "x") is False
    assert verify_pwd(base64.b64encode(b"trop court").decode(), "x") is False
