"""Account-bound community reporting, blocking, and moderation controls."""

from __future__ import annotations

import sqlite3
import uuid

import pytest

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


def _opinion_token(community, opinion_id: int) -> str:
    """The stored moderation token for one reply.

    ``public_opinion_token`` mints an unpredictable value (hashing the
    sequential row id would only disguise an enumerable one), so a test cannot
    recompute it — it reads back what the write persisted.
    """
    with community._connect() as conn:
        row = conn.execute(
            "SELECT public_id FROM community_opinions WHERE id = ?", (int(opinion_id),)
        ).fetchone()
    assert row is not None and row[0]
    return str(row[0])


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


def test_opinions_expose_only_opaque_safety_token_and_viewer_flag(safety_store):
    community = safety_store
    owner = _actor("owner")
    responder = _actor("responder")
    _scan(community, "opaque-opinion", owner)
    opinion_id = community.record_opinion(
        scan_id="opaque-opinion",
        account_actor_id=responder,
        suggested_disease="leaf_spot",
        nickname="Responder",
    )

    owner_view = community.opinions_for(
        "opaque-opinion", viewer_account_actor_id=owner
    )["opinions"][0]
    responder_view = community.opinions_for(
        "opaque-opinion", viewer_account_actor_id=responder
    )["opinions"][0]

    assert owner_view["content_id"] == _opinion_token(community, opinion_id)
    assert owner_view["content_id"].startswith("co")
    assert len(owner_view["content_id"]) == 32
    # The token is random, not derived from the row id, so a substring check on
    # the id would pass or fail by chance. Assert unpredictability instead: a
    # second reply on the same scan gets an unrelated token.
    other_id = community.record_opinion(
        scan_id="opaque-opinion",
        account_actor_id=_actor("third-party"),
        suggested_disease="leaf_spot",
    )
    assert _opinion_token(community, other_id) != owner_view["content_id"]
    assert owner_view["is_mine"] is False
    assert responder_view["is_mine"] is True
    assert "account_actor_id" not in owner_view


def test_reports_are_bound_idempotent_and_use_fixed_reasons(safety_store, monkeypatch):
    community = safety_store
    owner = _actor("reported-owner")
    reporter = _actor("reporter")
    scan_token = _scan(community, "reported-scan", owner)
    opinion_id = community.record_opinion(
        scan_id="reported-scan",
        account_actor_id=owner,
        suggested_disease="healthy",
    )
    opinion_token = _opinion_token(community, opinion_id)

    assert community.record_content_report(
        reporter_actor_id=reporter,
        target_type="scan",
        target_id=scan_token,
        reason="spam_or_misleading",
        now=1_000_000,
    ) is True
    assert community.record_content_report(
        reporter_actor_id=reporter,
        target_type="SCAN",
        target_id=scan_token,
        reason="spam_or_misleading",
        now=1_000_001,
    ) is False

    # Safety mutations never accept the internal publication identifier even
    # though legacy opinion/view reads still do for installed app versions.
    with pytest.raises(ValueError, match="target_not_found"):
        community.record_content_report(
            reporter_actor_id=reporter,
            target_type="scan",
            target_id="reported-scan",
            reason="spam_or_misleading",
        )

    with pytest.raises(ValueError, match="self_target"):
        community.record_content_report(
            reporter_actor_id=owner,
            target_type="scan",
            target_id=scan_token,
            reason="other_rules_violation",
        )
    with pytest.raises(ValueError, match="invalid_reason"):
        community.record_content_report(
            reporter_actor_id=reporter,
            target_type="opinion",
            target_id=opinion_token,
            reason="free form accusation",
        )

    monkeypatch.setattr(community, "COMMUNITY_REPORT_DAILY_LIMIT", 1)
    with pytest.raises(ValueError, match="report_limit"):
        community.record_content_report(
            reporter_actor_id=reporter,
            target_type="opinion",
            target_id=opinion_token,
            reason="hate_or_harassment",
            now=1_000_002,
        )

    with sqlite3.connect(community.COMMUNITY_DB_PATH) as connection:
        row = connection.execute(
            "SELECT reporter_actor_id, target_actor_id, target_scan_id, "
            "target_opinion_id, reason FROM community_content_reports"
        ).fetchone()
    assert row == (reporter, owner, "reported-scan", 0, "spam_or_misleading")


def test_block_is_mutual_filters_all_personalized_reads_and_can_be_undone(
    safety_store,
):
    community = safety_store
    blocker = _actor("blocker")
    blocked = _actor("blocked")
    other = _actor("other")
    blocked_token = _scan(community, "blocked-scan", blocked, "Blocked Fern")
    _scan(community, "blocker-scan", blocker, "Blocker Fern")
    _scan(community, "other-scan", other, "Other Fern")

    result = community.block_content_author(
        blocker_actor_id=blocker,
        target_type="scan",
        target_id=blocked_token,
        now=2_000_000,
    )
    assert result["created"] is True
    assert result["block_id"].startswith("cb")
    assert community.block_content_author(
        blocker_actor_id=blocker,
        target_type="scan",
        target_id=blocked_token,
    )["created"] is False

    blocker_feed = community.nearby_scans(
        country="RU", scope="country", viewer_account_actor_id=blocker
    )
    blocked_feed = community.nearby_scans(
        country="RU", scope="country", viewer_account_actor_id=blocked
    )
    assert "blocked-scan" not in {row["scan_id"] for row in blocker_feed}
    assert "blocker-scan" not in {row["scan_id"] for row in blocked_feed}
    assert "other-scan" in {row["scan_id"] for row in blocker_feed}

    assert "blocked-scan" not in {
        row["scan_id"]
        for row in community.scans_by_species(
            "Ficus elastica", viewer_account_actor_id=blocker
        )
    }
    map_ids = {
        row["scan_id"]
        for row in community.map_scans(
            55.0,
            56.0,
            37.0,
            38.0,
            viewer_account_actor_id=blocker,
        )["items"]
    }
    assert "blocked-scan" not in map_ids
    assert community.region_summary(
        "RU", viewer_account_actor_id=blocker
    )["total"] == 2

    blocks = community.actor_blocks(blocker)
    assert blocks == [
        {
            "block_id": result["block_id"],
            "nickname": "Blocked Fern",
            "created_at": 2_000_000,
        }
    ]
    assert community.unblock_actor(blocked, result["block_id"]) is False
    assert community.unblock_actor(blocker, result["block_id"]) is True
    assert "blocked-scan" in {
        row["scan_id"]
        for row in community.nearby_scans(
            country="RU", scope="country", viewer_account_actor_id=blocker
        )
    }


def test_blocked_reply_disappears_and_cannot_interact_again(safety_store):
    community = safety_store
    owner = _actor("thread-owner")
    responder = _actor("thread-responder")
    _scan(community, "thread-scan", owner)
    opinion_id = community.record_opinion(
        scan_id="thread-scan",
        account_actor_id=responder,
        suggested_disease="leaf_spot",
        note="unsafe reply",
        nickname="Responder",
    )
    opinion_token = _opinion_token(community, opinion_id)
    community.block_content_author(
        blocker_actor_id=owner,
        target_type="opinion",
        target_id=opinion_token,
    )

    assert community.opinions_for(
        "thread-scan", viewer_account_actor_id=owner
    )["opinions"] == []
    assert community.record_view(
        "thread-scan", account_actor_id=responder
    ) is False
    with pytest.raises(ValueError, match="blocked_interaction"):
        community.record_opinion(
            scan_id="thread-scan",
            account_actor_id=responder,
            suggested_disease="healthy",
        )


def test_legacy_content_can_be_reported_but_not_used_to_spoof_a_block(safety_store):
    community = safety_store
    reporter = _actor("legacy-reporter")
    community.record_scan(
        scan_id="legacy-safety",
        user_pseudo_id="old-install",
        country="RU",
        disease="healthy",
    )
    token = community.public_scan_token("legacy-safety")
    assert community.record_content_report(
        reporter_actor_id=reporter,
        target_type="scan",
        target_id=token,
        reason="privacy_or_impersonation",
    ) is True
    with pytest.raises(ValueError, match="target_not_blockable"):
        community.block_content_author(
            blocker_actor_id=reporter,
            target_type="scan",
            target_id=token,
        )


def test_local_moderation_removes_exact_target_and_account_cleanup_erases_safety_data(
    safety_store,
):
    community = safety_store
    owner = _actor("moderated-owner")
    responder = _actor("moderated-responder")
    reporter = _actor("moderation-reporter")
    scan_token = _scan(community, "moderated-scan", owner)
    opinion_id = community.record_opinion(
        scan_id="moderated-scan",
        account_actor_id=responder,
        suggested_disease="leaf_spot",
        note="remove only this reply",
    )
    opinion_token = _opinion_token(community, opinion_id)
    community.record_content_report(
        reporter_actor_id=reporter,
        target_type="opinion",
        target_id=opinion_token,
        reason="hate_or_harassment",
    )
    report_id = community.pending_content_reports()[0]["report_id"]
    assert community.moderate_content_report(report_id, "remove_content") is True
    assert community.opinions_for("moderated-scan")["opinions"] == []
    assert community.scan_exists("moderated-scan") is True

    community.record_content_report(
        reporter_actor_id=reporter,
        target_type="scan",
        target_id=scan_token,
        reason="illegal_or_dangerous",
    )
    community.block_content_author(
        blocker_actor_id=reporter,
        target_type="scan",
        target_id=scan_token,
    )
    assert community.request_actor_deletion(reporter, now=3_000_000) is True
    assert community.process_cleanup_jobs(now=3_000_000) == {
        "completed": 1,
        "retried": 0,
    }
    with sqlite3.connect(community.COMMUNITY_DB_PATH) as connection:
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
        ("get", "/api/community/safety/blocks", None),
        ("delete", "/api/community/safety/blocks/" + "cb" + "0" * 30, None),
    ],
)
def test_safety_routes_require_account(safety_store, client, method, path, body):
    response = client.request(
        method,
        path,
        headers={"Authorization": ""},
        **({"json": body} if body is not None else {}),
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "auth_required"


def test_http_safety_contract_never_accepts_actor_id_and_filters_signed_reads(
    safety_store, client,
):
    community = safety_store
    owner_label = "http-owner"
    reporter_label = "http-reporter"
    owner = _http_actor(owner_label)
    reporter = _http_actor(reporter_label)
    token = _scan(community, "http-safety-scan", owner, "HTTP Fern")

    spoof = client.post(
        "/api/community/safety/report",
        headers=_headers(reporter_label),
        json={
            "target_type": "scan",
            "target_id": token,
            "reason": "spam_or_misleading",
            "target_actor_id": reporter,
        },
    )
    assert spoof.status_code == 422

    report = client.post(
        "/api/community/safety/report",
        headers=_headers(reporter_label),
        json={
            "target_type": "scan",
            "target_id": token,
            "reason": "spam_or_misleading",
        },
    )
    assert report.status_code == 200 and report.json()["created"] is True
    raw_target = client.post(
        "/api/community/safety/block",
        headers=_headers(reporter_label),
        json={"target_type": "scan", "target_id": "http-safety-scan"},
    )
    assert raw_target.status_code == 422
    duplicate = client.post(
        "/api/community/safety/report",
        headers=_headers(reporter_label),
        json={
            "target_type": "scan",
            "target_id": token,
            "reason": "other_rules_violation",
        },
    )
    assert duplicate.status_code == 200 and duplicate.json()["created"] is False

    block = client.post(
        "/api/community/safety/block",
        headers=_headers(reporter_label),
        json={"target_type": "scan", "target_id": token},
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
