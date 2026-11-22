from __future__ import annotations

import pytest

import account_internal


@pytest.mark.parametrize(
    "supplied",
    [
        # A latin-1 header byte, the same byte pair after a UTF-8 round trip,
        # and a wholly non-Latin candidate. Each one used to raise TypeError.
        "é" + "a" * 39,
        "Ã©" + "a" * 38,
        "токен" * 8,
    ],
)
def test_internal_service_authorization_rejects_non_ascii_without_raising(
    monkeypatch, supplied,
):
    """A high byte in the header is a wrong credential, never a 500.

    ``secrets.compare_digest`` raises TypeError on non-ASCII ``str`` operands,
    and Starlette hands this function a decoded header value, so the internal
    routes used to answer an unauthenticated request with a bare 500 and a
    server traceback instead of their stable 401.
    """
    monkeypatch.setattr(account_internal, "_internal_service_token", "s" * 48)

    assert account_internal.internal_service_authorized(supplied) is False


def test_internal_service_authorization_still_accepts_the_configured_token(monkeypatch):
    monkeypatch.setattr(account_internal, "_internal_service_token", "s" * 48)

    assert account_internal.internal_service_authorized("s" * 48) is True
    assert account_internal.internal_service_authorized("s" * 47) is False
    assert account_internal.internal_service_authorized("") is False


def test_internal_account_deletion_route_answers_401_for_a_non_ascii_token(
    client, monkeypatch,
):
    monkeypatch.setattr(account_internal, "_internal_service_token", "s" * 48)

    response = client.post(
        "/api/internal/account-deletions",
        headers={b"X-Internal-Service-Token": b"\xe9" + b"a" * 39},
        json={"actor_id": "00000000-0000-0000-0000-000000000001"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "auth_required"}
