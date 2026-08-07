from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.api.errors import AccountAPIError
from app.config import Settings, SettingsError
from app.security.push_crypto import PushTokenProtector
from app.security.tokens import (
    SigningKeyRing,
    TokenService,
    hash_opaque_token,
    new_opaque_token,
)

NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)


def _service(key_ring: SigningKeyRing | None = None) -> TokenService:
    return TokenService(
        issuer="https://id.example.invalid",
        audience="plantdiseases-app",
        key_ring=key_ring or SigningKeyRing.ephemeral(),
    )


def _issue(service: TokenService, *, now: datetime = NOW) -> str:
    return service.issue_access(
        session_id=uuid.uuid4(),
        community_actor_id=uuid.uuid4(),
        nickname="SyntheticLeaf",
        authorization_version=3,
        now=now,
    )


def _raw_private(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _raw_public(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_access_token_has_only_allowlisted_claims_and_ed25519_header():
    service = _service()
    token = _issue(service)
    header = jwt.get_unverified_header(token)
    claims = jwt.decode(token, options={"verify_signature": False})

    assert header == {"alg": "EdDSA", "kid": service.key_ring.active_kid, "typ": "JWT"}
    assert set(claims) == {"sid", "act", "nick", "iss", "aud", "iat", "nbf", "exp", "av"}
    assert claims["exp"] - claims["iat"] == 15 * 60
    forbidden = {
        "sub",
        "account_id",
        "provider_subject",
        "provider_token",
        "email",
        "phone",
        "avatar",
        "display_name",
    }
    assert set(claims).isdisjoint(forbidden)
    assert service.decode_access(token, now=NOW).nickname == "SyntheticLeaf"


def test_expired_tampered_or_wrong_audience_tokens_fail_closed():
    service = _service()
    token = _issue(service)

    with pytest.raises(AccountAPIError, match="auth_required"):
        service.decode_access(token, now=NOW + timedelta(minutes=16))
    with pytest.raises(AccountAPIError, match="auth_required"):
        service.decode_access(f"{token[:-1]}x", now=NOW)

    wrong_audience = TokenService(
        issuer=service.issuer,
        audience="another-app",
        key_ring=service.key_ring,
    )
    with pytest.raises(AccountAPIError, match="auth_required"):
        wrong_audience.decode_access(token, now=NOW)


def test_extra_claims_are_rejected_even_when_correctly_signed():
    service = _service()
    token = _issue(service)
    claims = jwt.decode(token, options={"verify_signature": False})
    claims["provider_subject"] = "must-not-be-accepted"
    token_with_extra_claim = service.key_ring.sign(claims)

    with pytest.raises(AccountAPIError, match="auth_required"):
        service.decode_access(token_with_extra_claim, now=NOW)


def test_unsigned_and_unknown_kid_tokens_are_rejected():
    service = _service()
    claims = jwt.decode(_issue(service), options={"verify_signature": False})
    unsigned = jwt.encode(claims, key=None, algorithm="none")
    with pytest.raises(AccountAPIError, match="auth_required"):
        service.decode_access(unsigned, now=NOW)

    another_service = _service()
    with pytest.raises(AccountAPIError, match="auth_required"):
        service.decode_access(_issue(another_service), now=NOW)


def test_jwks_keeps_retired_public_key_for_rotation_overlap():
    old_private = Ed25519PrivateKey.generate()
    old_ring = SigningKeyRing(active_kid="old-key", active_private_key=old_private)
    old_token = _issue(_service(old_ring))

    current_private = Ed25519PrivateKey.generate()
    current_ring = SigningKeyRing(
        active_kid="current-key",
        active_private_key=current_private,
        retired_public_keys={"old-key": old_private.public_key()},
    )
    current_service = _service(current_ring)

    assert current_service.decode_access(old_token, now=NOW).nickname == "SyntheticLeaf"
    jwks = current_service.jwks()
    assert [key["kid"] for key in jwks["keys"]] == ["current-key", "old-key"]
    assert all(
        {"kty": key["kty"], "crv": key["crv"], "alg": key["alg"], "use": key["use"]}
        == {"kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "use": "sig"}
        for key in jwks["keys"]
    )


def test_signing_key_file_loads_active_and_retired_keys(tmp_path: Path):
    current = Ed25519PrivateKey.generate()
    old = Ed25519PrivateKey.generate()
    key_path = tmp_path / "signing.json"
    key_path.write_text(
        json.dumps(
            {
                "active_kid": "current",
                "keys": [
                    {"kid": "current", "private_key": _raw_private(current)},
                    {"kid": "old", "public_key": _raw_public(old)},
                ],
            }
        ),
        encoding="utf-8",
    )
    key_path.chmod(0o600)

    ring = SigningKeyRing.from_file(key_path)
    assert [key["kid"] for key in ring.jwks()["keys"]] == ["current", "old"]


def test_opaque_tokens_have_256_bits_and_are_stored_as_sha256_only():
    first = new_opaque_token()
    second = new_opaque_token()
    assert first != second
    assert len(base64.urlsafe_b64decode(first + "=")) == 32
    assert len(hash_opaque_token(first)) == 32
    assert first.encode() not in hash_opaque_token(first)


def test_push_tokens_use_randomized_aead_and_keyed_fingerprints():
    protector = PushTokenProtector(b"k" * 32)
    token = "synthetic-fcm-token-with-no-real-device-0001"
    first = protector.encrypt(token)
    second = protector.encrypt(token)

    assert first != second
    assert token.encode() not in first
    assert protector.decrypt(first) == token
    assert protector.fingerprint(token) == protector.fingerprint(token)
    assert protector.fingerprint(token) != protector.fingerprint(f"{token}-different")


def test_ephemeral_keys_are_explicit_and_test_only():
    base = {
        "ENVIRONMENT": "development",
        "AUTH_PROVIDER_MODE": "mock",
        "ACCOUNT_ISSUER": "http://localhost:8000",
        "ACCOUNT_AUDIENCE": "plantdiseases-app",
        "ACCOUNT_DATABASE_URL": "postgresql+psycopg://beta:synthetic@localhost/beta",
        "ACCOUNT_ALLOW_EPHEMERAL_KEYS": "true",
    }
    with pytest.raises(SettingsError, match="only in test"):
        Settings.from_env(base)

    test_settings = Settings.from_env(base | {"ENVIRONMENT": "test"})
    assert TokenService.from_settings(test_settings).jwks()["keys"]
    assert PushTokenProtector.from_settings(test_settings)


def test_malformed_encryption_key_file_fails_closed(tmp_path: Path):
    key_path = tmp_path / "encryption.key"
    key_path.write_text("!" * 43, encoding="ascii")
    key_path.chmod(0o600)
    settings = Settings.from_env(
        {
            "ENVIRONMENT": "test",
            "AUTH_PROVIDER_MODE": "mock",
            "ACCOUNT_ISSUER": "http://localhost:8000",
            "ACCOUNT_AUDIENCE": "plantdiseases-app",
            "ACCOUNT_DATABASE_URL": "postgresql+psycopg://beta:synthetic@localhost/beta",
            "ACCOUNT_ENCRYPTION_KEY_FILE": str(key_path),
        }
    )
    with pytest.raises(SettingsError, match="Invalid account encryption key"):
        PushTokenProtector.from_settings(settings)
