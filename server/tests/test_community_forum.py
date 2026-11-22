"""Community forum: posts, answers, likes, photos, search, deletion.

Store-level tests call ``community_forum`` against the isolated
``community_store`` DB; HTTP tests go through ``client`` with signed test
identities. Actor ids never appear in any response.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import uuid

import pytest
from PIL import Image

from tests.conftest import _test_access_token


def _actor(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"test-account:{label}"))


def _auth(label: str) -> dict:
    return {"Authorization": f"Bearer {_test_access_token(label, label.title())}"}


def _png(color=(30, 120, 40), size=(240, 180)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def forum(community_store):
    import community_forum

    community_forum.init_db()
    return community_forum


@pytest.fixture
def no_level_gates(monkeypatch):
    """Store mechanics without the level-2 requirement for "not helpful"."""
    import community_forum

    monkeypatch.setattr(community_forum, "can_mark_not_helpful", None)


def _question(forum, author="asker", **overrides):
    media = forum.store_media(actor_id=_actor(author), image_bytes=_png())
    fields = {
        "actor_id": _actor(author),
        "nickname": author.title(),
        "kind": "question",
        "title": "White fuzz on my jade plant",
        "body": "Cottony clumps at the base of the leaves",
        "space": "succulents",
        "media_ids": [media["media_id"]],
    }
    fields.update(overrides)
    return forum.create_post(**fields)


# ── store ───────────────────────────────────────────────────────────────


def test_question_answer_like_accept_flow(forum):
    post = _question(forum)
    assert post["id"].startswith("fp") and len(post["id"]) == 32
    assert post["author"]["is_me"] is True
    assert post["media_count"] == 1 and post["ai"] is None

    answer = forum.create_answer(
        actor_id=_actor("helper"),
        nickname="Helper",
        post_id=post["id"],
        body="Those are mealybugs.",
        steps=["Isolate the plant", "Dab with 70% alcohol"],
        diagnosis="pests",
    )
    assert answer["steps"] == ["Isolate the plant", "Dab with 70% alcohol"]
    assert answer["diagnosis"] == "pests"

    assert forum.set_post_like(actor_id=_actor("helper"), post_id=post["id"], liked=True) == {
        "liked": True,
        "like_count": 1,
    }
    reaction = forum.set_answer_reaction(
        actor_id=_actor("asker"), answer_id=answer["id"], reaction="like"
    )
    assert reaction == {"reaction": "like", "like_count": 1, "not_helpful_count": 0}
    forum.accept_answer(actor_id=_actor("asker"), post_id=post["id"], answer_id=answer["id"])

    detail = forum.get_post(post["id"], viewer_actor_id=_actor("helper"))
    assert detail["solved"] is True
    assert detail["accepted_answer_id"] == answer["id"]
    assert detail["answers"][0]["accepted"] is True
    assert detail["liked_by_me"] is True
    assert detail["answer_count"] == 1
    serialized = json.dumps(detail)
    assert _actor("asker") not in serialized and _actor("helper") not in serialized


def test_self_like_self_accept_and_self_reaction_are_refused(forum):
    post = _question(forum)
    own = forum.create_answer(
        actor_id=_actor("asker"), nickname="Asker", post_id=post["id"], body="Update: still bad"
    )
    with pytest.raises(forum.ForumError) as like_error:
        forum.set_post_like(actor_id=_actor("asker"), post_id=post["id"], liked=True)
    assert like_error.value.code == "self_like"
    with pytest.raises(forum.ForumError) as accept_error:
        forum.accept_answer(actor_id=_actor("asker"), post_id=post["id"], answer_id=own["id"])
    assert accept_error.value.code == "self_accept"
    with pytest.raises(forum.ForumError) as reaction_error:
        forum.set_answer_reaction(actor_id=_actor("asker"), answer_id=own["id"], reaction="like")
    assert reaction_error.value.code == "self_reaction"


def test_only_the_author_accepts(forum):
    post = _question(forum)
    answer = forum.create_answer(
        actor_id=_actor("helper"), nickname="Helper", post_id=post["id"], body="Mealybugs"
    )
    with pytest.raises(forum.ForumError) as error:
        forum.accept_answer(actor_id=_actor("other"), post_id=post["id"], answer_id=answer["id"])
    assert error.value.status == 403


def test_not_helpful_is_answers_only_and_switches_with_like(forum, no_level_gates):
    post = _question(forum)
    answer = forum.create_answer(
        actor_id=_actor("helper"), nickname="Helper", post_id=post["id"], body="Overwatering"
    )
    voter = _actor("voter")
    forum.set_answer_reaction(actor_id=voter, answer_id=answer["id"], reaction="not_helpful")
    switched = forum.set_answer_reaction(actor_id=voter, answer_id=answer["id"], reaction="like")
    assert switched == {"reaction": "like", "like_count": 1, "not_helpful_count": 0}
    cleared = forum.set_answer_reaction(actor_id=voter, answer_id=answer["id"], reaction=None)
    assert cleared == {"reaction": None, "like_count": 0, "not_helpful_count": 0}


def test_replies_nest_three_levels_then_become_siblings(forum):
    post = _question(forum)
    parent = None
    ids = []
    for depth in range(4):
        answer = forum.create_answer(
            actor_id=_actor(f"r{depth}"),
            nickname=f"R{depth}",
            post_id=post["id"],
            parent_id=parent,
            body=f"level {depth}",
        )
        ids.append(answer["id"])
        parent = answer["id"]
    detail = forum.get_post(post["id"])
    depths = {a["id"]: a["depth"] for a in detail["answers"]}
    assert [depths[i] for i in ids] == [0, 1, 2, 2]
    assert detail["answers"][3]["parent_id"] == ids[1]


def test_feed_tabs_and_needs_help(forum):
    unanswered = _question(forum, title="Yellow leaves on calathea", space="prayer_plants")
    answered = _question(forum, author="other", title="Brown tips on palm", space="palms")
    forum.create_answer(
        actor_id=_actor("helper"), nickname="Helper", post_id=answered["id"], body="Dry air"
    )
    forum.create_post(
        actor_id=_actor("tipster"), nickname="Tipster", kind="tip",
        title="Bottom watering works", body="Soak for 20 minutes", space="general",
    )
    all_ids = [p["id"] for p in forum.feed()["items"]]
    assert set(all_ids) >= {unanswered["id"], answered["id"]}
    help_ids = [p["id"] for p in forum.feed(tab="needs_help")["items"]]
    assert help_ids == [unanswered["id"]]
    space_ids = [p["id"] for p in forum.feed(space="palms")["items"]]
    assert space_ids == [answered["id"]]
    mine = forum.feed(tab="mine", viewer_actor_id=_actor("other"))["items"]
    assert [p["id"] for p in mine] == [answered["id"]]


def test_feed_pagination_cursor(forum):
    for index in range(5):
        forum.create_post(
            actor_id=_actor("tipster"), nickname="T", kind="tip",
            title=f"Tip {index}", body="text",
        )
    first = forum.feed(sort="new", limit=2)
    second = forum.feed(sort="new", limit=2, cursor=first["next_cursor"])
    assert len(first["items"]) == 2 and len(second["items"]) == 2
    assert not {p["id"] for p in first["items"]} & {p["id"] for p in second["items"]}
    with pytest.raises(forum.ForumError):
        forum.feed(cursor="not-a-cursor")


def test_search_matches_russian_word_forms_and_answers(forum):
    post = _question(
        forum,
        title="Белый налёт на листьях",
        body="Появился после зимы",
        space="begonias",
    )
    forum.create_answer(
        actor_id=_actor("helper"), nickname="Helper", post_id=post["id"],
        body="Это мучнистая роса, нужна вентиляция",
    )
    other = _question(forum, author="other", title="Leaves curling after repotting")
    assert [p["id"] for p in forum.search(query="листья")["items"]] == [post["id"]]
    assert [p["id"] for p in forum.search(query="вентиляцию")["items"]] == [post["id"]]
    assert [p["id"] for p in forum.search(query="налётом")["items"]] == [post["id"]]
    assert [p["id"] for p in forum.search(query="curling")["items"]] == [other["id"]]
    assert forum.search(query="White LEAVES")["terms"] == ["white", "leaves"]


def test_search_with_scan_filters_by_condition_and_species(forum):
    solved = _question(forum, title="Fuzz", species="Echeveria", condition="powdery_mildew")
    answer = forum.create_answer(
        actor_id=_actor("helper"), nickname="Helper", post_id=solved["id"], body="Airflow"
    )
    forum.accept_answer(actor_id=_actor("asker"), post_id=solved["id"], answer_id=answer["id"])
    _question(forum, author="other", title="Spots", species="Echeveria", condition="leaf_spot")
    found = forum.search(condition="powdery_mildew", species="echeveria")["items"]
    assert [p["id"] for p in found] == [solved["id"]]
    assert found[0]["solved"] is True
    assert found[0]["accepted_excerpt"] == "Airflow"
    with pytest.raises(forum.ForumError):
        forum.search()


def test_blocked_authors_disappear_for_the_blocker(forum, community_store):
    post = _question(forum, author="spammer")
    community_store.block_content_author(
        blocker_actor_id=_actor("reader"), target_type="post", target_id=post["id"]
    )
    assert forum.feed(viewer_actor_id=_actor("reader"))["items"] == []
    assert len(forum.feed()["items"]) == 1
    with pytest.raises(forum.ForumError) as error:
        forum.get_post(post["id"], viewer_actor_id=_actor("reader"))
    assert error.value.status == 404
    with pytest.raises(forum.ForumError):
        forum.create_answer(
            actor_id=_actor("reader"), nickname="R", post_id=post["id"], body="hi"
        )


def test_media_is_reencoded_without_metadata(forum):
    from PIL.ExifTags import Base

    exif = Image.Exif()
    exif[Base.Make] = "SecretPhone"
    exif[Base.Orientation] = 6
    buf = io.BytesIO()
    Image.new("RGB", (3000, 2000), (10, 200, 10)).save(buf, format="JPEG", exif=exif)
    media = forum.store_media(actor_id=_actor("asker"), image_bytes=buf.getvalue())
    path = forum.media_file(media["media_id"], viewer_actor_id=_actor("asker"))
    stored = path.read_bytes()
    assert b"SecretPhone" not in stored
    with Image.open(io.BytesIO(stored)) as reopened:
        assert not reopened.getexif()
        # Orientation 6 rotates the landscape source into portrait.
        assert reopened.size == (media["width"], media["height"])
        assert reopened.size[1] > reopened.size[0]
        assert max(reopened.size) <= forum.MEDIA_LONG_EDGE


def test_unattached_media_is_owner_only_and_cannot_be_borrowed(forum):
    media = forum.store_media(actor_id=_actor("asker"), image_bytes=_png())
    assert forum.media_file(media["media_id"]) is None
    assert forum.media_file(media["media_id"], viewer_actor_id=_actor("asker")) is not None
    with pytest.raises(forum.ForumError) as error:
        forum.create_post(
            actor_id=_actor("thief"), nickname="T", kind="question",
            title="Mine now", media_ids=[media["media_id"]],
        )
    assert error.value.code == "media_not_found"
    with pytest.raises(forum.ForumError):
        forum.store_media(actor_id=_actor("asker"), image_bytes=b"not an image")


def _issue_scan(community_store, token: str, image: bytes, disease="powdery_mildew"):
    community_store.record_analysis_token(
        token,
        disease=disease,
        confidence=0.71,
        species="Echeveria",
        species_score=0.9,
        image_sha256=hashlib.sha256(image).hexdigest(),
    )


def test_scan_post_carries_the_server_verdict_as_first_answer(forum, community_store, no_level_gates):
    image = _png((200, 200, 190))
    token = "scan-" + uuid.uuid4().hex
    _issue_scan(community_store, token, image)
    media = forum.store_media(actor_id=_actor("asker"), image_bytes=image, scan_token=token)
    post = forum.create_post(
        actor_id=_actor("asker"), nickname="Asker", kind="scan", title="",
        media_ids=[media["media_id"]], scan_token=token,
    )
    assert post["ai"] == {"disease": "powdery_mildew", "confidence": 0.71, "species": "Echeveria"}
    assert post["answers"][0]["is_ai"] is True
    assert post["answers"][0]["diagnosis"] == "powdery_mildew"
    assert post["answer_count"] == 0  # the AI answer doesn't take a post off "Needs help"
    assert [p["id"] for p in forum.feed(tab="needs_help")["items"]] == [post["id"]]
    # A human can like or mark the AI answer not helpful like any answer.
    forum.set_answer_reaction(
        actor_id=_actor("helper"), answer_id=post["answers"][0]["id"], reaction="not_helpful"
    )
    with pytest.raises(forum.ForumError) as duplicate:
        again = forum.store_media(actor_id=_actor("asker"), image_bytes=image, scan_token=token)
        forum.create_post(
            actor_id=_actor("asker"), nickname="Asker", kind="scan", title="",
            media_ids=[again["media_id"]], scan_token=token,
        )
    assert duplicate.value.code == "scan_already_posted"


def test_scan_photo_must_match_the_analysed_bytes(forum, community_store):
    token = "scan-" + uuid.uuid4().hex
    _issue_scan(community_store, token, _png((1, 2, 3)))
    with pytest.raises(forum.ForumError) as error:
        forum.store_media(actor_id=_actor("asker"), image_bytes=_png((9, 9, 9)), scan_token=token)
    assert error.value.code == "scan_image_mismatch"
    with pytest.raises(forum.ForumError) as unknown:
        forum.create_post(
            actor_id=_actor("asker"), nickname="A", kind="scan", title="",
            scan_token="never-issued",
        )
    assert unknown.value.code == "scan_unknown"


def test_scan_token_claimed_by_someone_else_is_refused(forum, community_store):
    image = _png((5, 6, 7))
    token = "scan-" + uuid.uuid4().hex
    _issue_scan(community_store, token, image)
    community_store.record_analysis_scan(
        scan_id=token, account_actor_id=_actor("owner"), country="GB", image_bytes=image,
    )
    with pytest.raises(forum.ForumError) as error:
        forum.store_media(actor_id=_actor("other"), image_bytes=image, scan_token=token)
    assert error.value.code == "scan_taken"
    # The owner who already shared it to the map may still post it.
    media = forum.store_media(actor_id=_actor("owner"), image_bytes=image, scan_token=token)
    post = forum.create_post(
        actor_id=_actor("owner"), nickname="O", kind="scan", title="",
        media_ids=[media["media_id"]], scan_token=token,
    )
    assert post["ai"]["disease"] == "powdery_mildew"


def test_recovery_update_links_to_own_post_and_shares_plant_group(forum):
    original = _question(forum)
    update = forum.create_post(
        actor_id=_actor("asker"), nickname="Asker", kind="recovery",
        title="Six weeks later", body="All clear", parent_post_id=original["id"],
    )
    assert update["parent_post_id"] == original["id"]
    assert forum.get_post(original["id"])["recovery_updates"][0]["id"] == update["id"]
    with pytest.raises(forum.ForumError):
        forum.create_post(
            actor_id=_actor("other"), nickname="O", kind="recovery",
            title="Not my plant", body="x", parent_post_id=original["id"],
        )
    import community

    with community._connect() as conn:
        groups = conn.execute("SELECT DISTINCT plant_group FROM forum_posts").fetchall()
    assert len(groups) == 1


def test_deleted_answer_with_replies_leaves_a_placeholder(forum):
    post = _question(forum)
    answer = forum.create_answer(
        actor_id=_actor("helper"), nickname="Helper", post_id=post["id"], body="Root rot"
    )
    forum.create_answer(
        actor_id=_actor("asker"), nickname="Asker", post_id=post["id"],
        parent_id=answer["id"], body="How do I check?",
    )
    forum.delete_answer(actor_id=_actor("helper"), answer_id=answer["id"])
    answers = forum.get_post(post["id"])["answers"]
    assert answers[0]["state"] == "removed" and answers[0]["body"] == ""
    assert answers[0]["author"] is None
    assert answers[1]["body"] == "How do I check?"
    assert forum.get_post(post["id"])["answer_count"] == 1


def test_author_deletes_post_and_photos(forum):
    post = _question(forum)
    media_id = post["media"][0]["media_id"]
    path = forum.media_file(media_id)
    assert path.is_file()
    forum.delete_post(actor_id=_actor("asker"), post_id=post["id"])
    assert not path.exists()
    with pytest.raises(forum.ForumError):
        forum.get_post(post["id"])
    assert forum.search(query="jade")["items"] == []


def test_account_deletion_purges_forum_content_and_counts(forum, community_store):
    post = _question(forum, author="leaver")
    other = _question(forum, author="stayer", title="Spider mites on ficus")
    leaver_answer = forum.create_answer(
        actor_id=_actor("leaver"), nickname="Leaver", post_id=other["id"], body="Neem oil"
    )
    forum.set_post_like(actor_id=_actor("leaver"), post_id=other["id"], liked=True)
    forum.accept_answer(
        actor_id=_actor("stayer"), post_id=other["id"], answer_id=leaver_answer["id"]
    )
    media_path = forum.media_file(post["media"][0]["media_id"])

    community_store.request_actor_deletion(_actor("leaver"))
    assert community_store.process_cleanup_jobs() == {"completed": 1, "retried": 0}

    assert not media_path.exists()
    with pytest.raises(forum.ForumError):
        forum.get_post(post["id"])
    remaining = forum.get_post(other["id"])
    assert remaining["like_count"] == 0
    assert remaining["answer_count"] == 0
    assert remaining["solved"] is False
    assert remaining["answers"] == []
    with pytest.raises(forum.ForumError) as error:
        forum.create_post(
            actor_id=_actor("leaver"), nickname="L", kind="tip", title="Back again", body="x",
        )
    assert error.value.code == "account_deleted"


def test_nickname_change_updates_forum_rows(forum, community_store):
    post = _question(forum)
    community_store.update_actor_nickname(_actor("asker"), "Renamed Fern")
    assert forum.get_post(post["id"])["author"]["nickname"] == "Renamed Fern"


def test_reports_are_bounded_and_not_self(forum):
    post = _question(forum)
    assert forum.report_content(
        reporter_actor_id=_actor("reader"), target_type="post",
        target_id=post["id"], reason="spam_or_misleading",
    ) is True
    assert forum.report_content(
        reporter_actor_id=_actor("reader"), target_type="post",
        target_id=post["id"], reason="spam_or_misleading",
    ) is False
    with pytest.raises(forum.ForumError) as error:
        forum.report_content(
            reporter_actor_id=_actor("asker"), target_type="post",
            target_id=post["id"], reason="spam_or_misleading",
        )
    assert error.value.code == "self_target"
    with pytest.raises(forum.ForumError):
        forum.report_content(
            reporter_actor_id=_actor("reader"), target_type="post",
            target_id=post["id"], reason="free text",
        )


def test_legacy_second_opinion_scans_sync_into_the_forum(forum, community_store):
    community_store.record_scan(
        scan_id="legacy-1", account_actor_id=_actor("old-owner"), country="GB",
        region="London", disease="leaf_spot", species="Monstera", nickname="Old Owner",
        image_bytes=_png(),
    )
    community_store.set_wants_opinion("legacy-1", account_actor_id=_actor("old-owner"))
    community_store.record_scan(
        scan_id="legacy-2", account_actor_id=_actor("quiet"), country="GB",
        region="London", disease="healthy",
    )
    community_store.record_opinion(
        scan_id="legacy-1", account_actor_id=_actor("old-helper"),
        suggested_disease="pests", note="Look under the leaves", nickname="Old Helper",
    )
    first = forum.sync_legacy()
    assert first == {"posts": 1, "answers": 1}
    assert forum.sync_legacy() == {"posts": 0, "answers": 0}
    posts = forum.feed()["items"]
    assert len(posts) == 1
    detail = forum.get_post(posts[0]["id"])
    assert detail["kind"] == "scan" and detail["ai"]["disease"] == "leaf_spot"
    assert detail["media_count"] == 1
    assert [a["is_ai"] for a in detail["answers"]] == [True, False]
    assert detail["answers"][1]["diagnosis"] == "pests"
    assert detail["answers"][1]["body"] == "Look under the leaves"


def test_prune_drops_abandoned_uploads_and_old_tombstones(forum):
    media = forum.store_media(actor_id=_actor("asker"), image_bytes=_png(), now=1_000)
    path = forum.media_dir() / f"{media['media_id']}.jpg"
    post = _question(forum)
    forum.delete_post(actor_id=_actor("asker"), post_id=post["id"], now=2_000)
    result = forum.prune(now=int(time.time()) + 40 * 86_400)
    assert result["orphan_media"] == 1 and result["posts"] == 1
    assert not path.exists()


# ── HTTP ────────────────────────────────────────────────────────────────


def _upload(client, label: str, image: bytes | None = None, **form) -> dict:
    response = client.post(
        "/api/community/media",
        files={"image": ("leaf.png", image or _png(), "image/png")},
        data=form,
        headers=_auth(label),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_http_post_answer_like_and_public_reads(client, community_store):
    media = _upload(client, "asker")
    created = client.post(
        "/api/community/posts",
        json={
            "kind": "question",
            "title": "Brown spots with yellow halo",
            "body": "Spreading on my Monstera",
            "space": "aroids",
            "condition": "leaf_spot",
            "media_ids": [media["media_id"]],
        },
        headers=_auth("asker"),
    )
    assert created.status_code == 201, created.text
    post_id = created.json()["id"]

    answer = client.post(
        f"/api/community/posts/{post_id}/answers",
        json={"body": "Cut the leaf off", "steps": ["Sterilise scissors"], "diagnosis": "leaf_spot"},
        headers=_auth("helper"),
    )
    assert answer.status_code == 201, answer.text

    liked = client.post(
        f"/api/community/posts/{post_id}/like", json={"liked": True}, headers=_auth("helper")
    )
    assert liked.json() == {"liked": True, "like_count": 1}
    reaction = client.post(
        f"/api/community/answers/{answer.json()['id']}/reaction",
        json={"reaction": "like"},
        headers=_auth("asker"),
    )
    assert reaction.json()["like_count"] == 1

    # Reads need no account.
    feed = client.get("/api/community/feed", headers={})
    assert feed.status_code == 200
    assert [p["id"] for p in feed.json()["items"]] == [post_id]
    detail = client.get(f"/api/community/posts/{post_id}", headers={})
    assert detail.status_code == 200 and detail.json()["answers"][0]["steps"] == ["Sterilise scissors"]
    photo = client.get(f"/api/community/media/{media['media_id']}", headers={})
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg"
    search = client.get("/api/community/search", params={"q": "halo"}, headers={})
    assert [p["id"] for p in search.json()["items"]] == [post_id]
    spaces = client.get("/api/community/spaces", headers={}).json()["items"]
    assert {"slug": "aroids", "post_count": 1}.items() <= next(
        s for s in spaces if s["slug"] == "aroids"
    ).items()
    activity = client.get("/api/community/activity", headers=_auth("asker")).json()
    assert activity["items"][0]["type"] == "answer_on_post"


def test_http_writes_require_an_account(client, community_store):
    response = client.post(
        "/api/community/posts",
        json={"kind": "tip", "title": "x", "body": "y"},
        headers={"Authorization": "Bearer not.a.token"},
    )
    assert response.status_code == 401


def test_http_rejects_unknown_labels_and_extra_fields(client, community_store):
    bad_label = client.post(
        "/api/community/posts",
        json={"kind": "tip", "title": "t", "body": "b", "condition": "made_up"},
        headers=_auth("asker"),
    )
    assert bad_label.status_code == 422
    extra = client.post(
        "/api/community/posts",
        json={"kind": "tip", "title": "t", "body": "b", "actor_id": "spoof"},
        headers=_auth("asker"),
    )
    assert extra.status_code == 422


def test_http_rule_errors_map_to_status_codes(client, community_store):
    created = client.post(
        "/api/community/posts",
        json={"kind": "tip", "title": "Bottom watering", "body": "Soak 20 min"},
        headers=_auth("asker"),
    ).json()
    self_like = client.post(
        f"/api/community/posts/{created['id']}/like", json={"liked": True}, headers=_auth("asker")
    )
    assert self_like.status_code == 409 and self_like.json()["detail"] == "self_like"
    missing = client.get("/api/community/posts/fp" + "0" * 30, headers={})
    assert missing.status_code == 404


def test_http_post_quota(client, community_store, monkeypatch):
    import routes_forum

    monkeypatch.setattr(routes_forum, "FORUM_POST_DAILY_QUOTA", 1)
    first = client.post(
        "/api/community/posts",
        json={"kind": "tip", "title": "One", "body": "b"},
        headers=_auth("busy"),
    )
    second = client.post(
        "/api/community/posts",
        json={"kind": "tip", "title": "Two", "body": "b"},
        headers=_auth("busy"),
    )
    assert first.status_code == 201 and second.status_code == 429


def test_http_block_and_report_forum_content(client, community_store):
    created = client.post(
        "/api/community/posts",
        json={"kind": "tip", "title": "Buy my fertilizer", "body": "link"},
        headers=_auth("spammer"),
    ).json()
    report = client.post(
        "/api/community/forum/report",
        json={"target_type": "post", "target_id": created["id"], "reason": "spam_or_misleading"},
        headers=_auth("reader"),
    )
    assert report.json() == {"ok": True, "created": True}
    block = client.post(
        "/api/community/forum/block",
        json={"target_type": "post", "target_id": created["id"]},
        headers=_auth("reader"),
    )
    assert block.status_code == 200 and block.json()["created"] is True
    feed = client.get("/api/community/feed", headers=_auth("reader")).json()
    assert feed["items"] == []
