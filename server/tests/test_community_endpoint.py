"""Coverage for /api/community/* and the community.py module.

Endpoint tests isolate the DB + thumb directory per test via env-var
indirection, so no two tests can step on each other's rows.
"""

from __future__ import annotations

import hashlib
import io
import sqlite3
import uuid
from contextlib import closing

import pytest

import community_deletion
import community_feeds
import community_scans
import community_views
from server.tests.conftest import _test_access_token


@pytest.fixture
def isolated_community(community_http_store, tmp_path):
    """Point the community store at a temp DB + thumb dir per test."""
    yield community_http_store.COMMUNITY_DB_PATH, community_http_store.COMMUNITY_THUMB_DIR


@pytest.fixture
def strict_community(community_strict_http_store):
    """``isolated_community`` with the real analysis-token and scan-write checks."""
    yield community_strict_http_store.COMMUNITY_DB_PATH, community_strict_http_store.COMMUNITY_THUMB_DIR


def _form(**fields):
    """Compact form-data builder; pytest-fastapi accepts a dict."""
    return {k: str(v) for k, v in fields.items() if v is not None}


def _png_bytes() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(60, 140, 60)).save(buf, format="PNG")
    return buf.getvalue()


def _account_headers(identity: str, nickname: str = "Signed Fern") -> dict[str, str]:
    return {"Authorization": f"Bearer {_test_access_token(identity, nickname)}"}


# ── Happy paths ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "body", "is_form"),
    [
        (
            "/api/community/scan",
            {"scan_id": "authgate001", "disease": "healthy"},
            True,
        ),
        ("/api/community/view", {"scan_id": "authgate001"}, False),
    ],
)
def test_community_mutations_require_bearer_account(
    client, isolated_community, path, body, is_form,
):
    kwargs = {"data" if is_form else "json": body, "headers": {"Authorization": ""}}
    response = client.post(path, **kwargs)
    assert response.status_code == 401
    assert response.json()["detail"] == "auth_required"


def test_signed_claims_override_spoofed_legacy_identity_fields(
    client, isolated_community,
):
    db_path, _ = isolated_community
    response = client.post(
        "/api/community/scan",
        headers=_account_headers("real-owner", "Signed Orchid"),
        data=_form(
            scan_id="claimbound01",
            user_pseudo_id="spoofed-victim",
            nickname="Spoofed Nickname",
            country="GB",
            region="London",
            disease="healthy",
        ),
    )
    assert response.status_code == 200, response.text
    import uuid

    with closing(sqlite3.connect(db_path)) as connection, connection:
        row = connection.execute(
            "SELECT user_pseudo_id, account_actor_id, nickname "
            "FROM community_scans WHERE scan_id = 'claimbound01'"
        ).fetchone()
    assert row == (
        None,
        str(uuid.uuid5(uuid.NAMESPACE_URL, "test-account:real-owner")),
        "Signed Orchid",
    )


def test_anonymous_reads_create_no_stable_viewer_records(client, isolated_community):
    db_path, _ = isolated_community
    uploaded = client.post(
        "/api/community/scan",
        headers=_account_headers("reader-test-owner"),
        data=_form(
            scan_id="anonymousread1",
            country="GB",
            region="London",
            disease="healthy",
        ),
    )
    assert uploaded.status_code == 200
    feed = client.get(
        "/api/community/nearby",
        headers={"Authorization": ""},
        params={"country": "GB", "scope": "country", "user_pseudo_id": "ignored"},
    )
    assert feed.status_code == 200 and len(feed.json()["items"]) == 1
    with closing(sqlite3.connect(db_path)) as connection, connection:
        assert connection.execute("SELECT COUNT(*) FROM community_scan_views").fetchone()[0] == 0


def test_legacy_rows_remain_readable_but_are_never_mutable(
    client, isolated_community,
):
    import community as comm

    community_scans.record_scan(
        scan_id="legacyreadonly1",
        user_pseudo_id="legacy-install",
        country="GB",
        region="London",
        disease="healthy",
    )
    feed = client.get(
        "/api/community/nearby",
        headers={"Authorization": ""},
        params={"country": "GB", "scope": "country"},
    )
    assert any(item["scan_id"] == comm.public_scan_token("legacyreadonly1") for item in feed.json()["items"])

    headers = _account_headers("new-account-viewer")
    view = client.post(
        "/api/community/view",
        headers=headers,
        json={"scan_id": "legacyreadonly1"},
    )
    assert view.status_code == 409


def test_internal_deletion_hook_is_secret_guarded_and_idempotent(
    client, isolated_community, monkeypatch,
):
    import uuid

    import account_internal

    client.post(
        "/api/community/scan",
        headers=_account_headers("delete-hook-owner"),
        data=_form(
            scan_id="deletehook001",
            country="GB",
            region="London",
            disease="healthy",
        ),
    )
    actor = str(uuid.uuid5(uuid.NAMESPACE_URL, "test-account:delete-hook-owner"))

    monkeypatch.setattr(
        account_internal,
        "internal_service_authorized",
        lambda supplied: supplied == "synthetic-internal-secret",
    )
    denied = client.post(
        "/api/internal/account-deletions",
        headers={"X-Internal-Service-Token": "wrong"},
        json={"actor_id": actor},
    )
    assert denied.status_code == 401

    for _attempt in range(2):
        accepted = client.post(
            "/api/internal/account-deletions",
            headers={"X-Internal-Service-Token": "synthetic-internal-secret"},
            json={"actor_id": actor},
        )
        assert accepted.status_code == 202
    feed = client.get(
        "/api/community/nearby",
        headers={"Authorization": ""},
        params={"country": "GB", "scope": "country"},
    )
    assert feed.json()["items"] == []


def test_internal_nickname_hook_replaces_historical_actor_rows(
    client, isolated_community, monkeypatch,
):
    import uuid

    import account_internal

    owner_headers = _account_headers("nickname-owner", "Old Fern")
    client.post(
        "/api/community/scan",
        headers=owner_headers,
        data=_form(
            scan_id="nicknamehist01",
            country="GB",
            region="London",
            disease="healthy",
        ),
    )
    owner_actor = str(uuid.uuid5(uuid.NAMESPACE_URL, "test-account:nickname-owner"))
    monkeypatch.setattr(
        account_internal,
        "internal_service_authorized",
        lambda supplied: supplied == "synthetic-internal-secret",
    )

    response = client.post(
        "/api/internal/account-nicknames",
        headers={"X-Internal-Service-Token": "synthetic-internal-secret"},
        json={"actor_id": owner_actor, "nickname": "New Fern"},
    )
    assert response.status_code == 202

    db_path, _ = isolated_community
    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        stored = conn.execute(
            "SELECT nickname FROM community_scans WHERE account_actor_id = ?",
            (owner_actor,),
        ).fetchall()
    assert stored == [("New Fern",)]


def test_community_scan_upload_minimal(client, isolated_community):
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanabc123",
            user_pseudo_id="pseudoabc123",
            region="London", country="GB",
            disease="powdery_mildew",
        ),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["id"] > 0


def test_community_scan_upload_stores_client_location(client, isolated_community):
    # Location is now supplied by the (opted-in) client: country + city + the
    # rounded coarse coords. There is no server-side IP/header geo.
    db_path, _ = isolated_community
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scangeo001",
            user_pseudo_id="pseudogeo01",
            country="US", region="Austin",
            lat=30.27, lon=-97.74,
            disease="powdery_mildew",
        ),
    )
    assert r.status_code == 200, r.text
    with closing(sqlite3.connect(db_path)) as conn, conn:
        row = conn.execute(
            "SELECT country, region, city, lat, lon FROM community_scans WHERE scan_id = ?",
            ("scangeo001",),
        ).fetchone()
    assert row[0] == "US"
    assert row[1] == "Austin" and row[2] == "Austin"
    assert abs(row[3] - 30.27) < 1e-6 and abs(row[4] - (-97.74)) < 1e-6


def test_community_scan_upload_no_location_stores_empty_country(client, isolated_community):
    # No client location (community off / permission denied) → empty country;
    # such rows only surface in the worldwide aggregate, never "RU"/"GLOBAL".
    db_path, _ = isolated_community
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scandefault1",
            user_pseudo_id="pseudodefault1",
            disease="healthy",
        ),
    )
    assert r.status_code == 200, r.text
    with closing(sqlite3.connect(db_path)) as conn, conn:
        row = conn.execute(
            "SELECT country, lat FROM community_scans WHERE scan_id = ?",
            ("scandefault1",),
        ).fetchone()
    assert row[0] == ""
    assert row[1] is None


def test_community_scan_upload_rejects_unissued_scan_id(
    client, isolated_community, monkeypatch,
):
    # Integrity gate: a scan_id the server never issued at /api/analyze is
    # refused (422) so a client can't post a fabricated diagnosis into the
    # public feed under a made-up or copied publication capability.
    db_path, _ = isolated_community
    monkeypatch.setattr(
        community_scans, "is_analysis_token_issued", lambda *a, **k: False
    )
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="forgedid1234",
            user_pseudo_id="pseudoforged1",
            country="NL", region="Amsterdam",
            disease="gray_mold_botrytis",
            confidence=0.99,
        ),
    )
    assert r.status_code == 422, r.text
    # Nothing was written to the corpus — the id is refused before any DB
    # write, so a follow-up nearby query surfaces no such scan.
    feed = client.get(
        "/api/community/nearby",
        params={"country": "NL", "scope": "country", "user_pseudo_id": "pseudo-check"},
    )
    assert all(item["disease"] != "gray_mold_botrytis" for item in feed.json()["items"])


def test_analysis_publication_uses_server_claims_and_is_actor_idempotent(
    client, strict_community, monkeypatch,
):
    db_path, _ = strict_community

    image_bytes = _png_bytes()
    community_scans.record_analysis_token(
        "bound-analysis-1",
        disease="leaf_spot",
        confidence=0.73,
        species="Server species",
        species_score=0.81,
        image_sha256=hashlib.sha256(image_bytes).hexdigest(),
    )
    upload = _form(
        scan_id="bound-analysis-1",
        disease="healthy",
        confidence=0.99,
        species="Spoofed species",
        species_score=0.98,
        country="GB",
    )
    first = client.post(
        "/api/community/scan",
        headers=_account_headers("claim-owner"),
        data=upload,
        files={"image": ("scan.png", image_bytes, "image/png")},
    )
    replay = client.post(
        "/api/community/scan",
        headers=_account_headers("claim-owner"),
        data=upload,
        files={"image": ("scan.png", image_bytes, "image/png")},
    )
    conflict = client.post(
        "/api/community/scan",
        headers=_account_headers("other-owner"),
        data=upload,
        files={"image": ("scan.png", image_bytes, "image/png")},
    )

    assert first.status_code == replay.status_code == 200, (first.text, replay.text)
    assert first.json()["id"] == replay.json()["id"]
    assert conflict.status_code == 409
    with closing(sqlite3.connect(db_path)) as connection, connection:
        rows = connection.execute(
            "SELECT disease, confidence, species, species_score, account_actor_id "
            "FROM community_scans WHERE scan_id = 'bound-analysis-1'"
        ).fetchall()
        claimed_actor = connection.execute(
            "SELECT claimed_actor_id FROM community_analysis_tokens "
            "WHERE token = 'bound-analysis-1'"
        ).fetchone()[0]
    expected_actor = str(uuid.uuid5(uuid.NAMESPACE_URL, "test-account:claim-owner"))
    assert rows == [("leaf_spot", 0.73, "Server species", 0.81, expected_actor)]
    assert claimed_actor == expected_actor


def test_analysis_publication_rejects_missing_or_substituted_image(
    client, strict_community, monkeypatch,
):
    db_path, thumb_dir = strict_community

    analyzed_bytes = _png_bytes()
    different = io.BytesIO()
    from PIL import Image

    Image.new("RGB", (32, 32), color=(180, 35, 30)).save(different, format="PNG")
    substituted_bytes = different.getvalue()
    community_scans.record_analysis_token(
        "image-bound-analysis-1",
        disease="leaf_spot",
        confidence=0.73,
        species=None,
        species_score=None,
        image_sha256=hashlib.sha256(analyzed_bytes).hexdigest(),
    )
    payload = _form(
        scan_id="image-bound-analysis-1",
        disease="leaf_spot",
        country="GB",
    )

    substituted = client.post(
        "/api/community/scan",
        headers=_account_headers("image-owner"),
        data=payload,
        files={"image": ("other.png", substituted_bytes, "image/png")},
    )
    missing = client.post(
        "/api/community/scan",
        headers=_account_headers("image-owner"),
        data=payload,
    )
    assert substituted.status_code == missing.status_code == 422
    assert substituted.json()["detail"] == "Community image does not match the analyzed image."
    assert missing.json()["detail"] == "Community image does not match the analyzed image."
    with closing(sqlite3.connect(db_path)) as connection, connection:
        assert connection.execute(
            "SELECT claimed_actor_id FROM community_analysis_tokens "
            "WHERE token = 'image-bound-analysis-1'"
        ).fetchone()[0] is None
        assert connection.execute(
            "SELECT COUNT(*) FROM community_scans WHERE scan_id = 'image-bound-analysis-1'"
        ).fetchone()[0] == 0
    assert not thumb_dir.exists() or not any(thumb_dir.iterdir())

    accepted = client.post(
        "/api/community/scan",
        headers=_account_headers("image-owner"),
        data=payload,
        files={"image": ("scan.png", analyzed_bytes, "image/png")},
    )
    wrong_replay = client.post(
        "/api/community/scan",
        headers=_account_headers("image-owner"),
        data=payload,
        files={"image": ("other.png", substituted_bytes, "image/png")},
    )
    assert accepted.status_code == 200, accepted.text
    assert wrong_replay.status_code == 422


def test_failed_analysis_insert_does_not_burn_publication_token(
    client, strict_community, monkeypatch,
):
    db_path, _ = strict_community

    image_bytes = _png_bytes()
    community_scans.record_analysis_token(
        "retry-analysis-1",
        disease="healthy",
        confidence=0.9,
        species=None,
        species_score=None,
        image_sha256=hashlib.sha256(image_bytes).hexdigest(),
    )
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute(
            "CREATE TRIGGER fail_analysis_insert BEFORE INSERT ON community_scans "
            "WHEN NEW.scan_id = 'retry-analysis-1' BEGIN "
            "SELECT RAISE(ABORT, 'synthetic insert failure'); END"
        )
    payload = _form(scan_id="retry-analysis-1", disease="healthy", country="GB")
    failed = client.post(
        "/api/community/scan",
        headers=_account_headers("retry-owner"),
        data=payload,
        files={"image": ("scan.png", image_bytes, "image/png")},
    )
    assert failed.status_code == 500, failed.text
    with closing(sqlite3.connect(db_path)) as connection, connection:
        assert connection.execute(
            "SELECT claimed_actor_id FROM community_analysis_tokens "
            "WHERE token = 'retry-analysis-1'"
        ).fetchone()[0] is None
        connection.execute("DROP TRIGGER fail_analysis_insert")

    retry = client.post(
        "/api/community/scan",
        headers=_account_headers("retry-owner"),
        data=payload,
        files={"image": ("scan.png", image_bytes, "image/png")},
    )
    assert retry.status_code == 200


def test_unclaimed_analysis_token_scan_id_collision_is_conflict(
    client, strict_community, monkeypatch,
):

    image_bytes = _png_bytes()
    community_scans.record_analysis_token(
        "collision-analysis-1",
        disease="healthy",
        confidence=0.9,
        species=None,
        species_score=None,
        image_sha256=hashlib.sha256(image_bytes).hexdigest(),
    )
    community_scans.record_scan(
        scan_id="collision-analysis-1",
        user_pseudo_id="legacy-collision",
        country="GB",
        disease="leaf_spot",
    )

    response = client.post(
        "/api/community/scan",
        headers=_account_headers("collision-owner"),
        data=_form(scan_id="collision-analysis-1", disease="healthy", country="GB"),
        files={"image": ("scan.png", image_bytes, "image/png")},
    )
    assert response.status_code == 409


def test_community_scan_upload_with_thumbnail(client, isolated_community):
    _, thumb_dir = isolated_community
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanthumb01",
            user_pseudo_id="pseudothumb",
            region="Berlin", country="DE",
            disease="gray_mold_botrytis",
            confidence=0.71,
            species="Begonia",
            species_score=0.92,
            locale="en",
        ),
        files={"image": ("scan.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 200
    # Thumbnail file was written.
    assert any(thumb_dir.iterdir()), "thumb dir should contain at least one file"


def test_community_thumbnail_is_oriented_resized_and_metadata_free(
    client, isolated_community,
):
    """_save_thumbnail used to write whatever bytes the client sent straight
    to disk — its own docstring claimed a 512px JPEG while enforcing nothing
    of the kind. A modified client could put a full-resolution, GPS-tagged
    photo behind a publicly served .jpg filename. Now it decodes, applies the
    orientation, downscales and drops every metadata block."""
    db_path, thumb_dir = isolated_community
    from PIL import Image

    sentinel = "PRIVATE_CAMERA_SENTINEL_7D1C"
    original_name = "private-family-album-sentinel.jpg"
    source = Image.new("RGB", (800, 400), color=(35, 145, 55))
    exif = Image.Exif()
    exif[274] = 6  # rotate 90 degrees clockwise when orientation is applied
    exif[271] = sentinel
    exif[272] = f"{sentinel}-MODEL"
    exif[306] = "2026:04:03 02:01:00"
    encoded = io.BytesIO()
    source.save(
        encoded,
        format="JPEG",
        exif=exif,
        icc_profile=f"{sentinel}-ICC".encode(),
    )

    response = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="sanitizedthumb1",
            country="DE",
            region="Berlin",
            disease="healthy",
        ),
        files={"image": (original_name, encoded.getvalue(), "image/jpeg")},
    )
    assert response.status_code == 200, response.text

    files = list(thumb_dir.iterdir())
    assert len(files) == 1
    stored = files[0].read_bytes()
    assert files[0].name.endswith(
        f"_{hashlib.sha256(stored).hexdigest()[:24]}.jpg"
    )
    assert sentinel.encode() not in stored
    assert original_name.encode() not in stored
    with Image.open(io.BytesIO(stored)) as sanitized:
        assert sanitized.format == "JPEG"
        assert sanitized.mode == "RGB"
        assert sanitized.size == (256, 512)
        assert not sanitized.getexif()
        assert "icc_profile" not in sanitized.info
        assert "xmp" not in sanitized.info

    persisted = db_path.read_bytes()
    assert sentinel.encode() not in persisted
    assert original_name.encode() not in persisted


# ── Validation guard-rails ──────────────────────────────────────────


def test_community_scan_rejects_unknown_disease(client, isolated_community):
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanunknown1",
            user_pseudo_id="pseudoabc123",
            region="London", country="GB",
            disease="alien_invasion",
        ),
    )
    assert r.status_code == 422


def test_community_scan_rejects_bad_region(client, isolated_community):
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanregion1",
            user_pseudo_id="pseudoabc123",
            region="bad\nregion;DROP TABLE",
            disease="healthy",
        ),
    )
    assert r.status_code == 422


def test_community_scan_accepts_unicode_region(client, isolated_community):
    """Region validation is Unicode-aware: Cyrillic / accented place names
    pass (it filters control chars + injection punctuation, not non-Latin
    letters). The country name additionally canonicalizes to its ISO code
    ("Россия" -> "RU") while the locality is preserved verbatim."""
    db_path, _ = isolated_community
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanru000001",
            user_pseudo_id="pseudoabc123",
            country="Россия", region="Москва",
            disease="healthy",
        ),
    )
    assert r.status_code == 200, r.text
    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        row = conn.execute(
            "SELECT country, region FROM community_scans WHERE scan_id = ?",
            ("scanru000001",),
        ).fetchone()
    assert row == ("RU", "Москва")


def test_community_scan_rejects_short_scan_id(client, isolated_community):
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="short",
            user_pseudo_id="pseudoabc123",
            region="London", country="GB",
            disease="healthy",
        ),
    )
    assert r.status_code == 422


def test_community_scan_thumbnail_size_limit(client, isolated_community):
    # 1 MB of bytes triggers the COMMUNITY_MAX_THUMB_BYTES guard inside
    # _save_thumbnail; the row is still recorded but with NULL
    # thumb_path. The endpoint succeeds.
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (1 * 1024 * 1024)
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanbig00001",
            user_pseudo_id="pseudoabc123",
            region="London", country="GB",
            disease="healthy",
        ),
        files={"image": ("big.png", big, "image/png")},
    )
    assert r.status_code == 200


def test_community_thumbnail_rejects_undecodable_bytes(client, isolated_community):
    """Bytes that pass the size cap but aren't a real image (a modified
    client, or a truncated upload) must not land on disk at all — the row
    is still recorded (best-effort thumbnail), just with no thumb_path."""
    db_path, thumb_dir = isolated_community
    garbage = b"\x89PNG\r\n\x1a\n" + b"not actually a png" * 100
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scangarbage1",
            user_pseudo_id="pseudogarba1",
            region="Berlin", country="DE",
            disease="healthy",
        ),
        files={"image": ("scan.png", garbage, "image/png")},
    )
    assert r.status_code == 200, r.text
    assert list(thumb_dir.iterdir()) == []
    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        row = conn.execute(
            "SELECT thumb_path FROM community_scans WHERE scan_id = ?",
            ("scangarbage1",),
        ).fetchone()
    assert row[0] is None


# ── Nearby + region summary ─────────────────────────────────────────


def test_community_nearby_lists_recent_rows(client, isolated_community):
    for i, disease in enumerate([
        "powdery_mildew", "powdery_mildew", "gray_mold_botrytis", "healthy",
    ]):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=f"scanrow{i:04d}",
                user_pseudo_id="pseudo-london-x",
                region="London", country="GB",
                disease=disease,
            ),
        )
    r = client.get(
        "/api/community/nearby",
        params={"region": "London", "country": "GB", "disease": "powdery_mildew", "limit": 10},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    diseases = [item["disease"] for item in body["items"]]
    # Same-disease rows are listed first.
    assert diseases[:2] == ["powdery_mildew", "powdery_mildew"]
    # Region summary reports total + last_7d.
    assert body["summary"]["total"] == 4
    assert body["summary"]["last_7d"] == 4


def test_community_nearby_excludes_signed_viewers_own_scan(
    client, isolated_community,
):
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanselfid01",
            user_pseudo_id="pseudo-self-x",
            region="Paris", country="FR",
            disease="healthy",
        ),
    )
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanotherid1",
            user_pseudo_id="pseudo-other-y",
            region="Paris", country="FR",
            disease="healthy",
        ),
    )
    r = client.get(
        "/api/community/nearby",
        params={"region": "Paris", "country": "FR", "user_pseudo_id": "pseudo-self-x"},
    )
    body = r.json()
    # The feed exposes the PUBLIC token in the scan_id field, never the
    # publication capability returned by analysis.
    import community as comm
    assert {item["scan_id"] for item in body["items"]} == {
        comm.public_scan_token("scanotherid1"),
    }
    # The summary block must apply the same exclusion. Otherwise the
    # chip row on Android shows a phantom disease — the user's own
    # scan they just uploaded — alongside the carousel that already
    # filters them out, which was confusing in practice.
    summary = body["summary"]
    assert summary["total"] == 1
    assert summary["last_7d"] == 1
    assert [d["disease"] for d in summary["top_diseases"]] == ["healthy"]
    assert summary["top_diseases"][0]["count"] == 1


def test_community_nearby_hides_not_a_plant(client, isolated_community):
    """``not_a_plant`` is noise in the community feed — typically faces
    or random objects — and must not surface in either the carousel
    rows or the summary aggregates, even if it's the freshest row in
    the bucket.
    """
    # One healthy row + two not_a_plant rows, none from the requesting
    # user so the user-exclusion filter can't account for the result.
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="hidehealthy1",
            user_pseudo_id="pseudo-feed-x",
            region="Berlin", country="DE",
            disease="healthy",
        ),
    )
    for sid in ("hidenotplant1", "hidenotplant2"):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=sid,
                user_pseudo_id="pseudo-feed-x",
                region="Berlin", country="DE",
                disease="not_a_plant",
            ),
        )
    r = client.get(
        "/api/community/nearby",
        params={"region": "Berlin", "country": "DE"},
    )
    body = r.json()
    assert {item["disease"] for item in body["items"]} == {"healthy"}
    summary = body["summary"]
    assert summary["total"] == 1
    assert summary["last_7d"] == 1
    assert [d["disease"] for d in summary["top_diseases"]] == ["healthy"]

    # Even an explicit ``disease=not_a_plant`` query must not surface
    # any hidden rows. The filler stage may return other diagnoses,
    # but no item in the carousel can carry the hidden disease.
    r2 = client.get(
        "/api/community/nearby",
        params={"region": "Berlin", "country": "DE", "disease": "not_a_plant"},
    )
    assert all(item["disease"] != "not_a_plant" for item in r2.json()["items"])


def test_community_nearby_disease_filter_is_strict(client, isolated_community):
    """A disease-scoped query must return ONLY that disease — never the
    region/country fillers of other diseases. This is the "similar scans
    near you" contract: same-disease or nothing, so the user is never
    shown an unrelated verdict dressed up as a 'similar' scan.
    """
    for i, disease in enumerate([
        "powdery_mildew", "gray_mold_botrytis", "healthy", "leaf_spot",
    ]):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=f"strict{i:05d}",
                user_pseudo_id="pseudo-strict-x",
                region="Lyon", country="FR",
                disease=disease,
            ),
        )
    r = client.get(
        "/api/community/nearby",
        params={"region": "Lyon", "country": "FR", "disease": "powdery_mildew", "limit": 10},
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    # Only the single powdery_mildew row — no gray_mold / healthy / leaf_spot
    # fillers padding the carousel.
    assert [item["disease"] for item in items] == ["powdery_mildew"]


def test_community_nearby_worldwide_ignores_location(client, isolated_community):
    """``scope=worldwide`` returns scans from every country, newest first,
    regardless of the requester's country/coords — and needs no location, so
    ``location_required`` is never set even when the caller sends nothing."""
    for i, country in enumerate(["PL", "US", "JP", "BR"]):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=f"world{i:05d}",
                user_pseudo_id="pseudo-world-up",
                region="Somewhere", country=country,
                disease="leaf_spot",
            ),
        )
    # No country, no coords, no region — a geo-scoped feed would be empty and
    # flag location_required; worldwide fills regardless.
    r = client.get(
        "/api/community/nearby",
        params={"scope": "worldwide", "user_pseudo_id": "pseudo-viewer-w", "limit": 10},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "worldwide"
    assert body["location_required"] is False
    assert len(body["items"]) == 4  # all four countries surfaced
    # Backward compat: the nested summary.country must never serialize as null
    # (older installed builds decode it as a non-null String and crash on null).
    assert body["summary"]["country"] == ""

    # Country scope with no anchor still flags location_required (contrast).
    r_country = client.get(
        "/api/community/nearby",
        params={"scope": "country", "user_pseudo_id": "pseudo-viewer-w"},
    )
    assert r_country.json()["location_required"] is True


def test_community_nearby_handles_empty_region(client, isolated_community):
    r = client.get(
        "/api/community/nearby",
        params={"region": "Nowheresville", "country": "ZZ"},
    )
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["summary"]["total"] == 0


def test_community_nearby_no_location_flags_location_required(client, isolated_community):
    # Neither coords nor country → the feed can't be geo-scoped. The response
    # must say so explicitly so the client prompts "turn on location" instead
    # of a misleading "no scans near you".
    r = client.get(
        "/api/community/nearby",
        params={"user_pseudo_id": "pseudo-no-geo"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["location_required"] is True


def test_community_nearby_with_location_not_flagged(client, isolated_community):
    # A country-scoped request has a geo anchor, so location_required is False.
    r = client.get(
        "/api/community/nearby",
        params={"country": "US", "scope": "country", "user_pseudo_id": "pseudo-geo"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["location_required"] is False


def test_community_nearby_country_param(client, isolated_community):
    # Location is client-supplied now (no header geo); a country-scoped query
    # passes country explicitly.
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scangeofeed1",
            user_pseudo_id="pseudo-other-y",
            country="US",
            disease="healthy",
        ),
    )
    r = client.get(
        "/api/community/nearby",
        params={"country": "US", "scope": "country", "user_pseudo_id": "pseudo-viewer-q"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "country"
    assert body["country"] == "US"
    assert body["summary"]["country_name_en"] == "United States"
    assert len(body["items"]) == 1


# ── nearby (30 km radius) vs country filter toggle ─────────────────


def test_community_nearby_scope_toggle(client, isolated_community):
    """`scope=nearby` restricts the feed to a ~30 km radius of the requester's
    coords; `scope=country` widens to the whole country. Coords ride in on the
    upload's lat/lon and the query's lat/lon."""
    # Two scans ~3 km apart in Warsaw, one ~300 km away in Gdańsk — all PL.
    client.post("/api/community/scan", data=_form(
        scan_id="nearwarsaw01", user_pseudo_id="pseudo-near-a",
        country="PL", region="Warsaw", lat=52.23, lon=21.01, disease="leaf_spot"))
    client.post("/api/community/scan", data=_form(
        scan_id="nearwarsaw02", user_pseudo_id="pseudo-near-a",
        country="PL", region="Warsaw", lat=52.25, lon=21.03, disease="leaf_spot"))
    client.post("/api/community/scan", data=_form(
        scan_id="fargdansk001", user_pseudo_id="pseudo-near-a",
        country="PL", region="Gdansk", lat=54.35, lon=18.65, disease="leaf_spot"))

    import community as comm
    # nearby (within 30 km of Warsaw): only the two Warsaw scans.
    near = client.get("/api/community/nearby", params={
        "country": "PL", "lat": 52.24, "lon": 21.02, "scope": "nearby",
        "user_pseudo_id": "pseudo-viewer-z"}).json()
    assert near["scope"] == "nearby"
    assert {it["scan_id"] for it in near["items"]} == {
        comm.public_scan_token("nearwarsaw01"),
        comm.public_scan_token("nearwarsaw02"),
    }

    # country: all three PL scans regardless of distance.
    whole = client.get("/api/community/nearby", params={
        "country": "PL", "scope": "country",
        "user_pseudo_id": "pseudo-viewer-z"}).json()
    assert whole["scope"] == "country"
    assert {it["scan_id"] for it in whole["items"]} == {
        comm.public_scan_token("nearwarsaw01"),
        comm.public_scan_token("nearwarsaw02"),
        comm.public_scan_token("fargdansk001"),
    }


def test_community_nearby_attaches_distance_and_hides_coords(client, isolated_community):
    """Nearby feed rows carry an approximate distance from the requester
    ('~5 km near you') but NEVER the other user's raw coords."""
    client.post("/api/community/scan", data=_form(
        scan_id="distnearwarsaw", user_pseudo_id="pseudo-near-a",
        country="PL", region="Warsaw", lat=52.23, lon=21.01, disease="leaf_spot"))
    items = client.get("/api/community/nearby", params={
        "country": "PL", "lat": 52.24, "lon": 21.02, "scope": "nearby",
        "user_pseudo_id": "pseudo-viewer-z"}).json()["items"]
    assert len(items) == 1
    item = items[0]
    # Distance present, within the radius, and coords stripped.
    assert item["distance_km"] is not None
    assert 0 <= item["distance_km"] <= 30
    assert "lat" not in item and "lon" not in item


def test_community_nearby_no_viewer_coords_omits_distance(client, isolated_community):
    """Country scope (no requester coords) → distance is null, coords stripped."""
    client.post("/api/community/scan", data=_form(
        scan_id="distcountry001", user_pseudo_id="pseudo-other-y",
        country="US", disease="healthy"))
    item = client.get("/api/community/nearby", params={
        "country": "US", "scope": "country",
        "user_pseudo_id": "pseudo-viewer-q"}).json()["items"][0]
    assert item["distance_km"] is None
    assert "lat" not in item and "lon" not in item


# ── Localized per-item country names (no raw "PL" codes) ────────────


def test_community_nearby_localizes_item_country(client, isolated_community):
    """Feed rows carry localized country NAMES so cards render 'Poland' /
    'Польша' instead of the raw 'PL' ISO code."""
    client.post("/api/community/scan", data=_form(
        scan_id="localename01", user_pseudo_id="pseudo-pl-b",
        country="PL", region="Warsaw", disease="leaf_spot"))
    item = client.get(
        "/api/community/nearby",
        params={"country": "PL", "scope": "country",
                "user_pseudo_id": "pseudo-viewer-x"},
    ).json()["items"][0]
    assert item["country"] == "PL"
    assert item["country_name_en"] == "Poland"
    assert item["country_name_ru"] == "Польша"


def test_community_thumb_rejects_path_traversal(client, isolated_community):
    # Starlette decodes `%2F` to `/` before matching, so a traversal
    # attempt either hits the explicit-guard 400 (if URL-decode keeps
    # `..` in the filename slot) OR misses the route entirely (404
    # from the router). Both outcomes are safe; we just assert it
    # never reaches the read.
    r = client.get("/api/community/thumb/..%2Fetc%2Fpasswd")
    assert r.status_code in (400, 404)
    r2 = client.get("/api/community/thumb/..%5Cetc%5Cpasswd")  # backslash variant
    assert r2.status_code in (400, 404)


def test_community_thumb_404_for_missing(client, isolated_community):
    r = client.get("/api/community/thumb/does-not-exist.jpg")
    assert r.status_code == 404


# ── /api/metrics surfaces the corpus ────────────────────────────────


def test_metrics_surfaces_community_block(client, isolated_community):
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanmetric01",
            user_pseudo_id="pseudo-metric",
            region="Madrid", country="ES",
            disease="healthy",
        ),
    )
    m = client.get("/api/metrics").json()
    assert "community" in m
    assert m["community"]["total"] >= 1
    assert m["community"]["regions"] >= 1


def test_country_groups_with_region_breakdown(client, isolated_community):
    """The corpus stores country + city (in the ``region`` column). A
    country-scoped query returns every city and surfaces a top-cities
    breakdown in the summary."""
    db_path, _ = isolated_community
    writes = (
        dict(scan_id="scangb-london-1", country="GB", region="London"),
        dict(scan_id="scangb-london-2", country="GB", region="London"),
        dict(scan_id="scangb-mancunian1", country="GB", region="Manchester"),
        dict(scan_id="scangb-bristol001", country="GB", region="Bristol"),
    )
    for w in writes:
        r = client.post(
            "/api/community/scan",
            data=_form(
                scan_id=w["scan_id"],
                user_pseudo_id="pseudo-gb-mixed",
                country=w["country"],
                region=w["region"],
                disease="healthy",
            ),
        )
        assert r.status_code == 200, r.text

    # Persisted country is "GB"; the region column carries the city.
    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        rows = conn.execute(
            "SELECT country, region FROM community_scans ORDER BY id"
        ).fetchall()
    assert {row[0] for row in rows} == {"GB"}
    assert {row[1] for row in rows} == {"London", "Manchester", "Bristol"}

    # Country-scoped query returns everything + a top-cities breakdown.
    r_country = client.get(
        "/api/community/nearby", params={"country": "GB", "scope": "country"},
    ).json()
    assert r_country["summary"]["total"] == 4
    assert r_country["summary"]["country"] == "GB"
    top_region_names = {r["region"] for r in r_country["summary"]["top_regions"]}
    assert top_region_names >= {"London", "Manchester", "Bristol"}


# ── Regression: thumbnail magic-byte validation ─────────────────────


def test_community_scan_rejects_non_image_thumbnail_bytes(client, isolated_community):
    """Bytes that aren't a real JPEG/PNG/WebP are refused even when the
    multipart Content-Type claims image/png — the stored thumbnail is later
    re-served as an image and must never carry arbitrary attacker bytes."""
    _, thumb_dir = isolated_community
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanbadbytes1",
            user_pseudo_id="pseudobadbyte",
            region="London", country="GB",
            disease="powdery_mildew",
        ),
        files={"image": ("scan.png", b"<html>not an image</html>", "image/png")},
    )
    assert r.status_code == 400
    assert not thumb_dir.exists() or not any(thumb_dir.iterdir())


# ── Regression: duplicate upload leaves no orphan thumbnail ─────────


def test_duplicate_upload_does_not_orphan_thumbnail(client, isolated_community):
    _, thumb_dir = isolated_community
    form = _form(
        scan_id="scandup00001",
        user_pseudo_id="pseudodup001",
        region="Berlin", country="DE",
        disease="leaf_spot",
    )
    r1 = client.post(
        "/api/community/scan", data=form,
        files={"image": ("a.png", _png_bytes(), "image/png")},
    )
    assert r1.status_code == 200
    files_after_first = sorted(p.name for p in thumb_dir.iterdir())
    # Second upload of the same scan_id (different bytes) dedups on the
    # row AND must not write a new, never-referenced thumbnail file.
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(200, 30, 30)).save(buf, format="PNG")
    r2 = client.post(
        "/api/community/scan", data=form,
        files={"image": ("b.png", buf.getvalue(), "image/png")},
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == r1.json()["id"]
    assert sorted(p.name for p in thumb_dir.iterdir()) == files_after_first


# ── Regression: prune sweeps companion tables ───────────────────────


def test_prune_sweeps_orphaned_companion_rows(isolated_community):
    import time as _time

    import community as comm

    comm.init_db()
    old = int(_time.time()) - 200 * 86_400
    db_path, _ = isolated_community
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO community_scans (scan_id, user_pseudo_id, country, "
            "region, disease, created_at) VALUES ('scanold00001','u0000000001',"
            "'GB','','leaf_spot',?)", (old,),
        )
        conn.execute(
            "INSERT INTO community_scan_views (scan_id, viewer_pseudo_id, "
            "created_at) VALUES ('scanold00001','u0000000002',?)", (old,),
        )
        conn.commit()
    finally:
        conn.close()

    assert community_deletion.prune_older_than(90) == 1
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM community_scan_views"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_prune_transaction_removes_fresh_companion_that_won_write_race(
    isolated_community,
):
    """A writer that commits first is swept with the expiring target scan."""
    import time as _time

    import community as comm

    comm.init_db()
    old = int(_time.time()) - 200 * 86_400
    fresh = int(_time.time())
    db_path, _ = isolated_community
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute(
            "INSERT INTO community_scans (scan_id, user_pseudo_id, country, "
            "region, disease, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("prune-raced", "owner", "GB", "", "leaf_spot", old),
        )
        connection.execute(
            "INSERT INTO community_scan_views "
            "(scan_id, viewer_pseudo_id, created_at) VALUES (?, ?, ?)",
            ("prune-raced", "viewer", fresh),
        )

    assert community_deletion.prune_older_than(90) == 1
    with closing(sqlite3.connect(db_path)) as connection, connection:
        for table in (
            "community_scans",
            "community_scan_views",
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE scan_id = ?",
                ("prune-raced",),
            ).fetchone()[0] == 0


def test_prune_retains_thumbnail_row_when_file_deletion_fails(
    isolated_community,
    monkeypatch,
):
    import time as _time
    from pathlib import Path

    import community as comm

    comm.init_db()
    old = int(_time.time()) - 200 * 86_400
    db_path, thumb_dir = isolated_community
    thumbnail = thumb_dir / "retained-on-unlink-error.jpg"
    thumbnail.write_bytes(b"private-thumbnail")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO community_scans (scan_id, user_pseudo_id, country, "
            "region, disease, thumb_path, created_at) VALUES "
            "('scanunlink001','u0000000001','GB','','leaf_spot',?,?)",
            (thumbnail.name, old),
        )
        conn.commit()
    finally:
        conn.close()

    original_unlink = Path.unlink

    def fail_target(path: Path, *args, **kwargs):
        if path == thumbnail:
            raise OSError("synthetic unlink failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_target)

    assert community_deletion.prune_older_than(90) == 0
    assert thumbnail.exists()
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT thumb_path FROM community_scans WHERE scan_id = 'scanunlink001'"
        ).fetchone() == (thumbnail.name,)
    finally:
        conn.close()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    assert community_deletion.prune_older_than(90) == 1
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM community_scans WHERE scan_id = 'scanunlink001'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


# ── Public scan tokens (feedback-gate leak closed) ──────────────────


def _share(client, scan_id: str, species: str):
    """Share one FR scan, identified in the feed by its species.

    The feed deliberately returns an opaque per-response ``scan_id`` rather
    than the stored one, so ranking assertions key off a field that survives
    the round trip.
    """
    response = client.post(
        "/api/community/scan",
        data=_form(
            scan_id=scan_id,
            user_pseudo_id=f"pseudo-owner-{scan_id}",
            region="Lyon",
            country="FR",
            disease="leaf_spot",
            species=species,
        ),
    )
    assert response.status_code == 200, response.text


def _recommended_species(client):
    feed = client.get(
        "/api/community/nearby",
        params={
            "country": "FR",
            "scope": "country",
            "ranking": "recommended",
            "user_pseudo_id": "pseudo-reader",
            "limit": 10,
        },
    )
    assert feed.status_code == 200, feed.text
    return [row["species"] for row in feed.json()["items"]]


def test_recommended_feed_ranks_recency_above_a_stale_post_s_engagement(
    client, isolated_community,
):
    """Freshness is the ranking's baseline, not a tiebreak.

    Under the pre-``_RANK_*`` weights freshness ticked once per six hours
    while engagement and the row-id jitter kept their own, much larger scale
    (the jitter alone was worth ~25 days), so a quiet week-old post routinely
    outranked everything posted today and the feed read as shuffled rather
    than dated. Every term is now denominated in hours of freshness, so five
    days of age outweighs two days' worth of views.
    """
    db_path, _ = isolated_community
    _share(client, "freshpost0001", "Fresh nothing")
    _share(client, "staleviewed01", "Stale viewed")
    _share(client, "stalequiet001", "Stale quiet")

    now = 1_800_000_000
    day = 86_400
    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        conn.executemany(
            "INSERT INTO community_scan_views "
            "(scan_id, viewer_pseudo_id, created_at) VALUES (?, ?, ?)",
            [("staleviewed01", f"view-{i}", now) for i in range(48)],
        )
        for scan_id, created_at in (
            ("freshpost0001", now),
            ("staleviewed01", now - 5 * day),
            ("stalequiet001", now - 10 * day),
        ):
            changed = conn.execute(
                "UPDATE community_scans SET created_at = ? WHERE scan_id = ?",
                (created_at, scan_id),
            ).rowcount
            assert changed == 1, scan_id

    assert _recommended_species(client) == ["Fresh nothing", "Stale viewed", "Stale quiet"]


def test_recommended_feed_caps_total_engagement_below_material_freshness(
    client, isolated_community,
):
    """No amount of ordinary engagement can bury a materially fresh post.

    The view cap is not sufficient on its own: at the current weight, 500
    views are worth almost 21 days of freshness. Pin the aggregate promotion
    cap by saturating the input; even then, a brand-new post must rank ahead
    of one from 30 days ago.
    """
    db_path, _ = isolated_community
    _share(client, "freshmax0001", "Fresh post")
    _share(client, "staleengaged1", "Max-engaged stale post")

    now = 1_800_000_000
    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        conn.execute(
            "UPDATE community_scans SET created_at = ? WHERE scan_id = ?",
            (now, "freshmax0001"),
        )
        conn.execute(
            "UPDATE community_scans SET created_at = ? WHERE scan_id = ?",
            (now - 30 * 86_400, "staleengaged1"),
        )
        conn.executemany(
            "INSERT INTO community_scan_views "
            "(scan_id, viewer_pseudo_id, created_at) VALUES (?, ?, ?)",
            [("staleengaged1", f"view-{i}", now) for i in range(500)],
        )

    assert _recommended_species(client) == ["Fresh post", "Max-engaged stale post"]


def test_recommended_feed_is_deterministic_and_composite_cursor_has_no_overlap(
    client, isolated_community,
):
    db_path, _ = isolated_community
    scan_ids = [f"recommend{i:04d}" for i in range(5)]
    for index, scan_id in enumerate(scan_ids):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=scan_id,
                user_pseudo_id=f"pseudo-author-{index}",
                region="Lyon",
                country="FR",
                disease="leaf_spot",
            ),
        )
    # Force the timestamp tie that a created_at-only cursor cannot page.
    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        conn.execute("UPDATE community_scans SET created_at = ?", (1_800_000_000,))

    params = {
        "country": "FR",
        "scope": "country",
        "ranking": "recommended",
        "user_pseudo_id": "pseudo-reader",
        "limit": 2,
    }
    first = client.get("/api/community/nearby", params=params)
    assert first.status_code == 200, first.text
    page1 = first.json()
    assert page1["ranking"] == "recommended"
    assert page1["next_cursor"].startswith("r1.")

    # Same snapshot + request produces the same order.
    repeat = client.get("/api/community/nearby", params=params).json()
    assert [row["scan_id"] for row in repeat["items"]] == [
        row["scan_id"] for row in page1["items"]
    ]

    seen: list[str] = []
    page = page1
    while True:
        seen.extend(row["scan_id"] for row in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
        page = client.get(
            "/api/community/nearby",
            params={**params, "cursor": cursor},
        ).json()
    assert len(seen) == 5
    assert len(set(seen)) == 5

    assert client.get(
        "/api/community/nearby",
        params={**params, "cursor": "not-a-cursor"},
    ).status_code == 422
    tampered = page1["next_cursor"][:-1] + (
        "0" if page1["next_cursor"][-1] != "0" else "1"
    )
    assert client.get(
        "/api/community/nearby",
        params={**params, "cursor": tampered},
    ).status_code == 422


def test_recent_composite_cursor_keeps_same_second_rows(client, isolated_community):
    db_path, _ = isolated_community
    for index in range(5):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=f"recenttie{index:03d}",
                user_pseudo_id=f"recent-owner-{index}",
                country="FR",
                region="Lyon",
                disease="healthy",
            ),
        )
    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        conn.execute("UPDATE community_scans SET created_at = ?", (1_800_000_000,))

    params = {
        "country": "FR",
        "scope": "country",
        "ranking": "recent",
        "user_pseudo_id": "recent-reader",
        "limit": 2,
    }
    seen: list[str] = []
    response = client.get("/api/community/nearby", params=params)
    assert response.status_code == 200, response.text
    page = response.json()
    while True:
        seen.extend(row["scan_id"] for row in page["items"])
        if page["next_cursor"] is None:
            break
        response = client.get(
            "/api/community/nearby",
            params={**params, "cursor": page["next_cursor"]},
        )
        assert response.status_code == 200, response.text
        page = response.json()
    assert len(seen) == 5
    assert len(set(seen)) == 5


def test_batch_impression_views_counted_once(client, isolated_community):
    for sid in ("viewbatch0001", "viewbatch0002"):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=sid, user_pseudo_id="pseudo-owner-v",
                region="Turin", country="IT", disease="leaf_spot",
            ),
        )
    first = client.post(
        "/api/community/view",
        json={
            "scan_ids": ["viewbatch0001", "viewbatch0002"],
            "user_pseudo_id": "pseudo-scroller-1",
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["counted_n"] == 2

    # Same two ids again — both are already-seen, so nothing new is counted.
    second = client.post(
        "/api/community/view",
        json={
            "scan_ids": ["viewbatch0001", "viewbatch0002"],
            "user_pseudo_id": "pseudo-scroller-1",
        },
    )
    assert second.json()["counted_n"] == 0

    assert community_views.view_counts(["viewbatch0001"]) == {"viewbatch0001": 1}


def test_legacy_rows_backfilled_with_public_id_and_thumb_rename(community_store, tmp_path):
    """Rows + thumbnails created before the public_id column existed get
    migrated on init: token assigned, thumbnail renamed off the real id."""
    import time as _time

    comm = community_store

    # Simulate a legacy DB: table without public_id, plus a legacy thumb.
    (tmp_path / "thumbs").mkdir()
    (tmp_path / "thumbs" / "legacyscan01_abc.jpg").write_bytes(b"\xff\xd8\xffthumb")
    conn = sqlite3.connect(tmp_path / "community.db")
    conn.executescript(
        "CREATE TABLE community_scans ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id TEXT NOT NULL,"
        " user_pseudo_id TEXT NOT NULL, country TEXT, region TEXT NOT NULL,"
        " disease TEXT NOT NULL, confidence REAL, species TEXT,"
        " species_lower TEXT, species_score REAL, thumb_path TEXT,"
        " locale TEXT, client_ip_hash TEXT,"
        " wants_opinion INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)"
    )
    conn.execute(
        "INSERT INTO community_scans (scan_id, user_pseudo_id, country, region,"
        " disease, thumb_path, created_at) VALUES (?,?,?,?,?,?,?)",
        ("legacyscan01", "pseudo-legacy", "FR", "Paris", "leaf_spot",
         "legacyscan01_abc.jpg", int(_time.time())),
    )
    conn.commit()
    conn.close()

    comm.init_db()
    token = comm.public_scan_token("legacyscan01")
    assert comm.resolve_scan_token(token) == "legacyscan01"
    rows = community_feeds.nearby_scans(country="FR")
    assert rows[0]["public_id"] == token
    assert rows[0]["thumb_path"] == f"{token}_abc.jpg"
    assert (tmp_path / "thumbs" / f"{token}_abc.jpg").exists()
    assert not (tmp_path / "thumbs" / "legacyscan01_abc.jpg").exists()


def test_nearby_cursor_rejected_with_disease_or_species(client, isolated_community):
    """A disease/species-scoped call is several concatenated passes with no
    global created_at order, so a keyset cursor is refused rather than paging
    wrongly (duplicated rows on the next page, and a whole pass silently
    dropped)."""
    base = {
        "country": "FR", "scope": "country", "limit": 2, "user_pseudo_id": "x",
    }
    for extra in ({"disease": "leaf_spot"}, {"species": "Ficus elastica"}):
        keyed = client.get(
            "/api/community/nearby",
            params={**base, "cursor": "r1.opaque.signature", **extra},
        )
        assert keyed.status_code == 422, keyed.text
        assert keyed.json()["detail"] == "cursor cannot be combined with disease or species."

    # The filters are still fine on their own — only the cursor is refused.
    r = client.get(
        "/api/community/nearby",
        params={**base, "disease": "leaf_spot"},
    )
    assert r.status_code == 200, r.text


def test_scan_id_index_exists_and_is_not_unique(isolated_community):
    """``community_scans.scan_id`` is the hot lookup key (token resolution,
    views, ownership) and must be indexed — but NOT uniquely: legacy
    rows may repeat a scan_id, and a UNIQUE index would fail to build and
    take boot down with it."""
    db_path, _ = isolated_community
    import community as comm
    comm.init_db()
    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        idx = {
            row[1]: bool(row[2])
            for row in conn.execute("PRAGMA index_list(community_scans)").fetchall()
        }
    assert "ix_community_scan_id" in idx
    assert idx["ix_community_scan_id"] is False


def test_legacy_backfill_pseudo_columns_are_indexed(isolated_community):
    db_path, _ = isolated_community
    import community as comm
    comm.init_db()
    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        view_indexes = {
            row[1] for row in conn.execute(
                "PRAGMA index_list(community_scan_views)",
            ).fetchall()
        }
    assert "ix_views_viewer_pseudo" in view_indexes


def test_batch_view_mixes_token_forms_and_drops_self_views(client, isolated_community):
    """One batch can carry public tokens and real scan_ids for the same rows;
    both resolve to one view each, unknown ids are ignored, and the owner's
    own impressions never count."""
    import community as comm

    for sid in ("viewmix000001", "viewmix000002"):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=sid, user_pseudo_id="pseudo-owner-m",
                region="Turin", country="IT", disease="leaf_spot",
            ),
        )
    token = comm.public_scan_token("viewmix000001")
    r = client.post(
        "/api/community/view",
        json={
            "scan_ids": [token, "viewmix000001", "viewmix000002", "neverssharedx"],
            "user_pseudo_id": "pseudo-scroller-m",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["counted_n"] == 2

    own = client.post(
        "/api/community/view",
        json={
            "scan_ids": ["viewmix000001", "viewmix000002"],
            "user_pseudo_id": "pseudo-owner-m",
        },
    )
    assert own.json()["counted_n"] == 0


def test_feed_token_round_trip_for_views(client, isolated_community):
    """Viewers only ever see the public token; a view posted with it must
    resolve to the real scan_id so the owner (who uses the real id) is
    counted against the right scan."""
    import community as comm

    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="realscanid01",
            user_pseudo_id="pseudo-owner-1",
            region="Paris", country="FR",
            disease="leaf_spot",
        ),
    )
    feed = client.get(
        "/api/community/nearby",
        params={"country": "FR", "user_pseudo_id": "pseudo-viewer-1"},
    ).json()["items"]
    token = feed[0]["scan_id"]
    assert token == comm.public_scan_token("realscanid01")
    assert token != "realscanid01"
    # Thumb filenames must not leak the real id either.
    assert "realscanid01" not in (feed[0].get("thumb_path") or "")
    assert "opinion_count" not in feed[0]
    assert "wants_opinion" not in feed[0]

    r = client.post(
        "/api/community/view",
        json={"scan_id": token, "user_pseudo_id": "pseudo-viewer-1"},
    )
    assert r.json()["counted"] is True
    assert community_views.view_counts(["realscanid01"]) == {"realscanid01": 1}
