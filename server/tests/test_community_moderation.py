"""Forum moderation: staff actions, audit, sanctions, appeals, auto-hide.

The account service's role lookup is replaced by ``FakeRoles`` on
``main.community_roles``; the HTTP layer must fail closed without it.
"""

from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image

from tests.conftest import _test_access_token


def _actor(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"test-account:{label}"))


def _auth(label: str) -> dict:
    return {"Authorization": f"Bearer {_test_access_token(label, label.title())}"}


def _png(color=(30, 120, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (200, 160), color).save(buf, format="PNG")
    return buf.getvalue()


class FakeRoles:
    def __init__(self, mapping: dict[str, list[tuple[str, str]]] | None = None):
        import account_roles

        self._role = account_roles.Role
        self.mapping = {
            _actor(label): [account_roles.Role(r, s) for r, s in roles]
            for label, roles in (mapping or {}).items()
        }
        self.changes: list[dict] = []

    def roles_for(self, actor_id):
        return list(self.mapping.get(actor_id, []))

    def holders(self):
        return [
            {
                "actor_id": actor,
                "nickname": "Someone",
                "role": role.role,
                "scope": role.scope,
                "granted_at": "2026-11-18T00:00:00+00:00",
            }
            for actor, roles in self.mapping.items()
            for role in roles
        ]

    def change(self, *, grant, actor_id, role, scope, by_actor_id):
        self.changes.append(
            {"grant": grant, "actor_id": actor_id, "role": role, "scope": scope, "by": by_actor_id}
        )
        return {"actor_id": actor_id, "role": role, "scope": scope, "created": grant}


@pytest.fixture
def mod(community_store):
    import community_moderation

    community_moderation.init_db()
    return community_moderation


@pytest.fixture
def forum(mod):
    import community_forum

    return community_forum


@pytest.fixture
def staff(monkeypatch, community_store):
    import main

    roles = FakeRoles(
        {
            "admin": [("admin", "*")],
            "globalmod": [("moderator", "*")],
            "aroidmod": [("moderator", "aroids")],
            "expert": [("expert", "*")],
        }
    )
    monkeypatch.setattr(main, "community_roles", roles)
    return roles


def _post(forum, author="author", space="aroids", title="Brown spots on my Monstera"):
    return forum.create_post(
        actor_id=_actor(author), nickname=author.title(), kind="question",
        title=title, body="Spreading fast", space=space,
    )


# ── store ───────────────────────────────────────────────────────────────


def test_remove_restore_is_audited_and_notified(mod, forum):
    post = _post(forum)
    removed = mod.moderate_post(
        moderator_actor_id=_actor("globalmod"), post_id=post["id"],
        action="remove", reason_code="spam",
    )
    with pytest.raises(forum.ForumError):
        forum.get_post(post["id"])
    assert forum.feed()["items"] == []
    notices = mod.notices_for(_actor("author"))
    assert notices["items"][0]["action_id"] == removed["action_id"]
    assert notices["items"][0]["reason_code"] == "spam"
    assert notices["items"][0]["can_appeal"] is True

    mod.moderate_post(
        moderator_actor_id=_actor("globalmod"), post_id=post["id"], action="restore"
    )
    assert forum.get_post(post["id"])["id"] == post["id"]
    audit = mod.audit()["items"]
    assert [item["action"] for item in audit] == ["restore", "remove"]
    assert all(item["moderator_token"].startswith("ca") for item in audit)
    with pytest.raises(forum.ForumError) as error:
        mod.moderate_post(
            moderator_actor_id=_actor("globalmod"), post_id=post["id"], action="remove"
        )
    assert error.value.code == "invalid_reason"


def test_answer_removal_unaccepts_and_keeps_thread(mod, forum):
    post = _post(forum)
    answer = forum.create_answer(
        actor_id=_actor("helper"), nickname="Helper", post_id=post["id"], body="Buy my cure"
    )
    forum.create_answer(
        actor_id=_actor("author"), nickname="Author", post_id=post["id"],
        parent_id=answer["id"], body="Is this legit?",
    )
    forum.accept_answer(actor_id=_actor("author"), post_id=post["id"], answer_id=answer["id"])
    mod.moderate_answer(
        moderator_actor_id=_actor("globalmod"), answer_id=answer["id"],
        action="remove", reason_code="spam",
    )
    detail = forum.get_post(post["id"])
    assert detail["solved"] is False
    assert detail["answers"][0]["state"] == "removed"
    assert detail["answers"][0]["body"] == ""
    assert detail["answer_count"] == 1


def test_three_reports_auto_hide_then_dismiss_restores(mod, forum, monkeypatch):
    monkeypatch.setattr(mod, "reporter_counts", None)
    post = _post(forum, author="spammer", title="Cheap pots link")
    for reporter in ("r1", "r2"):
        forum.report_content(
            reporter_actor_id=_actor(reporter), target_type="post",
            target_id=post["id"], reason="spam_or_misleading",
        )
    assert forum.get_post(post["id"])["state"] == "visible"
    forum.report_content(
        reporter_actor_id=_actor("r3"), target_type="post",
        target_id=post["id"], reason="spam_or_misleading",
    )
    # Hidden from everyone but its author until reviewed.
    with pytest.raises(forum.ForumError):
        forum.get_post(post["id"])
    assert forum.get_post(post["id"], viewer_actor_id=_actor("spammer"))["state"] == "hidden"
    queue = mod.queue(kind="hidden")["items"]
    assert [item["target_id"] for item in queue] == [post["id"]]
    reports = mod.queue(kind="reports")["items"]
    assert reports[0]["report_count"] == 3
    assert mod.audit()["items"][0]["automatic"] is True

    dismissed = mod.dismiss_reports(
        moderator_actor_id=_actor("globalmod"), target_type="post", target_id=post["id"]
    )
    assert dismissed["dismissed"] == 3
    assert forum.get_post(post["id"])["state"] == "visible"
    assert mod.queue(kind="reports")["items"] == []


def test_reporter_filter_hook_limits_auto_hide(mod, forum, monkeypatch):
    monkeypatch.setattr(mod, "reporter_counts", lambda _conn, actor: actor == _actor("trusted"))
    post = _post(forum, author="spammer")
    for reporter in ("trusted", "new1", "new2"):
        forum.report_content(
            reporter_actor_id=_actor(reporter), target_type="post",
            target_id=post["id"], reason="spam_or_misleading",
        )
    assert forum.get_post(post["id"])["state"] == "visible"


def test_mute_blocks_writing_but_not_liking_and_ban_blocks_everything(mod, forum):
    target = _post(forum, author="other")
    mod.sanction(
        moderator_actor_id=_actor("globalmod"), actor_id=_actor("rude"),
        kind="mute", reason_code="harassment", hours=24,
    )
    with pytest.raises(forum.ForumError) as muted:
        _post(forum, author="rude")
    assert muted.value.code == "muted" and muted.value.status == 403
    forum.set_post_like(actor_id=_actor("rude"), post_id=target["id"], liked=True)
    with pytest.raises(forum.ForumError):
        mod.sanction(
            moderator_actor_id=_actor("globalmod"), actor_id=_actor("rude"),
            kind="mute", reason_code="harassment", hours=5,
        )

    mod.sanction(
        moderator_actor_id=_actor("admin"), actor_id=_actor("rude"),
        kind="ban", reason_code="harassment",
    )
    with pytest.raises(forum.ForumError) as banned:
        forum.set_post_like(actor_id=_actor("rude"), post_id=target["id"], liked=False)
    assert banned.value.code == "banned"
    assert mod.notices_for(_actor("rude"))["active_sanction"] == {"kind": "ban", "until": None}

    mod.sanction(moderator_actor_id=_actor("admin"), actor_id=_actor("rude"), kind="unban")
    mod.sanction(moderator_actor_id=_actor("admin"), actor_id=_actor("rude"), kind="unmute")
    assert _post(forum, author="rude")["id"].startswith("fp")


def test_mutes_expire(mod, forum):
    mod.sanction(
        moderator_actor_id=_actor("globalmod"), actor_id=_actor("rude"),
        kind="mute", reason_code="harassment", hours=24, now=1_000,
    )
    assert _post(forum, author="rude")["id"]


def test_appeal_once_and_a_different_moderator_decides(mod, forum):
    post = _post(forum)
    removed = mod.moderate_post(
        moderator_actor_id=_actor("globalmod"), post_id=post["id"],
        action="remove", reason_code="off_topic",
    )
    appeal = mod.appeal(actor_id=_actor("author"), action_id=removed["action_id"], text="It is on topic")
    with pytest.raises(forum.ForumError) as twice:
        mod.appeal(actor_id=_actor("author"), action_id=removed["action_id"], text="again")
    assert twice.value.code == "already_appealed"
    with pytest.raises(forum.ForumError) as stranger:
        mod.appeal(actor_id=_actor("someone"), action_id=removed["action_id"], text="x")
    assert stranger.value.status == 404
    assert mod.queue(kind="appeals")["items"][0]["text"] == "It is on topic"

    with pytest.raises(forum.ForumError) as same:
        mod.resolve_appeal(
            reviewer_actor_id=_actor("globalmod"), appeal_id=appeal["appeal_id"], decision="overturn"
        )
    assert same.value.code == "same_moderator"
    result = mod.resolve_appeal(
        reviewer_actor_id=_actor("admin"), appeal_id=appeal["appeal_id"], decision="overturn"
    )
    assert result["state"] == "overturned"
    assert forum.get_post(post["id"])["state"] == "visible"
    assert mod.notices_for(_actor("author"))["items"][0]["appeal_state"] == "overturned"


def test_overturning_a_mute_lifts_it(mod, forum):
    muted = mod.sanction(
        moderator_actor_id=_actor("globalmod"), actor_id=_actor("rude"),
        kind="mute", reason_code="harassment", hours=168,
    )
    appeal = mod.appeal(actor_id=_actor("rude"), action_id=muted["action_id"], text="")
    mod.resolve_appeal(
        reviewer_actor_id=_actor("admin"), appeal_id=appeal["appeal_id"], decision="overturn"
    )
    assert _post(forum, author="rude")["id"]


def test_links_wait_for_a_week_and_blocked_terms(mod, forum, monkeypatch):
    with pytest.raises(forum.ForumError) as link:
        _post(forum, author="newbie", title="See www.cheap-pots.shop")
    assert link.value.code == "links_not_allowed_yet"
    monkeypatch.setattr(mod, "_blocked_terms", lambda: ["badword"])
    with pytest.raises(forum.ForumError) as term:
        _post(forum, author="newbie", title="This BADWORD thing")
    assert term.value.code == "blocked_terms"


def test_banned_image_refuses_lookalikes(mod, forum):
    media = forum.store_media(actor_id=_actor("spammer"), image_bytes=_png((200, 10, 10)))
    forum.create_post(
        actor_id=_actor("spammer"), nickname="S", kind="question",
        title="Look", media_ids=[media["media_id"]],
    )
    mod.ban_image(admin_actor_id=_actor("admin"), media_id=media["media_id"])
    with pytest.raises(forum.ForumError) as error:
        forum.store_media(actor_id=_actor("other"), image_bytes=_png((200, 10, 10)))
    assert error.value.code == "image_not_allowed"


def test_expert_verification_shows_on_the_answer(mod, forum):
    post = _post(forum)
    answer = forum.create_answer(
        actor_id=_actor("helper"), nickname="Helper", post_id=post["id"],
        body="Bacterial leaf spot", diagnosis="leaf_spot",
    )
    mod.verify_answer(expert_actor_id=_actor("expert"), answer_id=answer["id"], verified=True)
    assert forum.get_post(post["id"])["answers"][0]["verified"] is True
    with pytest.raises(forum.ForumError):
        mod.verify_answer(expert_actor_id=_actor("helper"), answer_id=answer["id"], verified=True)


def test_account_deletion_forgets_sanctions_but_keeps_decisions(mod, forum, community_store):
    post = _post(forum, author="leaver")
    mod.moderate_post(
        moderator_actor_id=_actor("globalmod"), post_id=post["id"],
        action="lock", reason_code="off_topic",
    )
    mod.sanction(
        moderator_actor_id=_actor("globalmod"), actor_id=_actor("leaver"),
        kind="warn", reason_code="off_topic",
    )
    community_store.request_actor_deletion(_actor("leaver"))
    community_store.process_cleanup_jobs()
    import community

    with community._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM forum_sanctions WHERE actor_id = ?", (_actor("leaver"),)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM forum_mod_actions WHERE target_actor_id = ?",
            (_actor("leaver"),),
        ).fetchone()[0] == 0
    assert len(mod.audit()["items"]) == 2


# ── HTTP ────────────────────────────────────────────────────────────────


def _http_post(client, author="author", space="aroids", title="Spots on leaves"):
    response = client.post(
        "/api/community/posts",
        json={"kind": "question", "title": title, "body": "help", "space": space},
        headers=_auth(author),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_http_staff_routes_fail_closed_without_the_role_service(client, community_store, monkeypatch):
    import main

    monkeypatch.setattr(main, "community_roles", None)
    me = client.get("/api/community/admin/me", headers=_auth("admin"))
    assert me.json() == {"available": False, "roles": []}
    queue = client.get("/api/community/admin/queue", headers=_auth("admin"))
    assert queue.status_code == 503


def test_http_members_cannot_use_staff_routes(client, staff):
    post = _http_post(client)
    response = client.post(
        f"/api/community/admin/posts/{post['id']}/action",
        json={"action": "remove", "reason_code": "spam"},
        headers=_auth("member"),
    )
    assert response.status_code == 403
    assert client.get("/api/community/admin/audit", headers=_auth("globalmod")).status_code == 403


def test_http_scoped_moderator_acts_only_in_their_space(client, staff):
    aroid_post = _http_post(client, space="aroids")
    cactus_post = _http_post(client, space="succulents")
    ok = client.post(
        f"/api/community/admin/posts/{aroid_post['id']}/action",
        json={"action": "lock", "reason_code": "off_topic"},
        headers=_auth("aroidmod"),
    )
    assert ok.status_code == 200, ok.text
    refused = client.post(
        f"/api/community/admin/posts/{cactus_post['id']}/action",
        json={"action": "lock"},
        headers=_auth("aroidmod"),
    )
    assert refused.status_code == 403
    move = client.post(
        f"/api/community/admin/posts/{aroid_post['id']}/action",
        json={"action": "move", "space": "succulents", "reason_code": "off_topic"},
        headers=_auth("aroidmod"),
    )
    assert move.status_code == 403
    sanction = client.post(
        f"/api/community/admin/actors/{aroid_post['author']['author_token']}/sanction",
        json={"kind": "warn", "reason_code": "spam"},
        headers=_auth("aroidmod"),
    )
    assert sanction.status_code == 403
    queue = client.get("/api/community/admin/queue?kind=hidden", headers=_auth("aroidmod"))
    assert queue.status_code == 200


def test_http_ban_needs_admin_and_member_sees_notice_and_appeals(client, staff):
    post = _http_post(client, author="rude")
    token = post["author"]["author_token"]
    by_mod = client.post(
        f"/api/community/admin/actors/{token}/sanction",
        json={"kind": "ban", "reason_code": "harassment"},
        headers=_auth("globalmod"),
    )
    assert by_mod.status_code == 403
    by_admin = client.post(
        f"/api/community/admin/actors/{token}/sanction",
        json={"kind": "ban", "reason_code": "harassment"},
        headers=_auth("admin"),
    )
    assert by_admin.status_code == 200, by_admin.text
    blocked = client.post(
        "/api/community/posts",
        json={"kind": "tip", "title": "x", "body": "y"},
        headers=_auth("rude"),
    )
    assert blocked.status_code == 403 and blocked.json()["detail"] == "banned"

    notices = client.get("/api/community/moderation/notices", headers=_auth("rude")).json()
    assert notices["active_sanction"]["kind"] == "ban"
    action_id = notices["items"][0]["action_id"]
    appeal = client.post(
        "/api/community/moderation/appeals",
        json={"action_id": action_id, "text": "I apologise"},
        headers=_auth("rude"),
    )
    assert appeal.status_code == 201
    summary = client.get(f"/api/community/admin/actors/{token}", headers=_auth("globalmod"))
    assert summary.json()["active_sanction"]["kind"] == "ban"
    decided = client.post(
        f"/api/community/admin/appeals/{appeal.json()['appeal_id']}/resolve",
        json={"decision": "overturn"},
        headers=_auth("globalmod"),
    )
    assert decided.status_code == 200
    assert client.post(
        "/api/community/posts",
        json={"kind": "tip", "title": "Back", "body": "sorry"},
        headers=_auth("rude"),
    ).status_code == 201


def test_http_admin_grants_roles_through_the_account_service(client, staff):
    post = _http_post(client, author="helpful")
    granted = client.post(
        "/api/community/admin/roles",
        json={"author_token": post["author"]["author_token"], "role": "expert"},
        headers=_auth("admin"),
    )
    assert granted.status_code == 200, granted.text
    assert staff.changes[0]["role"] == "expert" and staff.changes[0]["grant"] is True
    assert staff.changes[0]["actor_id"] == _actor("helpful")
    assert "actor_id" not in granted.json()
    holders = client.get("/api/community/admin/roles", headers=_auth("admin")).json()["items"]
    assert all("actor_id" not in item for item in holders)
    assert {item["role"] for item in holders} == {"admin", "moderator", "expert"}
    refused = client.post(
        "/api/community/admin/roles",
        json={"author_token": post["author"]["author_token"], "role": "expert"},
        headers=_auth("globalmod"),
    )
    assert refused.status_code == 403


def test_http_moderator_sees_photos_of_removed_posts(client, staff):
    upload = client.post(
        "/api/community/media",
        files={"image": ("leaf.png", _png(), "image/png")},
        headers=_auth("author"),
    ).json()
    created = client.post(
        "/api/community/posts",
        json={"kind": "question", "title": "Photo", "space": "aroids", "media_ids": [upload["media_id"]]},
        headers=_auth("author"),
    ).json()
    client.post(
        f"/api/community/admin/posts/{created['id']}/action",
        json={"action": "remove", "reason_code": "inappropriate_image"},
        headers=_auth("globalmod"),
    )
    path = f"/api/community/media/{upload['media_id']}"
    assert client.get(path, headers={}).status_code == 404
    assert client.get(path, headers=_auth("globalmod")).status_code == 200
    review = client.get(f"/api/community/admin/posts/{created['id']}", headers=_auth("expert"))
    assert review.status_code == 200 and review.json()["state"] == "removed"
