"""The read routes app 1.9 still calls answer with shapes 1.9 decodes.

``routes_legacy_app.py`` explains why they exist. Each answer is checked
against 1.9's own models (``fixtures/app_1_9_models.kt``), and every key it
sends must be one 1.9 knows: a misspelt key would silently decode to the
field's default.
"""

from pathlib import Path

import pytest

from tests import app_contract

APP_1_9 = app_contract.parse_classes(
    Path(__file__).parent / "fixtures" / "app_1_9_models.kt"
)

BEARER = {"Authorization": "Bearer a.b.c"}

CASES = [
    ("get", "/api/community/my-scan-opinions", None, "MyScanOpinionsResponse"),
    ("get", "/api/community/opinions/realscanid01", None, "CommunityOpinionsResponse"),
    (
        "post",
        "/api/community/request-opinion/cancel",
        {"scan_id": "realscanid01"},
        "CommunityCancelOpinionResponse",
    ),
    (
        "post",
        "/api/community/scan-counts",
        {"scan_ids": ["realscanid01"]},
        "CommunityScanCountsResponse",
    ),
]


def test_fixture_holds_the_models_the_stubs_answer_with():
    for *_, class_name in CASES:
        assert APP_1_9[class_name], class_name


@pytest.mark.parametrize(("method", "path", "body", "class_name"), CASES)
@pytest.mark.parametrize("headers", [BEARER, {}], ids=["signed-in", "guest"])
def test_stub_decodes_in_app_1_9(client, method, path, body, class_name, headers):
    response = client.request(
        method, path, headers=headers, **({"json": body} if body is not None else {})
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert app_contract.violations(payload, class_name, known=APP_1_9) == []
    known_keys = {field.json_name for field in APP_1_9[class_name]}
    assert set(payload) <= known_keys, set(payload) - known_keys


def test_stubs_report_nothing(client):
    """Empty is the point: the poll never notifies, nothing waits for a reply."""
    polled = client.get("/api/community/my-scan-opinions", headers=BEARER).json()
    assert polled["scans"] == []
    opinions = client.get("/api/community/opinions/realscanid01").json()
    assert opinions["scan_id"] == "realscanid01"
    assert opinions["opinions"] == [] and opinions["consensus"] == []
    assert opinions["wants_opinion"] is False
    cancelled = client.post(
        "/api/community/request-opinion/cancel", json={"scan_id": "realscanid01"}
    ).json()
    assert cancelled == {"ok": True, "cleared": False}
    counts = client.post("/api/community/scan-counts", json={"scan_ids": ["x"]}).json()
    assert counts == {"counts": {}}


def test_overlong_scan_id_is_rejected(client):
    response = client.get("/api/community/opinions/" + "a" * 129)
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/community/opinion"),
        ("post", "/api/community/request-opinion"),
    ],
)
def test_retired_writes_stay_gone(client, method, path):
    """A 200 here would tell a 1.9 user their action happened."""
    response = client.request(method, path, headers=BEARER, json={"scan_id": "x"})
    assert response.status_code in {404, 405}


def test_stubs_stay_out_of_the_schema():
    from main import app

    paths = app.openapi()["paths"]
    for _, path, _, _ in CASES:
        templated = path.replace("realscanid01", "{scan_id}")
        assert templated not in paths
