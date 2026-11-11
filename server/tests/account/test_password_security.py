from __future__ import annotations

import pytest
from app.security.passwords import PasswordHashService, normalize_email
from argon2 import PasswordHasher
from argon2.low_level import Type


def _test_hasher(*, time_cost: int = 1) -> PasswordHasher:
    return PasswordHasher(
        time_cost=time_cost,
        memory_cost=8_192,
        parallelism=1,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )


def test_email_normalization_is_stable_without_accepting_ambiguous_addresses():
    assert normalize_email("  Garden.Fern@EXAMPLE.COM. ") == "garden.fern@example.com"
    assert normalize_email("leaf@exämple.de") == "leaf@xn--exmple-cua.de"

    for invalid in (
        "missing-at.example",
        "two@@example.com",
        ".leading@example.com",
        "double..dot@example.com",
        "unicode-лист@example.com",
        "leaf@localhost",
    ):
        with pytest.raises(ValueError, match="invalid email"):
            normalize_email(invalid)


def test_password_hashes_are_argon2id_and_dummy_verification_is_indistinguishable():
    service = PasswordHashService(_test_hasher())
    password = "correct horse garden staple"
    encoded = service.hash_new(password)

    assert encoded.startswith("$argon2id$")
    assert password not in encoded
    assert service.verify(password, encoded).valid is True
    assert service.verify("a different long passphrase", encoded).valid is False
    assert service.verify("a different long passphrase", None).valid is False


def test_production_hash_cost_is_pinned_to_the_reviewed_memory_budget():
    encoded = PasswordHashService().hash_new("correct horse garden staple")
    assert encoded.startswith("$argon2id$v=19$m=65536,t=3,p=4$")


def test_password_policy_and_hash_upgrade_are_enforced():
    old_hasher = _test_hasher(time_cost=1)
    service = PasswordHashService(_test_hasher(time_cost=2))

    with pytest.raises(ValueError, match="invalid password"):
        service.hash_new("too short")

    verification = service.verify(
        "correct horse garden staple",
        old_hasher.hash("correct horse garden staple"),
    )
    assert verification.valid is True
    assert verification.replacement_hash is not None
    assert verification.replacement_hash.startswith("$argon2id$")


def test_password_hash_work_limit_must_be_positive():
    with pytest.raises(ValueError, match="max_concurrent must be positive"):
        PasswordHashService(_test_hasher(), max_concurrent=0)
