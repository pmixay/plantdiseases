"""Community profile photos: upload, public fetch by author token, removal.

A member's photo is opt-in (the app asks first), re-encoded as a square JPEG
and served by the opaque author token; authors in the feed and threads carry
``avatar_version`` so clients can cache-bust. Account deletion removes it.
"""

from __future__ import annotations

import io
import json
import uuid

import pytest
from PIL import Image

import community_deletion
from tests.conftest import _test_access_token
from tests.test_community_moderation import FakeRoles


def _actor(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"test-account:{label}"))


def _auth(label: str) -> dict:
    return {"Authorization": f"Bearer {_test_access_token(label, label.title())}"}


def _jpeg_with_exif(size=(900, 600), color=(40, 140, 60)) -> bytes:
    image = Image.new("RGB", size, color)
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"  # Make
    buf = io.BytesIO()
    image.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _png(color=(30, 120, 40), size=(240, 180)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def forum(community_store):
    import community_forum

    community_forum.init_db()
    return community_forum


def _put_avatar(client, label: str, image: bytes | None = None):
    return client.put(
        "/api/community/me/avatar",
        files={"image": ("me.jpg", image or _jpeg_with_exif(), "image/jpeg")},
        headers=_auth(label),
    )


# ── store ───────────────────────────────────────────────────────────────


def test_avatar_is_square_bounded_and_stripped(forum):
    result = forum.set_avatar(actor_id=_actor("ann"), image_bytes=_jpeg_with_exif((2_000, 1_200)))
    assert result["size"] == forum.AVATAR_EDGE
    path, version = forum.avatar_file(result["author_token"])
    assert version == result["avatar_version"]
    with Image.open(path) as stored:
        assert stored.format == "JPEG"
        assert stored.size == (forum.AVATAR_EDGE, forum.AVATAR_EDGE)
        assert not stored.getexif()
    small = forum.set_avatar(actor_id=_actor("bob"), image_bytes=_png(size=(120, 80)))
    assert small["size"] == 80


def test_replacing_bumps_the_version_and_removes_the_old_file(forum):
    first = forum.set_avatar(actor_id=_actor("ann"), image_bytes=_png())
    old_path, _ = forum.avatar_file(first["author_token"])
    second = forum.set_avatar(actor_id=_actor("ann"), image_bytes=_png((200, 30, 30)))
    new_path, version = forum.avatar_file(second["author_token"])
    assert second["author_token"] == first["author_token"]
    assert version == second["avatar_version"] > first["avatar_version"]
    assert new_path != old_path and not old_path.exists()


def test_author_objects_carry_the_avatar_version(forum):
    post = forum.create_post(
        actor_id=_actor("asker"), nickname="Asker", kind="question", title="Yellow leaves",
        body="Lower leaves turn yellow", space="aroids",
    )
    forum.create_answer(
        actor_id=_actor("helper"), nickname="Helper", post_id=post["id"], body="Overwatering"
    )
    assert post["author"]["avatar_version"] is None
    published = forum.set_avatar(actor_id=_actor("helper"), image_bytes=_png())

    detail = forum.get_post(post["id"])
    assert detail["author"]["avatar_version"] is None
    assert detail["answers"][0]["author"]["avatar_version"] == published["avatar_version"]
    assert _actor("helper") not in json.dumps(detail)

    forum.set_avatar(actor_id=_actor("asker"), image_bytes=_png((10, 10, 200)))
    card = forum.feed()["items"][0]
    assert isinstance(card["author"]["avatar_version"], int)


def test_delete_withdraws_the_photo(forum):
    published = forum.set_avatar(actor_id=_actor("ann"), image_bytes=_png())
    path, _ = forum.avatar_file(published["author_token"])
    assert forum.delete_avatar(actor_id=_actor("ann")) is True
    assert forum.avatar_file(published["author_token"]) is None
    assert not path.exists()
    assert forum.delete_avatar(actor_id=_actor("ann")) is False


def test_account_deletion_purges_the_avatar(forum, community_store):
    published = forum.set_avatar(actor_id=_actor("leaver"), image_bytes=_png())
    path, _ = forum.avatar_file(published["author_token"])

    community_deletion.request_actor_deletion(_actor("leaver"))
    # Hidden as soon as the tombstone exists, before the cleanup job runs.
    assert forum.avatar_file(published["author_token"]) is None
    assert community_deletion.process_cleanup_jobs() == {"completed": 1, "retried": 0}

    assert not path.exists()
    with community_store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM forum_avatars").fetchone()[0] == 0
    with pytest.raises(forum.ForumError) as error:
        forum.set_avatar(actor_id=_actor("leaver"), image_bytes=_png())
    assert error.value.code == "account_deleted"


def test_moderator_removal_bans_the_picture(forum):
    import community_moderation

    community_moderation.init_db()
    published = forum.set_avatar(actor_id=_actor("troll"), image_bytes=_png((250, 0, 250)))
    result = community_moderation.remove_avatar(
        moderator_actor_id=_actor("globalmod"), author_token=published["author_token"]
    )
    assert result["image_banned"] is True
    assert forum.avatar_file(published["author_token"]) is None
    with pytest.raises(forum.ForumError) as error:
        forum.set_avatar(actor_id=_actor("troll"), image_bytes=_png((250, 0, 250)))
    assert error.value.code == "image_not_allowed"
    with pytest.raises(forum.ForumError) as missing:
        community_moderation.remove_avatar(
            moderator_actor_id=_actor("globalmod"), author_token=published["author_token"]
        )
    assert missing.value.status == 404


def test_banned_forum_images_cannot_become_avatars(forum):
    import community_moderation

    community_moderation.init_db()
    media = forum.store_media(actor_id=_actor("spammer"), image_bytes=_png((200, 10, 10)))
    community_moderation.ban_image(admin_actor_id=_actor("admin"), media_id=media["media_id"])
    with pytest.raises(forum.ForumError) as error:
        forum.set_avatar(actor_id=_actor("other"), image_bytes=_png((200, 10, 10)))
    assert error.value.code == "image_not_allowed"


# ── HTTP ────────────────────────────────────────────────────────────────


def test_http_upload_fetch_and_delete(client, community_store):
    uploaded = _put_avatar(client, "ann")
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    token, version = body["author_token"], body["avatar_version"]
    assert token.startswith("ca") and len(token) == 32
    assert _actor("ann") not in uploaded.text

    fetched = client.get(f"/api/community/avatars/{token}", params={"v": version}, headers={})
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/jpeg"
    assert "immutable" in fetched.headers["cache-control"]
    stale = client.get(f"/api/community/avatars/{token}", params={"v": version - 1}, headers={})
    assert "must-revalidate" in stale.headers["cache-control"]
    revalidated = client.get(
        f"/api/community/avatars/{token}",
        headers={"If-None-Match": fetched.headers["etag"]},
    )
    assert revalidated.status_code == 304

    removed = client.delete("/api/community/me/avatar", headers=_auth("ann"))
    assert removed.json() == {"ok": True, "removed": True}
    assert client.get(f"/api/community/avatars/{token}", headers={}).status_code == 404


def test_http_unknown_or_malformed_token_is_404(client, community_store):
    assert client.get("/api/community/avatars/ca" + "0" * 30, headers={}).status_code == 404
    assert client.get("/api/community/avatars/not-a-token", headers={}).status_code == 404


def test_http_author_field_is_present_in_feed_and_thread(client, community_store):
    created = client.post(
        "/api/community/posts",
        json={"kind": "tip", "title": "Bottom watering", "body": "Soak 20 min"},
        headers=_auth("asker"),
    ).json()
    client.post(
        f"/api/community/posts/{created['id']}/answers",
        json={"body": "Works for my calathea"},
        headers=_auth("helper"),
    )
    assert created["author"]["avatar_version"] is None
    version = _put_avatar(client, "helper").json()["avatar_version"]

    detail = client.get(f"/api/community/posts/{created['id']}", headers={}).json()
    assert "avatar_version" in detail["author"] and detail["author"]["avatar_version"] is None
    assert detail["answers"][0]["author"]["avatar_version"] == version
    feed = client.get("/api/community/feed", headers={}).json()
    assert "avatar_version" in feed["items"][0]["author"]
    helper = detail["answers"][0]["author"]["author_token"]
    profile = client.get(f"/api/community/helpers/{helper}", headers={}).json()
    assert profile["avatar_version"] == version


def test_http_avatar_requires_an_account_and_a_real_image(client, community_store):
    anonymous = client.put(
        "/api/community/me/avatar",
        files={"image": ("me.png", _png(), "image/png")},
        headers={"Authorization": "Bearer not.a.token"},
    )
    assert anonymous.status_code == 401
    garbage = client.put(
        "/api/community/me/avatar",
        files={"image": ("me.png", b"definitely not an image", "image/png")},
        headers=_auth("ann"),
    )
    assert garbage.status_code == 400


def test_http_avatar_quota(client, community_store, monkeypatch):
    import routes_forum

    monkeypatch.setattr(routes_forum, "FORUM_AVATAR_DAILY_QUOTA", 1)
    assert _put_avatar(client, "busy").status_code == 200
    assert _put_avatar(client, "busy").status_code == 429


def test_http_moderator_removes_an_avatar(client, community_store, monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "community_roles",
        FakeRoles({"globalmod": [("moderator", "*")], "aroidmod": [("moderator", "aroids")]}),
    )
    token = _put_avatar(client, "troll").json()["author_token"]
    path = f"/api/community/admin/actors/{token}/avatar/remove"
    scoped = client.post(path, json={}, headers=_auth("aroidmod"))
    assert scoped.status_code == 403
    member = client.post(path, json={}, headers=_auth("ann"))
    assert member.status_code == 403
    removed = client.post(path, json={"ban_image": False}, headers=_auth("globalmod"))
    assert removed.status_code == 200 and removed.json()["image_banned"] is False
    assert client.get(f"/api/community/avatars/{token}", headers={}).status_code == 404
