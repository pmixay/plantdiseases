from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from account_auth import (
    AccountAuthenticationError,
    AccountAuthenticationUnavailable,
    RemoteAccountAuthenticator,
    UnconfiguredAccountAuthenticator,
    _NoRedirectHandler,
    _open_no_redirect,
    build_account_authenticator,
)

ISSUER = "https://accounts.synthetic.invalid"
AUDIENCE = "plantdiseases-app"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _fixture_token(*, kid: str = "key-v1", **claim_overrides):
    key = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    claims = {
        "sid": str(uuid.uuid4()),
        "act": str(uuid.uuid4()),
        "nick": "Synthetic Fern",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
        "av": 3,
    }
    claims.update(claim_overrides)
    token = jwt.encode(
        claims,
        key,
        algorithm="EdDSA",
        headers={"kid": kid, "typ": "JWT"},
    )
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    jwks = {
        "keys": [
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "use": "sig",
                "alg": "EdDSA",
                "kid": kid,
                "x": _b64url(public),
            }
        ]
    }
    introspection = {
        "session_id": claims["sid"],
        "actor_id": claims["act"],
        "nickname": claims["nick"],
        "authorization_version": claims["av"],
    }
    return token, jwks, introspection, key.public_key()


class _Response:
    def __init__(self, body: dict, status: int = 200):
        self.status = status
        self._raw = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit: int) -> bytes:
        return self._raw[:limit]


def _authenticator() -> RemoteAccountAuthenticator:
    return RemoteAccountAuthenticator(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url=f"{ISSUER}/.well-known/jwks.json",
        introspection_url=f"{ISSUER}/v1/internal/auth/verify",
        internal_service_token="s" * 43,
    )


def test_valid_token_is_verified_locally_then_confirmed_live(monkeypatch):
    token, jwks, introspection, _public = _fixture_token()
    calls: list[str] = []

    def urlopen(request, timeout):
        assert timeout == 2.0
        calls.append(request.full_url)
        if request.full_url.endswith("jwks.json"):
            return _Response(jwks)
        assert request.get_header("X-internal-service-token") == "s" * 43
        return _Response(introspection)

    monkeypatch.setattr("account_auth._open_no_redirect", urlopen)
    principal = _authenticator().authenticate(token)
    assert principal.actor_id == introspection["actor_id"]
    assert principal.nickname == "Synthetic Fern"
    assert calls == [
        f"{ISSUER}/.well-known/jwks.json",
        f"{ISSUER}/v1/internal/auth/verify",
    ]


def test_unknown_kid_gets_exactly_one_bounded_refresh(monkeypatch):
    token, jwks, introspection, _public = _fixture_token(kid="rotated-v2")
    authenticator = _authenticator()
    authenticator._keys = {"old-v1": Ed25519PrivateKey.generate().public_key()}
    authenticator._keys_loaded_at = time.monotonic()
    jwks_calls = 0

    def urlopen(request, timeout):
        nonlocal jwks_calls
        if request.full_url.endswith("jwks.json"):
            jwks_calls += 1
            return _Response(jwks)
        return _Response(introspection)

    monkeypatch.setattr("account_auth._open_no_redirect", urlopen)
    assert authenticator.authenticate(token).authorization_version == 3
    assert jwks_calls == 1


def test_repeated_and_concurrent_unknown_kid_uses_one_negative_cached_refresh(
    monkeypatch,
):
    token, _unknown_jwks, _introspection, _public = _fixture_token(kid="unknown-v9")
    _old_token, old_jwks, _old_introspection, old_public = _fixture_token(kid="old-v1")
    authenticator = _authenticator()
    authenticator._keys = {"old-v1": old_public}
    authenticator._keys_loaded_at = time.monotonic()
    jwks_calls = 0
    call_lock = threading.Lock()

    def urlopen(request, timeout):
        nonlocal jwks_calls
        assert request.full_url.endswith("jwks.json")
        with call_lock:
            jwks_calls += 1
        return _Response(old_jwks)

    monkeypatch.setattr("account_auth._open_no_redirect", urlopen)

    def reject_unknown() -> None:
        with pytest.raises(AccountAuthenticationError):
            authenticator.authenticate(token)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _index: reject_unknown(), range(8)))
    reject_unknown()
    assert jwks_calls == 1


def test_random_unknown_kids_share_the_global_refresh_backoff(monkeypatch):
    _old_token, old_jwks, _old_introspection, old_public = _fixture_token(
        kid="old-v1"
    )
    authenticator = _authenticator()
    authenticator._keys = {"old-v1": old_public}
    authenticator._keys_loaded_at = time.monotonic()
    jwks_calls = 0

    def urlopen(request, timeout):
        nonlocal jwks_calls
        assert request.full_url.endswith("jwks.json")
        jwks_calls += 1
        return _Response(old_jwks)

    monkeypatch.setattr("account_auth._open_no_redirect", urlopen)

    with pytest.raises(ValueError, match="unknown signing key"):
        authenticator._key_for("attacker-kid-one")
    with pytest.raises(ValueError, match="unknown signing key"):
        authenticator._key_for("attacker-kid-two")

    assert jwks_calls == 1


@pytest.mark.parametrize(
    "override",
    [
        {"aud": "wrong-app"},
        {"aud": [AUDIENCE]},
        {"iss": "https://wrong.synthetic.invalid"},
        {"av": 0},
        {"unexpected": "claim"},
        # ``exp`` alone must not decide how far a session reaches. The account
        # service mints exactly a 15-minute token; one signed by a trusted key
        # but carrying a year of validity is outside that published policy, so
        # it is refused here rather than honoured on the issuer's arithmetic.
        {"exp": int((datetime.now(UTC) + timedelta(days=365)).timestamp())},
    ],
)
def test_invalid_claim_contract_fails_before_introspection(monkeypatch, override):
    token, jwks, _introspection, _public = _fixture_token(**override)
    calls = 0

    def urlopen(_request, timeout):
        assert timeout == 2.0
        nonlocal calls
        calls += 1
        return _Response(jwks)

    monkeypatch.setattr("account_auth._open_no_redirect", urlopen)
    with pytest.raises(AccountAuthenticationError):
        _authenticator().authenticate(token)
    assert calls == 1


def test_revoked_or_version_changed_session_fails_closed(monkeypatch):
    token, jwks, introspection, _public = _fixture_token()
    introspection["authorization_version"] += 1

    def urlopen(request, timeout):
        assert timeout == 2.0
        return _Response(jwks if request.full_url.endswith("jwks.json") else introspection)

    monkeypatch.setattr("account_auth._open_no_redirect", urlopen)
    with pytest.raises(AccountAuthenticationError):
        _authenticator().authenticate(token)


def test_malformed_introspection_types_fail_as_dependency_outage(monkeypatch):
    token, jwks, introspection, _public = _fixture_token(av=1)
    introspection["authorization_version"] = True

    def urlopen(request, timeout):
        assert timeout == 2.0
        return _Response(jwks if request.full_url.endswith("jwks.json") else introspection)

    monkeypatch.setattr("account_auth._open_no_redirect", urlopen)
    with pytest.raises(AccountAuthenticationUnavailable):
        _authenticator().authenticate(token)


def test_account_dependency_outage_is_not_misreported_as_bad_credentials(monkeypatch):
    token, _jwks, _introspection, _public = _fixture_token()

    def unavailable(request, timeout):
        assert timeout == 2.0
        raise urllib.error.URLError("synthetic outage")

    monkeypatch.setattr("account_auth._open_no_redirect", unavailable)
    with pytest.raises(AccountAuthenticationUnavailable):
        _authenticator().authenticate(token)


def test_missing_configuration_constructs_a_fail_closed_authenticator(monkeypatch):
    for name in (
        "ENVIRONMENT",
        "ACCOUNT_ISSUER",
        "ACCOUNT_AUDIENCE",
        "ACCOUNT_JWKS_URL",
        "ACCOUNT_INTROSPECTION_URL",
        "INTERNAL_SERVICE_TOKEN_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    assert isinstance(
        RemoteAccountAuthenticator.from_env(), UnconfiguredAccountAuthenticator
    )
    with pytest.raises(RuntimeError, match="required but misconfigured"):
        build_account_authenticator(expected=True)


def test_credentialed_http_helper_installs_a_redirect_rejecting_handler(monkeypatch):
    installed: list[object] = []

    class Opener:
        def open(self, request, *, timeout):
            assert timeout == 1.0
            assert request.get_header("Authorization") == "Bearer secret-jwt"
            raise urllib.error.HTTPError(
                request.full_url, 302, "redirect rejected", {}, None
            )

    def build_opener(handler):
        installed.append(handler)
        return Opener()

    monkeypatch.setattr("account_auth.urllib.request.build_opener", build_opener)
    request = __import__("urllib.request").request.Request(
        "https://account.invalid/private",
        data=b"{}",
        headers={"Authorization": "Bearer secret-jwt"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as rejected:
        _open_no_redirect(request, timeout=1.0)
    # An HTTPError is also the response: whoever catches it closes it.
    rejected.value.close()

    assert len(installed) == 1
    assert isinstance(installed[0], _NoRedirectHandler)
    assert installed[0].redirect_request(None, None, 302, "", {}, "https://evil.invalid") is None


@pytest.mark.parametrize(
    ("environment", "jwks_url"),
    [
        ("prod", f"{ISSUER}/.well-known/jwks.json"),
        ("test", "https://accounts.synthetic.invalid:444/.well-known/jwks.json"),
    ],
)
def test_unsafe_auth_service_configuration_fails_closed(
    monkeypatch, tmp_path, environment, jwks_url,
):
    token_file = tmp_path / "internal.token"
    token_file.write_text("s" * 43, encoding="ascii")
    token_file.chmod(0o600)
    values = {
        "ENVIRONMENT": environment,
        "ACCOUNT_ISSUER": ISSUER,
        "ACCOUNT_AUDIENCE": AUDIENCE,
        "ACCOUNT_JWKS_URL": jwks_url,
        "ACCOUNT_INTROSPECTION_URL": f"{ISSUER}/v1/internal/auth/verify",
        "INTERNAL_SERVICE_TOKEN_FILE": str(token_file),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    assert isinstance(
        RemoteAccountAuthenticator.from_env(), UnconfiguredAccountAuthenticator
    )


def test_explicit_private_account_origin_keeps_public_issuer(monkeypatch, tmp_path):
    token_file = tmp_path / "internal.token"
    token_file.write_text("s" * 43, encoding="ascii")
    token_file.chmod(0o600)
    values = {
        "ENVIRONMENT": "production",
        "ACCOUNT_ISSUER": ISSUER,
        "ACCOUNT_AUDIENCE": AUDIENCE,
        "ACCOUNT_INTERNAL_BASE_URL": "http://account-api:8000",
        "ACCOUNT_JWKS_URL": "http://account-api:8000/.well-known/jwks.json",
        "ACCOUNT_INTROSPECTION_URL": (
            "http://account-api:8000/v1/internal/auth/verify"
        ),
        "INTERNAL_SERVICE_TOKEN_FILE": str(token_file),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    authenticator = RemoteAccountAuthenticator.from_env()

    assert isinstance(authenticator, RemoteAccountAuthenticator)
    assert authenticator.issuer == ISSUER
    assert authenticator.jwks_url.startswith("http://account-api:8000/")


def test_group_readable_internal_token_supports_separate_service_volume(
    monkeypatch,
    tmp_path,
):
    token_file = tmp_path / "internal.token"
    token_file.write_text("s" * 43, encoding="ascii")
    token_file.chmod(0o640)
    values = {
        "ENVIRONMENT": "production",
        "ACCOUNT_ISSUER": ISSUER,
        "ACCOUNT_AUDIENCE": AUDIENCE,
        "ACCOUNT_INTERNAL_BASE_URL": "http://account-api:8000",
        "ACCOUNT_JWKS_URL": "http://account-api:8000/.well-known/jwks.json",
        "ACCOUNT_INTROSPECTION_URL": (
            "http://account-api:8000/v1/internal/auth/verify"
        ),
        "INTERNAL_SERVICE_TOKEN_FILE": str(token_file),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    assert isinstance(
        RemoteAccountAuthenticator.from_env(), RemoteAccountAuthenticator
    )


@pytest.mark.parametrize("mode", [0o620, 0o604])
def test_internal_token_rejects_group_write_or_world_access(
    monkeypatch,
    tmp_path,
    mode,
):
    token_file = tmp_path / "internal.token"
    token_file.write_text("s" * 43, encoding="ascii")
    token_file.chmod(mode)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ACCOUNT_ISSUER", ISSUER)
    monkeypatch.setenv("ACCOUNT_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("ACCOUNT_JWKS_URL", f"{ISSUER}/.well-known/jwks.json")
    monkeypatch.setenv(
        "ACCOUNT_INTROSPECTION_URL", f"{ISSUER}/v1/internal/auth/verify"
    )
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN_FILE", str(token_file))

    assert isinstance(
        RemoteAccountAuthenticator.from_env(), UnconfiguredAccountAuthenticator
    )


@pytest.mark.parametrize(
    ("internal_base", "jwks_url"),
    [
        (
            "http://accounts.public.invalid",
            "http://accounts.public.invalid/.well-known/jwks.json",
        ),
        ("http://account-api:8000/unsafe", "http://account-api:8000/jwks"),
        ("http://account-api:8000", "http://other-api:8000/.well-known/jwks.json"),
    ],
)
def test_unsafe_explicit_internal_account_origin_fails_closed(
    monkeypatch,
    tmp_path,
    internal_base,
    jwks_url,
):
    token_file = tmp_path / "internal.token"
    token_file.write_text("s" * 43, encoding="ascii")
    token_file.chmod(0o600)
    values = {
        "ENVIRONMENT": "production",
        "ACCOUNT_ISSUER": ISSUER,
        "ACCOUNT_AUDIENCE": AUDIENCE,
        "ACCOUNT_INTERNAL_BASE_URL": internal_base,
        "ACCOUNT_JWKS_URL": jwks_url,
        "ACCOUNT_INTROSPECTION_URL": (
            "http://account-api:8000/v1/internal/auth/verify"
        ),
        "INTERNAL_SERVICE_TOKEN_FILE": str(token_file),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    assert isinstance(
        RemoteAccountAuthenticator.from_env(), UnconfiguredAccountAuthenticator
    )


def test_access_token_at_exactly_the_policy_lifetime_is_still_accepted(monkeypatch):
    """The bound above must not clip a normally issued token."""
    now = datetime.now(UTC)
    token, jwks, _introspection, _public = _fixture_token(
        iat=int(now.timestamp()),
        nbf=int(now.timestamp()),
        exp=int((now + timedelta(minutes=15)).timestamp()),
    )

    monkeypatch.setattr(
        "account_auth._open_no_redirect",
        lambda _request, timeout: _Response(jwks),
    )

    assert _authenticator()._verify_local(token).nickname == "Synthetic Fern"
