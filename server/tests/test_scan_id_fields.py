"""Every request field that names a scan shares one rule (``ScanId``)."""

import pytest
from pydantic import ValidationError

from routes_community import CommunityScanRequest, CommunityViewRequest

ANALYSIS_TOKEN = "0123456789abcdef0123456789abcdef"  # uuid4().hex
APP_UUID = "6f1c1e8a-2b7d-4c1e-9a55-3f0e7d2b9c41"  # the app's own scan
PUBLIC_TOKEN = "cs" + "a" * 30  # a feed row
MINTED = [ANALYSIS_TOKEN, APP_UUID, PUBLIC_TOKEN]
BAD = ["", "short", "a" * 129, "has space", "semi;colon", "ünicode"]


def _upload(scan_id: str) -> CommunityScanRequest:
    return CommunityScanRequest(scan_id=scan_id, disease="healthy")


@pytest.mark.parametrize("scan_id", MINTED)
def test_every_minted_id_is_accepted_everywhere(scan_id):
    assert _upload(scan_id).scan_id == scan_id
    assert CommunityViewRequest(scan_id=scan_id).scan_id == scan_id
    assert CommunityViewRequest(scan_ids=[scan_id]).scan_ids == [scan_id]


@pytest.mark.parametrize("scan_id", BAD)
def test_every_field_rejects_the_same_bad_ids(scan_id):
    with pytest.raises(ValidationError):
        _upload(scan_id)
    with pytest.raises(ValidationError):
        CommunityViewRequest(scan_id=scan_id, scan_ids=[ANALYSIS_TOKEN])
    with pytest.raises(ValidationError):
        CommunityViewRequest(scan_ids=[ANALYSIS_TOKEN, scan_id])


def test_upload_accepts_the_same_length_as_view():
    longest = "a" * 128
    assert _upload(longest).scan_id == longest
    assert CommunityViewRequest(scan_id=longest).scan_id == longest


def test_a_batch_holds_at_most_fifty():
    CommunityViewRequest(scan_ids=[ANALYSIS_TOKEN] * 50)
    with pytest.raises(ValidationError):
        CommunityViewRequest(scan_ids=[ANALYSIS_TOKEN] * 51)


def test_view_still_needs_a_target():
    with pytest.raises(ValidationError):
        CommunityViewRequest()
    with pytest.raises(ValidationError):
        CommunityViewRequest(scan_ids=[])


def test_bad_batch_item_is_a_422_over_http(client):
    response = client.post(
        "/api/community/view", json={"scan_ids": [ANALYSIS_TOKEN, "bad id"]}
    )
    assert response.status_code == 422
