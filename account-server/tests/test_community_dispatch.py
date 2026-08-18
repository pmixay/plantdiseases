from __future__ import annotations

import urllib.error
import urllib.request
import uuid
from types import SimpleNamespace

import pytest

from app.community_deletion import (
    _NoRedirectHandler,
    _open_no_redirect,
    build_community_deletion_dispatcher,
    build_community_nickname_dispatcher,
)


def test_community_dispatchers_use_actor_keyed_fixed_internal_paths(monkeypatch):
    requests: list[urllib.request.Request] = []

    class Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b'{}'

    def open_request(request, *, timeout):
        assert timeout == 2.0
        requests.append(request)
        return Response()

    monkeypatch.setattr("app.community_deletion._open_no_redirect", open_request)
    settings = SimpleNamespace(
        community_deletion_url=(
            "http://community-api:8000/api/internal/account-deletions"
        )
    )
    actor = uuid.uuid4()

    assert build_community_deletion_dispatcher(
        settings, service_token="s" * 43
    )(actor)
    assert build_community_nickname_dispatcher(
        settings, service_token="s" * 43
    )(actor, "New Fern")

    assert [request.full_url for request in requests] == [
        "http://community-api:8000/api/internal/account-deletions",
        "http://community-api:8000/api/internal/account-nicknames",
    ]
    assert all(
        request.get_header("X-internal-service-token") == "s" * 43
        for request in requests
    )
    assert str(actor).encode() in requests[0].data
    assert b'"nickname":"New Fern"' in requests[1].data


def test_community_dispatch_http_installs_redirect_rejecting_handler(monkeypatch):
    installed: list[object] = []

    class Opener:
        def open(self, request, *, timeout):
            assert timeout == 1.0
            assert request.get_header("X-internal-service-token") == (
                "private-service-secret"
            )
            raise urllib.error.HTTPError(
                request.full_url, 307, "redirect rejected", {}, None
            )

    def build_opener(handler):
        installed.append(handler)
        return Opener()

    monkeypatch.setattr("app.community_deletion.urllib.request.build_opener", build_opener)
    request = urllib.request.Request(
        "http://community-api:8000/api/internal/account-deletions",
        data=b"{}",
        headers={"X-Internal-Service-Token": "private-service-secret"},
        method="POST",
    )

    with pytest.raises(urllib.error.HTTPError):
        _open_no_redirect(request, timeout=1.0)

    assert len(installed) == 1
    assert isinstance(installed[0], _NoRedirectHandler)
    assert installed[0].redirect_request(
        None, None, 307, "", {}, "http://evil.invalid/capture"
    ) is None
