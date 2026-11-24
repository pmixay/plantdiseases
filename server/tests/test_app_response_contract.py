"""The app response contract (tests/app_contract.py) and its conftest hook."""

from __future__ import annotations

import pytest

import main
from tests import app_contract


def test_reads_the_apps_model_classes():
    classes = app_contract.classes()
    post = {field.json_name: field for field in classes["ForumPost"]}
    assert not post["id"].has_default and not post["id"].nullable
    assert post["like_count"].has_default
    assert post["answers"].kotlin_type == "List<ForumAnswer>"


def test_request_paths_resolve_to_their_model():
    assert app_contract.class_for("get", "/api/community/posts/abc123") == "ForumPost"
    assert app_contract.class_for("GET", "/api/community/posts/abc/extra") is None
    assert app_contract.class_for("DELETE", "/api/community/posts/abc") is None


def test_every_contract_route_exists_and_names_a_known_class():
    paths = main.app.openapi()["paths"]
    routes = {(method.upper(), path) for path, methods in paths.items() for method in methods}
    for (method, path), class_name in app_contract.ROUTES.items():
        assert (method, path) in routes, f"{method} {path} is not a server route"
        assert class_name in app_contract.classes(), f"{class_name} is not an app model"


def test_violations_name_missing_null_and_mistyped_fields():
    assert app_contract.violations({"id": "p1"}, "ForumPost") == []
    assert app_contract.violations({}, "ForumPost") == ["$.id: missing (required by ForumPost)"]
    assert app_contract.violations({"id": None}, "ForumPost") == ["$.id: null (required by ForumPost)"]
    assert app_contract.violations({"id": "p1", "like_count": "3"}, "ForumPost") == [
        "$.like_count: '3' is not a Int"
    ]
    # Unknown keys are ignored and a defaulted field may be null, as in the app.
    assert app_contract.violations({"id": "p1", "new_field": 1, "title": None}, "ForumPost") == []
    nested = app_contract.violations({"id": "p1", "answers": [{}]}, "ForumPost")
    assert nested == ["$.answers[0].id: missing (required by ForumAnswer)"]


def test_the_client_fails_a_response_the_app_could_not_decode(client, monkeypatch):
    # /api/health has no media_id, so pretending the app decodes it as a media
    # upload must fail the request inside the test client.
    real = app_contract.class_for
    monkeypatch.setattr(
        app_contract,
        "class_for",
        lambda method, path: "ForumMediaUpload" if path == "/api/health" else real(method, path),
    )
    with pytest.raises(AssertionError, match="would not decode in the app"):
        client.get("/api/health")
