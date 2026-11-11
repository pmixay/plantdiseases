from __future__ import annotations

import io
import uuid
from types import SimpleNamespace

from app import community_deletion


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
        self.request = None
        self.timeout = None

    def open(self, request, *, timeout):  # noqa: ANN001
        self.request = request
        self.timeout = timeout
        return self.response


def test_deletion_delivery_refuses_redirects():
    handler = community_deletion._NoRedirectHandler()
    assert handler.redirect_request(None, None, 302, "moved", {}, "https://evil.invalid") is None


def test_deletion_delivery_is_bounded_and_authenticated(monkeypatch):
    opener = _Opener(_Response(b"accepted"))
    monkeypatch.setattr(community_deletion, "_open_no_redirect", opener.open)
    dispatcher = community_deletion.build_community_deletion_dispatcher(
        SimpleNamespace(community_deletion_url="https://app.invalid/api/internal/deletions"),
        service_token="s" * 64,
    )

    assert dispatcher(uuid.uuid4()) is True
    assert opener.timeout == 2.0
    assert opener.request.get_header("X-internal-service-token") == "s" * 64


def test_deletion_delivery_rejects_oversized_response(monkeypatch):
    opener = _Opener(_Response(b"x" * (community_deletion._MAX_RESPONSE_BYTES + 1)))
    monkeypatch.setattr(community_deletion, "_open_no_redirect", opener.open)
    dispatcher = community_deletion.build_community_deletion_dispatcher(
        SimpleNamespace(community_deletion_url="https://app.invalid/api/internal/deletions"),
        service_token="s" * 64,
    )

    assert dispatcher(uuid.uuid4()) is False
