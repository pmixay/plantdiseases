from __future__ import annotations

import io
import uuid

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
