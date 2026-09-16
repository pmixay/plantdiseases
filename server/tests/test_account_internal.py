from __future__ import annotations

import io
import uuid

import pytest

import account_internal
from account_auth import _NoRedirectHandler


class _Response(io.BytesIO):
    def __init__(self, body: bytes, *, status: int = 202):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _Opener:
    def __init__(self, response: _Response):
        self.response = response

    def open(self, _request, *, timeout):  # noqa: ANN001
        assert timeout == 2.0
        return self.response


def test_internal_notification_delivery_refuses_redirects():
    handler = _NoRedirectHandler()
    assert handler.redirect_request(None, None, 307, "moved", {}, "https://evil.invalid") is None


def test_internal_notification_delivery_rejects_oversized_response(monkeypatch):
    opener = _Opener(
        _Response(b"x" * (account_internal._MAX_RESPONSE_BYTES + 1)),
    )
    monkeypatch.setattr(account_internal, "_open_no_redirect", opener.open)
    client = account_internal.AccountInternalClient(
        notification_url="https://account.invalid/v1/internal/notifications/opinion",
        service_token="s" * 64,
    )

    assert client.send_opinion(str(uuid.uuid4()), "scan", "healthy", "", "tester") is False


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
