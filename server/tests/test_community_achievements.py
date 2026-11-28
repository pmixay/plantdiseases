"""Consensus labels, levels, achievements and the achievement discount."""

from __future__ import annotations

import hashlib
import io
import uuid

import pytest
from PIL import Image

import community_scans
from tests.conftest import _test_access_token
from tests.test_community_moderation import FakeRoles

DAY = 86_400
T0 = 1_800_000_000


def _actor(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"test-account:{label}"))


def _auth(label: str) -> dict:
    return {"Authorization": f"Bearer {_test_access_token(label, label.title())}"}


def _png(seed: int = 0) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (180, 140), (seed % 255, 120, (seed * 7) % 255)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def ach(community_store):
    import community_achievements

    community_achievements.init_db()
    return community_achievements


@pytest.fixture
def forum(ach):
    import community_forum

    return community_forum


@pytest.fixture
def labels(ach):
    import community_labels

    return community_labels


def _question(forum, asker, *, now, photo=True, condition=None, consent=False, title="Help"):
    media = []
    if photo:
        media = [forum.store_media(actor_id=_actor(asker), image_bytes=_png(hash(asker) % 200))["media_id"]]
    return forum.create_post(
        actor_id=_actor(asker), nickname=asker.title(), kind="question", title=title,
        body="details", media_ids=media, condition=condition, training_consent=consent, now=now,
    )


def _answer(forum, helper, post, *, now, diagnosis=None, body="Try this"):
    return forum.create_answer(
        actor_id=_actor(helper), nickname=helper.title(), post_id=post["id"],
        body=body, diagnosis=diagnosis, now=now,
    )


# ── consensus labels ────────────────────────────────────────────────────


def test_three_tags_and_an_accept_make_a_label_likes_do_not(forum, labels):
    post = _question(forum, "asker", now=T0, consent=True)
    first = _answer(forum, "h1", post, now=T0 + 10, diagnosis="pests")
    _answer(forum, "h2", post, now=T0 + 20, diagnosis="pests")
    _answer(forum, "h3", post, now=T0 + 30, diagnosis="leaf_spot")
    labels.refresh()
    assert labels.label_for(post["id"]) is None  # 2 vs 1, below 70 %

    forum.accept_answer(actor_id=_actor("asker"), post_id=post["id"], answer_id=first["id"], now=T0 + 40)
    labels.refresh()
    label = labels.label_for(post["id"])
    assert label["label"] == "pests" and label["source"] == "consensus"
    assert label["voters"] == 4 and label["agreement"] >= 0.7

    liked_only = _question(forum, "other", now=T0)
    plain = _answer(forum, "h1", liked_only, now=T0 + 5)
    for fan in ("f1", "f2", "f3", "f4"):
        forum.set_answer_reaction(actor_id=_actor(fan), answer_id=plain["id"], reaction="like")
    labels.refresh()
    assert labels.label_for(liked_only["id"]) is None


def test_expert_verification_decides_and_marks_hard_examples(forum, labels, community_store):
    import community_moderation

    image = _png(42)
    token = "scan-" + uuid.uuid4().hex
    community_scans.record_analysis_token(
        token, disease="powdery_mildew", confidence=0.9, species="Jade",
        species_score=0.9, image_sha256=hashlib.sha256(image).hexdigest(),
    )
    media = forum.store_media(actor_id=_actor("asker"), image_bytes=image, scan_token=token)
    post = forum.create_post(
        actor_id=_actor("asker"), nickname="Asker", kind="scan", title="",
        media_ids=[media["media_id"]], scan_token=token,
    )
    answer = _answer(forum, "helper", post, now=None, diagnosis="pests")
    community_moderation.init_db()
    community_moderation.verify_answer(
        expert_actor_id=_actor("expert"), answer_id=answer["id"], verified=True
    )
    labels.refresh()
    assert labels.label_for(post["id"]) == {
        "label": "pests", "source": "expert", "agreement": 1.0, "voters": 1, "hard_example": True,
    }


def test_label_review_lists_confident_ai_answers_people_push_back_on(forum, labels, community_store, monkeypatch):
    monkeypatch.setattr(forum, "can_mark_not_helpful", None)
    image = _png(7)
    token = "scan-" + uuid.uuid4().hex
    community_scans.record_analysis_token(
        token, disease="healthy", confidence=0.85, species=None,
        species_score=None, image_sha256=hashlib.sha256(image).hexdigest(),
    )
    media = forum.store_media(actor_id=_actor("asker"), image_bytes=image, scan_token=token)
    post = forum.create_post(
        actor_id=_actor("asker"), nickname="Asker", kind="scan", title="",
        media_ids=[media["media_id"]], scan_token=token,
    )
    ai_answer = post["answers"][0]["id"]
    for voter in ("v1", "v2", "v3"):
        forum.set_answer_reaction(actor_id=_actor(voter), answer_id=ai_answer, reaction="not_helpful")
    items = labels.review_queue()["items"]
    assert [item["post_id"] for item in items] == [post["id"]]
    assert items[0]["ai_not_helpful"] == 3


# ── achievements ────────────────────────────────────────────────────────


def _earned(ach, label, *, now=None) -> set[tuple[str, int]]:
    progress = ach.progress(_actor(label), now=now)
    return {
        (item["key"], tier["tier"])
        for item in progress["achievements"]
        for tier in item["tiers"]
        if tier["awarded_at"] is not None
    }


def _accepted(forum, asker, helper, *, now, condition=None, body="Do this", photo=True):
    post = _question(forum, asker, now=now, photo=photo, condition=condition)
    answer = _answer(forum, helper, post, now=now + 60, body=body)
    forum.accept_answer(actor_id=_actor(asker), post_id=post["id"], answer_id=answer["id"], now=now + 120)
    return post, answer


def test_first_aid_needs_a_photo_question(ach, forum):
    _accepted(forum, "asker", "helper", now=T0, photo=False)
    assert _earned(ach, "helper") == set()
    _accepted(forum, "asker2", "helper", now=T0)
    assert _earned(ach, "helper") == {("first_aid", 1)}


def test_one_accept_per_asker_per_week_and_three_in_total(ach, forum, labels):
    import community

    for week in range(5):
        _accepted(forum, "same-asker", "helper", now=T0 + week * 8 * DAY, condition="pests")
    _accepted(forum, "same-asker", "helper", now=T0 + 33 * DAY + 3_600, condition="pests")
    with community._connect() as conn:
        values = ach.metrics(conn, _actor("helper"))
    assert values["accepted_answers"] == 3
    assert values["pest_control"] == 3


def test_specialist_tier_and_bilingual(ach, forum):
    for index in range(5):
        _accepted(
            forum, f"asker{index}", "helper", now=T0 + index * DAY, condition="pests",
            body="Протрите листья спиртом" if index == 0 else "Wipe with alcohol",
        )
    awarded = _earned(ach, "helper")
    assert ("pest_control", 1) in awarded
    assert ("bilingual", 1) in awarded


def test_rescue_ranger_and_closing_the_loop(ach, forum):
    post, _answer_row = _accepted(forum, "asker", "helper", now=T0)
    forum.create_post(
        actor_id=_actor("asker"), nickname="Asker", kind="recovery", title="Better now",
        body="new leaves", parent_post_id=post["id"], now=T0 + 20 * DAY,
    )
    assert ("rescue_ranger", 1) in _earned(ach, "helper")
    assert ("closing_the_loop", 1) in _earned(ach, "asker")


def test_lifeline_rewards_answering_a_question_left_waiting(ach, forum):
    post = _question(forum, "asker", now=T0)
    answer = _answer(forum, "helper", post, now=T0 + 2 * DAY)
    forum.set_answer_reaction(actor_id=_actor("asker"), answer_id=answer["id"], reaction="like")
    assert ("lifeline", 1) in _earned(ach, "helper")


def test_removed_answers_stop_counting(ach, forum):
    import community_moderation

    _post, answer = _accepted(forum, "asker", "helper", now=T0)
    assert _earned(ach, "helper") == {("first_aid", 1)}
    community_moderation.init_db()
    community_moderation.moderate_answer(
        moderator_actor_id=_actor("mod"), answer_id=answer["id"], action="remove", reason_code="spam"
    )
    ach.refresh(_actor("helper"), now=T0 + 2 * DAY)
    assert ach.progress(_actor("helper"))["earned"] == 0


# ── levels and gates ────────────────────────────────────────────────────


def test_levels_gate_not_helpful(ach, forum):
    target_post = _question(forum, "asker", now=T0)
    target = _answer(forum, "someone", target_post, now=T0 + 10)
    with pytest.raises(forum.ForumError) as error:
        forum.set_answer_reaction(actor_id=_actor("voter"), answer_id=target["id"], reaction="not_helpful")
    assert error.value.code == "level_required"
    for index in range(4):  # 4 accepted answers = 60 leaves = level 2
        _accepted(forum, f"a{index}", "voter", now=T0 + index * 8 * DAY)
    import community

    with community._connect() as conn:
        assert ach.level_of(conn, _actor("voter")) == (2, 60)
    forum.set_answer_reaction(actor_id=_actor("voter"), answer_id=target["id"], reaction="not_helpful")


def test_answer_likes_are_capped_per_day(ach, forum):
    post = _question(forum, "asker", now=T0)
    answer = _answer(forum, "helper", post, now=T0 + 1)
    for fan in range(30):
        forum.set_answer_reaction(actor_id=_actor(f"fan{fan}"), answer_id=answer["id"], reaction="like")
    import community

    with community._connect() as conn:
        assert ach.leaves(conn, _actor("helper")) == 40


# ── discount ────────────────────────────────────────────────────────────


def test_discount_waits_two_weeks_and_respects_freezes(ach, forum):
    _accepted(forum, "asker", "helper", now=T0)
    early = ach.progress(_actor("helper"), include_private=True, now=T0 + DAY)
    assert early["earned"] == 1
    assert early["discount"]["achievements"] == 0
    assert early["discount"]["unlocks_at"] == T0 + 60 + 14 * DAY
    later = ach.progress(_actor("helper"), include_private=True, now=T0 + 15 * DAY)
    assert later["discount"]["percent"] == 2 and later["discount"]["unlocks_at"] is None
    ach.set_frozen(admin_actor_id=_actor("admin"), actor_id=_actor("helper"), frozen=True)
    frozen = ach.progress(_actor("helper"), include_private=True, now=T0 + 15 * DAY)
    assert frozen["discount"]["percent"] == 0 and frozen["discount"]["frozen"] is True


def test_discount_is_capped_at_forty_percent(ach):
    import community

    with community._connect() as conn:
        assert ach.discount_count(conn, _actor("nobody"), 25, T0) == 0
    assert ach.TOTAL_TIERS == 30
    assert min(30 * ach.DISCOUNT_PER_ACHIEVEMENT, ach.DISCOUNT_CAP) == 40


def test_mutual_accept_rings_are_frozen(ach, forum):
    for round_ in range(3):
        _accepted(forum, "ring-a", "ring-b", now=T0 + round_ * 8 * DAY)
        _accepted(forum, "ring-b", "ring-a", now=T0 + round_ * 8 * DAY + 3_600)
    assert ach.detect_rings(now=T0 + 30 * DAY) == 2
    assert {item["nickname"] for item in ach.frozen_queue()["items"]} == {"Ring-A", "Ring-B"}


def test_sync_reports_only_changed_counts(ach, forum):
    _accepted(forum, "asker", "helper", now=T0)
    sent: list[tuple[str, int]] = []
    report = lambda actor, count: sent.append((actor, count))  # noqa: E731
    ach.sync_rewards(report=report, now=T0 + 15 * DAY)
    assert sent == [(_actor("helper"), 1)]
    ach.sync_rewards(report=report, now=T0 + 16 * DAY)
    assert sent == [(_actor("helper"), 1)]


# ── HTTP ────────────────────────────────────────────────────────────────


def test_http_helper_profile_is_public_and_private_view_has_discount(client, ach, forum, monkeypatch):
    import main

    _accepted(forum, "asker", "helper", now=T0)
    me = client.get("/api/community/me/achievements", headers=_auth("helper")).json()
    assert me["earned"] == 1 and "discount" in me
    public = client.get(f"/api/community/helpers/{me['author_token']}", headers={})
    assert public.status_code == 200
    assert "discount" not in public.json()
    assert public.json()["stats"]["accepted_answers"] == 1

    roles = FakeRoles({"admin": [("admin", "*")], "expert": [("expert", "*")]})
    monkeypatch.setattr(main, "community_roles", roles)
    freeze = client.post(
        f"/api/community/admin/actors/{me['author_token']}/rewards",
        json={"frozen": True},
        headers=_auth("admin"),
    )
    assert freeze.status_code == 200
    rewards = client.get("/api/community/admin/queue?kind=rewards", headers=_auth("admin")).json()
    assert rewards["items"][0]["author_token"] == me["author_token"]
    review = client.get("/api/community/admin/queue?kind=label_review", headers=_auth("expert"))
    assert review.status_code == 200
    assert client.get(
        "/api/community/admin/queue?kind=rewards", headers=_auth("expert")
    ).status_code == 403
