"""Following spaces: per-member follows and the feed's ``following`` tab."""

from __future__ import annotations

import pytest

from tests.test_community_achievements import _actor, _auth, ach, forum  # noqa: F401


def _post(forum, asker, space, title):  # noqa: F811
    return forum.create_post(
        actor_id=_actor(asker), nickname=asker, kind="question", title=title,
        body="Help", space=space,
    )


def _titles(page: dict) -> set[str]:
    return {item["title"] for item in page["items"]}


def test_following_tab_lists_only_followed_spaces(forum):  # noqa: F811
    _post(forum, "a", "pests", "Mites")
    _post(forum, "a", "orchids", "Orchid roots")
    _post(forum, "a", "ferns", "Fern tips")
    reader = _actor("reader")

    assert forum.feed(viewer_actor_id=reader, tab="following")["items"] == []
    forum.set_space_follow(actor_id=reader, slug="pests", following=True)
    forum.set_space_follow(actor_id=reader, slug="orchids", following=True)
    assert forum.followed_spaces(reader) == ["pests", "orchids"]
    assert _titles(forum.feed(viewer_actor_id=reader, tab="following")) == {
        "Mites",
        "Orchid roots",
    }
    # Another member's follows are their own.
    assert forum.followed_spaces(_actor("other")) == []

    forum.set_space_follow(actor_id=reader, slug="pests", following=False)
    forum.set_space_follow(actor_id=reader, slug="pests", following=False)  # idempotent
    assert _titles(forum.feed(viewer_actor_id=reader, tab="following")) == {"Orchid roots"}


def test_following_needs_a_signed_in_viewer_and_a_live_space(forum):  # noqa: F811
    with pytest.raises(forum.ForumError) as missing_viewer:
        forum.feed(viewer_actor_id=None, tab="following")
    assert missing_viewer.value.status == 401
    with pytest.raises(forum.ForumError):
        forum.set_space_follow(actor_id=_actor("reader"), slug="no_such_space", following=True)


def test_deleting_an_account_drops_its_follows(forum):  # noqa: F811
    import community
    import community_deletion

    reader = _actor("leaver")
    forum.set_space_follow(actor_id=reader, slug="pests", following=True)
    community_deletion.request_actor_deletion(reader)
    assert community_deletion.process_cleanup_jobs() == {"completed": 1, "retried": 0}
    with community._connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM forum_space_follows WHERE actor_id = ?", (reader,)
        ).fetchone()[0]
    assert count == 0


def test_http_follow_round_trip(client, forum):  # noqa: F811
    _post(forum, "a", "pests", "Mites")
    _post(forum, "a", "ferns", "Fern tips")
    headers = _auth("follower")

    followed = client.post(
        "/api/community/spaces/pests/follow", json={"following": True}, headers=headers
    )
    assert followed.status_code == 200, followed.text
    assert followed.json() == {"slug": "pests", "following": True}
    listed = client.get("/api/community/spaces/following", headers=headers)
    assert listed.json() == {"items": ["pests"]}
    page = client.get("/api/community/feed", params={"tab": "following"}, headers=headers)
    assert page.status_code == 200
    assert _titles(page.json()) == {"Mites"}

    unfollowed = client.post(
        "/api/community/spaces/pests/follow", json={"following": False}, headers=headers
    )
    assert unfollowed.json() == {"slug": "pests", "following": False}


def test_http_follow_needs_an_account_and_a_known_space(client, forum):  # noqa: F811
    assert client.get(
        "/api/community/spaces/following", headers={"Authorization": ""}
    ).status_code == 401
    assert client.post(
        "/api/community/spaces/pests/follow", json={"following": True},
        headers={"Authorization": ""},
    ).status_code == 401
    assert client.get(
        "/api/community/feed", params={"tab": "following"}, headers={"Authorization": ""}
    ).status_code == 401
    unknown = client.post(
        "/api/community/spaces/nope/follow", json={"following": True},
        headers=_auth("follower"),
    )
    assert unknown.status_code in {404, 422}
