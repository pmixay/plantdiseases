"""Community scan publication: thumbnails, scan rows and analysis tokens.

A published scan is bound to a server-issued analysis token minted when the
server analysed the photo; ``record_analysis_scan`` checks the token and the
exact image before writing, ``record_scan`` is the legacy client-supplied
write. Thumbnails are re-encoded here before they reach
``community.COMMUNITY_THUMB_DIR``. The connection, write lock, identity
helpers and schema stay in ``community.py``."""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import math
import os
import sqlite3
import time

from PIL import Image, ImageOps, UnidentifiedImageError

try:
    from . import community
    from .countries import to_iso_alpha2
except ImportError:
    import community
    from countries import to_iso_alpha2

logger = logging.getLogger(__name__)


# Cap thumbnails on disk so a runaway client can't fill the volume.
COMMUNITY_MAX_THUMB_BYTES = 350 * 1024  # 350 KB — generous for a 512px JPEG


class AnalysisTokenUnknownError(ValueError):
    """The publication capability was not issued here or has expired."""


class AnalysisTokenConflictError(ValueError):
    """The capability or scan id is already bound to another publication."""


class AnalysisImageMismatchError(ValueError):
    """The community image is not the exact upload that was analysed."""


def scan_exists(scan_id: str) -> bool:
    """True when a community row exists for ``scan_id`` (already resolved)."""
    sid = (scan_id or "").strip()
    if not sid:
        return False
    community.init_db()
    with community._connect() as conn:
        return conn.execute(
            "SELECT 1 FROM community_scans WHERE scan_id = ? "
            "AND (account_actor_id IS NULL OR account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors)) LIMIT 1", (sid,),
        ).fetchone() is not None


def record_analysis_token(
    token: str,
    *,
    disease: str,
    confidence: float | None,
    species: str | None,
    species_score: float | None,
    image_sha256: str,
    ttl_days: int = 30,
) -> None:
    """Persist immutable server-derived claims for a publication capability."""
    value = (token or "").strip()
    if not value or len(value) > 128:
        raise ValueError("invalid analysis token")
    disease_value = (disease or "").strip()
    if not disease_value or len(disease_value) > 256:
        raise ValueError("invalid analysis disease")
    confidence_value = float(confidence) if confidence is not None else None
    species_value = (species or "").strip() or None
    species_score_value = float(species_score) if species_score is not None else None
    image_sha256_value = (image_sha256 or "").strip().lower()
    if confidence_value is not None and (
        not math.isfinite(confidence_value) or not 0.0 <= confidence_value <= 1.0
    ):
        raise ValueError("invalid analysis confidence")
    if species_value is not None and len(species_value) > 256:
        raise ValueError("invalid analysis species")
    if species_score_value is not None and (
        not math.isfinite(species_score_value) or not 0.0 <= species_score_value <= 1.0
    ):
        raise ValueError("invalid analysis species score")
    if len(image_sha256_value) != 64:
        raise ValueError("invalid analysis image digest")
    try:
        bytes.fromhex(image_sha256_value)
    except ValueError as exc:
        raise ValueError("invalid analysis image digest") from exc
    # Salt the exact-byte digest with the random capability so identical
    # photos do not create a reusable, cross-token fingerprint in SQLite.
    image_binding = hashlib.sha256(
        value.encode("utf-8") + b"\0" + bytes.fromhex(image_sha256_value)
    ).hexdigest()
    community.init_db()
    expires_at = int(time.time()) + max(1, min(int(ttl_days), 90)) * 86_400
    with community._write_lock, community._connect() as connection:
        connection.execute(
            "INSERT INTO community_analysis_tokens ("
            "token, expires_at, disease, confidence, species, species_score, image_binding"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                value,
                expires_at,
                disease_value,
                confidence_value,
                species_value,
                species_score_value,
                image_binding,
            ),
        )


def is_analysis_token_issued(token: str) -> bool:
    """Whether a token is current and eligible for community publication."""
    value = (token or "").strip()
    if not value:
        return False
    community.init_db()
    with community._connect() as connection:
        return connection.execute(
            "SELECT 1 FROM community_analysis_tokens "
            "WHERE token = ? AND expires_at > ? AND disease IS NOT NULL "
            "AND image_binding IS NOT NULL LIMIT 1",
            (value, int(time.time())),
        ).fetchone() is not None


def _save_thumbnail(scan_id: str, image_bytes: bytes) -> str | None:
    """Decode and persist a fresh, metadata-free 512px RGB JPEG."""
    if not image_bytes:
        return None
    if len(image_bytes) > COMMUNITY_MAX_THUMB_BYTES:
        logger.info("Community thumbnail rejected as oversized")
        return None
    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            width, height = opened.size
            if (
                width < 32
                or height < 32
                or width > 8_192
                or height > 8_192
                or width * height > 20_000_000
            ):
                return None
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError):
        return None
    if max(image.size) > 512:
        image.thumbnail((512, 512), Image.Resampling.LANCZOS)
    clean = Image.new("RGB", image.size)
    clean.paste(image)
    encoded = io.BytesIO()
    try:
        clean.save(
            encoded,
            format="JPEG",
            quality=85,
            optimize=True,
            progressive=False,
            subsampling=2,
        )
    except (OSError, ValueError):
        return None
    body = encoded.getvalue()
    if len(body) > COMMUNITY_MAX_THUMB_BYTES:
        return None
    # Hash-prefix the filename so two scans with the same scan_id (a
    # mis-behaved client) can't collide. Truncate to 24 hex chars —
    # plenty of collision resistance for this bounded corpus.
    # Named by the PUBLIC token, not the raw scan_id: the filename is
    # served to every feed reader via ``thumb_path`` and must not leak
    # the publication capability.
    digest = hashlib.sha256(body).hexdigest()[:24]
    filename = f"{community.public_scan_token(scan_id)}_{digest}.jpg"
    target = community.COMMUNITY_THUMB_DIR / filename
    temporary = community.COMMUNITY_THUMB_DIR / f".{filename}.tmp"
    try:
        community.COMMUNITY_THUMB_DIR.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(body)
        temporary.chmod(0o600)
        os.replace(temporary, target)
    except OSError:
        temporary.unlink(missing_ok=True)
        logger.warning("Failed to write community thumbnail")
        return None
    return filename


def record_scan(
    *,
    scan_id: str,
    user_pseudo_id: str | None = None,
    account_actor_id: str | None = None,
    country: str,
    region: str = "",
    city: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    disease: str,
    confidence: float | None = None,
    species: str | None = None,
    species_score: float | None = None,
    image_bytes: bytes | None = None,
    locale: str | None = None,
    nickname: str | None = None,
    author_id: str | None = None,
    client_ip: str | None = None,
) -> int:
    """Append a community-shared scan row. Returns the new row id.

    ``region``/``city`` are the display locality and ``lat``/``lon`` the
    rounded coarse coords — all client-supplied. ``nickname`` is the author's
    generated plant-vibe handle shown in the feed.
    """
    community.init_db()
    legacy_id, actor_id = community._identity(user_pseudo_id, account_actor_id)
    now = int(time.time())
    # The connection address may be used by the in-memory HTTP limiter, but it
    # is never persisted on account-era community rows.
    ip_hash = None
    # Canonicalize the country bucket to an ISO alpha-2 code at the write so
    # the corpus never splits one country across code / name / localized-name
    # representations (the HTTP layer already canonicalizes, but a direct
    # caller — tests, future code — gets the same guarantee).
    country = to_iso_alpha2(country)
    region_value = region or ""
    species_lower = species.lower() if species else None
    with community._write_lock, community._connect() as conn:
        if community._actor_is_deleted(conn, actor_id):
            raise ValueError("deleted account actor")
        # Dedup by scan_id. The community upload is fire-and-forget, so the
        # same scan can be offered twice; we never want two feed rows for one
        # scan. (There's no UNIQUE constraint on
        # scan_id for back-compat with legacy rows, so we guard here.)
        # The dedup check runs BEFORE the thumbnail write — writing first
        # leaked an orphan thumbnail file per duplicate upload that no
        # prune path ever swept.
        existing = conn.execute(
            "SELECT id FROM community_scans WHERE scan_id = ? LIMIT 1",
            (scan_id,),
        ).fetchone()
        if existing:
            if author_id:
                conn.execute(
                    "UPDATE community_scans SET author_id = COALESCE(author_id, ?) "
                    "WHERE id = ?",
                    (author_id, int(existing[0])),
                )
            return int(existing[0])
        thumb_path = _save_thumbnail(scan_id, image_bytes) if image_bytes else None
        cur = conn.execute(
            "INSERT INTO community_scans ("
            "  scan_id, user_pseudo_id, account_actor_id, country, region, city, lat, lon, "
            "  disease, confidence, "
            "  species, species_lower, species_score, thumb_path, locale, "
            "  nickname, author_id, client_ip_hash, public_id, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scan_id,
                legacy_id,
                actor_id,
                country,
                region_value,
                city,
                lat,
                lon,
                disease,
                confidence,
                species,
                species_lower,
                species_score,
                thumb_path,
                locale,
                (nickname or None),
                author_id,
                ip_hash,
                community.public_scan_token(scan_id),
                now,
            ),
        )
        return int(cur.lastrowid or 0)


def record_analysis_scan(
    *,
    scan_id: str,
    account_actor_id: str,
    country: str,
    region: str = "",
    city: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    image_bytes: bytes | None = None,
    locale: str | None = None,
    nickname: str | None = None,
    client_disease: str | None = None,
    client_confidence: float | None = None,
    client_species: str | None = None,
    client_species_score: float | None = None,
) -> int:
    """Publish one server analysis with its immutable stored verdict.

    The uploaded image must be byte-for-byte identical to the upload analysed
    when the capability was issued. Claiming the capability and inserting the
    scan share one SQLite transaction. A database failure therefore leaves the
    capability retryable; after success, only the same signed actor receives
    the existing row id.
    The ``client_*`` diagnosis fields remain accepted for wire/backward test
    compatibility but are deliberately ignored in favor of the stored claims.
    """
    del client_disease, client_confidence, client_species, client_species_score
    community.init_db()
    token = (scan_id or "").strip()
    if not token or len(token) > 128:
        raise AnalysisTokenUnknownError("unknown analysis token")
    if not image_bytes:
        raise AnalysisImageMismatchError("analysis image is required")
    image_binding = hashlib.sha256(
        token.encode("utf-8") + b"\0" + hashlib.sha256(image_bytes).digest()
    ).hexdigest()
    _legacy_id, actor_id = community._identity(None, account_actor_id)
    assert actor_id is not None
    now = int(time.time())
    country_value = to_iso_alpha2(country)
    region_value = region or ""
    thumb_path: str | None = None

    with community._write_lock, community._connect() as conn:
        try:
            # Serialize token claims and scan-id collision checks even when
            # multiple application threads/processes reach SQLite together.
            conn.execute("BEGIN IMMEDIATE")
            if community._actor_is_deleted(conn, actor_id):
                raise ValueError("deleted account actor")
            if actor_id is not None:
                owner_row = conn.execute(
                    "SELECT account_actor_id FROM community_scans "
                    "WHERE scan_id = ? LIMIT 1",
                    (scan_id,),
                ).fetchone()
                owner_actor = (
                    str(owner_row[0]) if owner_row and owner_row[0] else None
                )
                if owner_actor and owner_actor != actor_id:
                    blocked = conn.execute(
                        "SELECT 1 FROM community_actor_blocks WHERE "
                        "(blocker_actor_id = ? AND blocked_actor_id = ?) OR "
                        "(blocker_actor_id = ? AND blocked_actor_id = ?) LIMIT 1",
                        (actor_id, owner_actor, owner_actor, actor_id),
                    ).fetchone()
                    if blocked is not None:
                        raise ValueError("blocked_interaction")
            claim = conn.execute(
                "SELECT expires_at, disease, confidence, species, species_score, image_binding, "
                "claimed_actor_id FROM community_analysis_tokens WHERE token = ?",
                (token,),
            ).fetchone()
            if (
                claim is None
                or int(claim[0]) <= now
                or claim[1] is None
                or claim[5] is None
            ):
                raise AnalysisTokenUnknownError("unknown analysis token")
            if image_binding != str(claim[5]).lower():
                raise AnalysisImageMismatchError("analysis image does not match")

            existing_rows = conn.execute(
                "SELECT id, account_actor_id FROM community_scans "
                "WHERE scan_id = ? LIMIT 2",
                (token,),
            ).fetchall()
            claimed_actor = str(claim[6]) if claim[6] is not None else None
            if claimed_actor is not None:
                if (
                    claimed_actor != actor_id
                    or len(existing_rows) != 1
                    or existing_rows[0][1] != actor_id
                ):
                    raise AnalysisTokenConflictError("analysis token already claimed")
                conn.execute("COMMIT")
                return int(existing_rows[0][0])

            # An unclaimed token must not adopt an unrelated/pre-existing row,
            # even if that row happens to have the same actor id.
            if existing_rows:
                raise AnalysisTokenConflictError("scan id collision")

            disease = str(claim[1])
            confidence = float(claim[2]) if claim[2] is not None else None
            species = str(claim[3]) if claim[3] is not None else None
            species_score = float(claim[4]) if claim[4] is not None else None
            species_lower = species.lower() if species else None
            thumb_path = _save_thumbnail(token, image_bytes) if image_bytes else None
            cur = conn.execute(
                "INSERT INTO community_scans ("
                "scan_id, user_pseudo_id, account_actor_id, country, region, city, lat, lon, "
                "disease, confidence, species, species_lower, species_score, thumb_path, "
                "locale, nickname, client_ip_hash, public_id, created_at"
                ") VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    token,
                    actor_id,
                    country_value,
                    region_value,
                    city,
                    lat,
                    lon,
                    disease,
                    confidence,
                    species,
                    species_lower,
                    species_score,
                    thumb_path,
                    locale,
                    (nickname or None),
                    community.public_scan_token(token),
                    now,
                ),
            )
            claimed = conn.execute(
                "UPDATE community_analysis_tokens "
                "SET claimed_actor_id = ?, claimed_at = ? "
                "WHERE token = ? AND claimed_actor_id IS NULL",
                (actor_id, now, token),
            ).rowcount
            if claimed != 1:
                raise AnalysisTokenConflictError("analysis token already claimed")
            conn.execute("COMMIT")
            return int(cur.lastrowid or 0)
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            if thumb_path:
                with contextlib.suppress(OSError):
                    (community.COMMUNITY_THUMB_DIR / thumb_path).unlink(missing_ok=True)
            raise


def account_owner_of(scan_id: str) -> str | None:
    """Signed actor that owns a new account-era scan; legacy rows return None."""
    sid = (scan_id or "").strip()
    if not sid:
        return None
    community.init_db()
    with community._connect() as conn:
        row = conn.execute(
            "SELECT account_actor_id FROM community_scans WHERE scan_id = ? "
            "AND account_actor_id IS NOT NULL AND account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors) LIMIT 1",
            (sid,),
        ).fetchone()
    return str(row[0]) if row else None


def thumbnail_is_visible(filename: str) -> bool:
    """Whether a thumbnail belongs to a currently visible community row."""
    name = (filename or "").strip()
    if not name:
        return False
    community.init_db()
    with community._connect() as conn:
        return conn.execute(
            "SELECT 1 FROM community_scans WHERE thumb_path = ? "
            "AND (account_actor_id IS NULL OR account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors)) LIMIT 1",
            (name,),
        ).fetchone() is not None
