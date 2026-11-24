"""Account-bound community blocking and the operator's scan moderation.

Shared scans are shown without an author, so they have no report or block
route. A block comes from the forum and still hides that author's scans.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing

import pytest

import community_deletion
import community_feeds
import community_safety
from server.tests.conftest import _test_access_token


@pytest.fixture
def safety_store(community_store):
    import main as server_main

    community_store.init_db()
    server_main._daily_write_counts.clear()
    yield community_store


def _actor(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"safety-test:{label}"))


def _http_actor(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"test-account:{label}"))


def _headers(label: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_test_access_token(label, label.title())}"}


def _forum_post(actor: str, nickname: str) -> str:
    """A forum post by ``actor``: the only content an author is blocked from."""
    import community_forum

    community_forum.init_db()
    return community_forum.create_post(
        actor_id=actor, nickname=nickname, kind="tip", title="Mist daily", body="b"
    )["id"]


def _queue_legacy_report(community, reporter: str, owner: str, scan_id: str) -> int:
    """A scan report as an older app version filed it (no route writes one now)."""
    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as connection, connection:
        return int(
            connection.execute(
                "INSERT INTO community_content_reports (reporter_actor_id, target_type, "
                "target_scan_id, target_opinion_id, target_actor_id, reason, created_at) "
                "VALUES (?, 'scan', ?, 0, ?, 'hate_or_harassment', ?)",
                (reporter, scan_id, owner, 1_000_000),
            ).lastrowid
        )


def _scan(community, scan_id: str, actor: str, nickname: str = "Fern") -> str:
    community.record_scan(
        scan_id=scan_id,
        account_actor_id=actor,
        country="RU",
        region="Moscow",
        city="Moscow",
        lat=55.75,
        lon=37.62,
        disease="healthy",
        species="Ficus elastica",
        nickname=nickname,
    )
    return community.public_scan_token(scan_id)


def test_a_scan_is_not_a_block_target(safety_store):
    community = safety_store
    token = _scan(community, "unblockable-scan", _actor("scan-owner"))
    with pytest.raises(ValueError, match="target_not_found"):
        community_safety.block_content_author(
            blocker_actor_id=_actor("scan-reader"),
            target_type="scan",
            target_id=token,
        )
    assert community_safety.actor_blocks(_actor("scan-reader")) == []


def test_block_is_mutual_filters_all_personalized_reads_and_can_be_undone(
    safety_store,
):
    community = safety_store
    blocker = _actor("blocker")
    blocked = _actor("blocked")
    other = _actor("other")
    _scan(community, "blocked-scan", blocked, "Blocked Fern")
    _scan(community, "blocker-scan", blocker, "Blocker Fern")
    _scan(community, "other-scan", other, "Other Fern")
    blocked_post = _forum_post(blocked, "Blocked Fern")

    result = community_safety.block_content_author(
        blocker_actor_id=blocker,
        target_type="post",
        target_id=blocked_post,
        now=2_000_000,
    )
    assert result["created"] is True
    assert result["block_id"].startswith("cb")
    assert community_safety.block_content_author(
        blocker_actor_id=blocker,
        target_type="post",
        target_id=blocked_post,
    )["created"] is False

    blocker_feed = community_feeds.nearby_scans(
        country="RU", scope="country", viewer_account_actor_id=blocker
    )
    blocked_feed = community_feeds.nearby_scans(
        country="RU", scope="country", viewer_account_actor_id=blocked
    )
    assert "blocked-scan" not in {row["scan_id"] for row in blocker_feed}
    assert "blocker-scan" not in {row["scan_id"] for row in blocked_feed}
    assert "other-scan" in {row["scan_id"] for row in blocker_feed}

    assert "blocked-scan" not in {
        row["scan_id"]
        for row in community_feeds.scans_by_species(
            "Ficus elastica", viewer_account_actor_id=blocker
        )
    }
    map_ids = {
        row["scan_id"]
        for row in community_feeds.map_scans(
            55.0,
            56.0,
            37.0,
            38.0,
            viewer_account_actor_id=blocker,
        )["items"]
    }
    assert "blocked-scan" not in map_ids
    assert community_feeds.region_summary(
        "RU", viewer_account_actor_id=blocker
    )["total"] == 2

    blocks = community_safety.actor_blocks(blocker)
    assert blocks == [
        {
            "block_id": result["block_id"],
            "nickname": "Blocked Fern",
            "created_at": 2_000_000,
        }
    ]
    assert community_safety.unblock_actor(blocked, result["block_id"]) is False
    assert community_safety.unblock_actor(blocker, result["block_id"]) is True
    assert "blocked-scan" in {
        row["scan_id"]
        for row in community_feeds.nearby_scans(
            country="RU", scope="country", viewer_account_actor_id=blocker
        )
    }


def test_operator_removes_a_scan_and_drains_the_legacy_report_queue(safety_store):
    community = safety_store
    owner = _actor("moderated-owner")
    reporter = _actor("moderation-reporter")

    # A scan flagged to the moderation contact is removed by its public id.
    flagged = _scan(community, "flagged-scan", owner)
    flagged_report = _queue_legacy_report(community, reporter, owner, "flagged-scan")
    assert community_safety.remove_scan("flagged-scan") is False  # never the internal id
    assert community_safety.remove_scan(flagged) is True
    assert community.scan_exists("flagged-scan") is False
    assert community_safety.remove_scan(flagged) is False
    assert flagged_report not in {
        report["report_id"] for report in community_safety.pending_content_reports()
    }

    # Reports filed by older app versions can still be resolved.
    _scan(community, "moderated-scan", owner)
    report_id = _queue_legacy_report(community, reporter, owner, "moderated-scan")
    assert [r["report_id"] for r in community_safety.pending_content_reports()] == [report_id]
    assert community_safety.moderate_content_report(report_id, "remove_content") is True
    assert community.scan_exists("moderated-scan") is False
    assert community_safety.pending_content_reports() == []


def test_account_cleanup_erases_safety_data(safety_store):
    community = safety_store
    owner = _actor("cleanup-owner")
    reporter = _actor("cleanup-reporter")
    _scan(community, "cleanup-scan", owner)
    _queue_legacy_report(community, reporter, owner, "cleanup-scan")
    community_safety.block_content_author(
        blocker_actor_id=reporter,
        target_type="post",
        target_id=_forum_post(owner, "Owner Fern"),
    )
    assert community_deletion.request_actor_deletion(reporter, now=3_000_000) is True
    assert community_deletion.process_cleanup_jobs(now=3_000_000) == {
        "completed": 1,
        "retried": 0,
    }
    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM community_content_reports "
            "WHERE reporter_actor_id = ? OR target_actor_id = ?",
            (reporter, reporter),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM community_actor_blocks "
            "WHERE blocker_actor_id = ? OR blocked_actor_id = ?",
            (reporter, reporter),
        ).fetchone()[0] == 0


def test_moderation_tool_requires_confirmation_to_remove_a_scan(safety_store, capsys):
    from tools import community_moderation

    community = safety_store
    token = _scan(community, "tool-scan", _actor("tool-owner"))
    with pytest.raises(SystemExit):
        community_moderation.main(["remove-scan", token])
    assert community.scan_exists("tool-scan") is True
    assert community_moderation.main(["remove-scan", token, "--confirm-remove"]) == 0
    assert community.scan_exists("tool-scan") is False
    assert community_moderation.main(["remove-scan", token, "--confirm-remove"]) == 2
    capsys.readouterr()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/community/safety/blocks"),
        ("delete", "/api/community/safety/blocks/" + "cb" + "0" * 30),
    ],
)
def test_safety_routes_require_account(safety_store, client, method, path):
    response = client.request(method, path, headers={"Authorization": ""})
    assert response.status_code == 401
    assert response.json()["detail"] == "auth_required"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "post",
            "/api/community/safety/report",
            {"target_type": "scan", "target_id": "cs" + "0" * 30, "reason": "other_rules_violation"},
        ),
        (
            "post",
            "/api/community/safety/block",
            {"target_type": "scan", "target_id": "cs" + "0" * 30},
        ),
        ("post", "/api/community/like", {"scan_id": "cs" + "0" * 30, "liked": True}),
        ("get", "/api/community/profile/" + "ca" + "0" * 30, None),
    ],
)
def test_scan_report_block_like_and_profile_routes_are_retired(
    safety_store, client, method, path, body,
):
    response = client.request(
        method,
        path,
        headers=_headers("retired-caller"),
        **({"json": body} if body is not None else {}),
    )
    assert response.status_code in {404, 405}


def test_http_forum_block_filters_signed_scan_reads_and_can_be_undone(
    safety_store, client,
):
    community = safety_store
    owner_label = "http-owner"
    reporter_label = "http-reporter"
    _scan(community, "http-safety-scan", _http_actor(owner_label), "HTTP Fern")
    post = client.post(
        "/api/community/posts",
        json={"kind": "tip", "title": "Mist daily", "body": "b"},
        headers=_headers(owner_label),
    ).json()

    block = client.post(
        "/api/community/forum/block",
        headers=_headers(reporter_label),
        json={"target_type": "post", "target_id": post["id"]},
    )
    assert block.status_code == 200
    block_id = block.json()["block_id"]
    assert block_id.startswith("cb")
    assert "actor_id" not in block.text

    feed = client.get(
        "/api/community/nearby",
        headers=_headers(reporter_label),
        params={"country": "RU", "scope": "country"},
    )
    assert feed.status_code == 200 and feed.json()["items"] == []

    listed = client.get(
        "/api/community/safety/blocks", headers=_headers(reporter_label)
    )
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert listed.json()["items"][0]["block_id"] == block_id
    assert "blocked_actor_id" not in listed.text

    wrong_account = client.delete(
        f"/api/community/safety/blocks/{block_id}", headers=_headers(owner_label)
    )
    assert wrong_account.status_code == 404
    removed = client.delete(
        f"/api/community/safety/blocks/{block_id}",
        headers=_headers(reporter_label),
    )
    assert removed.status_code == 200
    feed = client.get(
        "/api/community/nearby",
        headers=_headers(reporter_label),
        params={"country": "RU", "scope": "country"},
    )
    assert [item["disease"] for item in feed.json()["items"]] == ["healthy"]


def test_scan_items_carry_no_author_or_like_fields(safety_store, client):
    community = safety_store
    _scan(community, "anonymous-scan", _http_actor("anon-owner"), "Named Fern")
    items = []
    for path, params in (
        ("/api/community/nearby", {"country": "RU", "scope": "country"}),
        ("/api/community/nearby", {"scope": "worldwide", "ranking": "recommended"}),
        ("/api/community/by-species", {"species": "Ficus elastica"}),
        (
            "/api/community/map",
            {"min_lat": 55, "min_lon": 37, "max_lat": 56, "max_lon": 38},
        ),
    ):
        response = client.get(path, headers=_headers("anon-reader"), params=params)
        assert response.status_code == 200
        assert "Named Fern" not in response.text
        items.extend(response.json()["items"])
    assert len(items) == 4
    for item in items:
        assert item["scan_id"].startswith("cs")
        assert not {"nickname", "author_token", "like_count", "liked_by_me"} & item.keys()


def test_species_scans_come_nearest_first_with_distance(safety_store):
    community = safety_store
    places = {
        "far-scan": ("DE", "Berlin", 52.52, 13.40),
        "country-scan": ("RU", "Kazan", 55.79, 49.12),
        "near-scan": ("RU", "Moscow", 55.76, 37.63),
    }
    for scan_id, (country, city, lat, lon) in places.items():
        community.record_scan(
            scan_id=scan_id,
            account_actor_id=_actor(scan_id),
            country=country,
            region=city,
            city=city,
            lat=lat,
            lon=lon,
            disease="healthy",
            species="Ficus elastica",
            nickname="Fern",
        )

    anchored = community_feeds.scans_by_species("Ficus elastica", country="RU", lat=55.75, lon=37.62)
    assert [row["scan_id"] for row in anchored] == ["near-scan", "country-scan", "far-scan"]
    assert anchored[0]["distance_km"] is not None and anchored[0]["distance_km"] < 5
    assert all("lat" not in row and "lon" not in row for row in anchored)

    unanchored = community_feeds.scans_by_species("Ficus elastica")
    assert {row["scan_id"] for row in unanchored} == set(places)
    assert all(row["distance_km"] is None for row in unanchored)
