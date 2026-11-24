"""Community read paths that build feeds: species, recommended, nearby, map,
density grid and region summary, plus the recommendation cursor and the feed
column/ranking constants and the hidden-disease clause that
``community_popular`` shares.

Every function reads through ``community._connect()`` at call time, so a test
(or deployment) that redirects ``community.COMMUNITY_DB_PATH`` is followed. The
shared schema, locks, identity helpers and blocked-actor clauses stay in
``community.py``."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import math
import time

try:
    from . import community  # type: ignore[import-not-found]
    from .config import get_str  # type: ignore[import-not-found]
    from .countries import to_iso_alpha2  # type: ignore[import-not-found]
except ImportError:
    import community
    from config import get_str
    from countries import to_iso_alpha2


# Recommendation ranking weights. One point is ONE HOUR OF FRESHNESS, so every
# other term below reads directly as "worth N hours of being newer". That
# common unit is the point: freshness used to tick once per six hours while the
# other terms kept their own scale, which let engagement and the exploration
# jitter bury the date order the feed is supposed to lead with.
_RANK_FRESHNESS_BUCKET_S = 3600
_RANK_PER_VIEW = 1  # a distinct view, an hour
_RANK_VIEW_CAP = 500
# Deterministic per-row wobble so equally fresh, equally quiet posts don't
# always come back in insertion order. Bounded to under a day: exploration
# should reshuffle within a morning, not reorder a week.
_RANK_JITTER_MOD = 7
# Views and exploration share ONE promotion budget, so a heavily viewed
# month-old scan cannot outrank a brand-new one. Seven days leaves engagement
# meaningful within the current week while giving us a simple invariant: a
# scan at least a week newer always ranks first, even against maximum views.
_RANK_PROMOTION_CAP = 7 * 24

# Disease classes that should never surface in the community feed.
# ``not_a_plant`` rows are uninteresting to other users (and often
# faces / objects / random photos), so we silently hide them from
# both the carousel and the summary aggregates. Existing rows stay
# on disk for retraining; only the read paths filter them out.
_HIDDEN_COMMUNITY_DISEASES = ("not_a_plant",)

# Column list shared by every feed query so the positional ``_row_to_dict``
# reader can't drift between callers. Order is load-bearing — keep in sync.
_FEED_COLUMNS = (
    "scan_id, country, region, city, lat, lon, disease, confidence, "
    "species, species_score, thumb_path, locale, created_at"
)

# How many columns ``_FEED_COLUMNS`` projects. The ranked feed appends its
# cursor/score/engagement columns after these, so every slice below is taken
# relative to this rather than a literal that silently rots when a column is
# added.
_FEED_COLUMN_COUNT = len([c for c in _FEED_COLUMNS.split(",") if c.strip()])


def _qualified_feed_columns(alias: str) -> str:
    return ", ".join(f"{alias}.{name.strip()}" for name in _FEED_COLUMNS.split(","))


_RECOMMENDATION_CURSOR_PREFIX = "r1"
_RECOMMENDATION_CURSOR_SECRET = get_str(
    "COMMUNITY_CURSOR_SECRET",
    get_str("FEEDBACK_IP_PEPPER", "community-cursor-v1"),
).encode("utf-8")


def encode_recommendation_cursor(score: int, created_at: int, row_id: int) -> str:
    """Opaque, signed keyset cursor for the recommendation order."""
    payload = f"{int(score)}:{int(created_at)}:{int(row_id)}".encode("ascii")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(
        _RECOMMENDATION_CURSOR_SECRET,
        payload,
        hashlib.sha256,
    ).hexdigest()[:24]
    return f"{_RECOMMENDATION_CURSOR_PREFIX}.{encoded}.{signature}"


def decode_recommendation_cursor(value: str | None) -> tuple[int, int, int] | None:
    raw = (value or "").strip()
    if not raw:
        return None
    parts = raw.split(".")
    if len(parts) != 3 or parts[0] != _RECOMMENDATION_CURSOR_PREFIX:
        raise ValueError("invalid recommendation cursor")
    try:
        payload = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        expected = hmac.new(
            _RECOMMENDATION_CURSOR_SECRET,
            payload,
            hashlib.sha256,
        ).hexdigest()[:24]
        if not hmac.compare_digest(parts[2], expected):
            raise ValueError("invalid recommendation cursor")
        score, created_at, row_id = (int(part) for part in payload.decode("ascii").split(":"))
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise ValueError("invalid recommendation cursor") from exc
    if score < 0 or created_at < 0 or row_id <= 0:
        raise ValueError("invalid recommendation cursor")
    return score, created_at, row_id


# "Nearby" radius for the coords-based feed + popular scope (km). The client
# resolves coarse location; we group within this radius of it.
NEARBY_RADIUS_KM = 30.0


def _bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lon, max_lon) box ~``radius_km`` around a point.
    A cheap prefilter for the indexed lat/lon columns; callers refine to a true
    circle with :func:`_haversine_km` where exactness matters."""
    dlat = radius_km / 111.0
    coslat = math.cos(math.radians(lat))
    dlon = radius_km / (111.0 * coslat) if abs(coslat) > 1e-6 else 180.0
    return (lat - dlat, lat + dlat, lon - dlon, lon + dlon)


def _hidden_clause(table_alias: str = "") -> str:
    """SQL fragment + placeholders for the hidden-disease filter.

    Returns a string starting with " AND " so callers can append
    directly to existing WHERE conditions. Every caller filters an
    unaliased ``disease`` column; the aliased joins build their own
    disease filters instead.
    """
    if table_alias not in {"", "s"}:
        raise ValueError("invalid SQL table alias")
    prefix = f"{table_alias}." if table_alias else ""
    placeholders = ", ".join(["?"] * len(_HIDDEN_COMMUNITY_DISEASES))
    # SQL identifiers are allowlisted above; only the number of bound
    # placeholders is generated dynamically.
    return (
        f" AND {prefix}disease NOT IN ({placeholders})"  # nosec B608
        f" AND ({prefix}account_actor_id IS NULL OR {prefix}account_actor_id "
        "NOT IN (SELECT actor_id FROM community_deleted_actors))"
    )


def scans_by_species(
    scientific: str,
    *,
    limit: int = 12,
    exclude_user_pseudo_id: str | None = None,
    exclude_account_actor_id: str | None = None,
    viewer_account_actor_id: str | None = None,
    country: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = NEARBY_RADIUS_KM,
) -> list[dict]:
    """Community scans of one species (by scientific name), newest first —
    the species page's "scans of this species near you" strip.

    With the requester's coarse location (community members) the strip is
    nearest first: scans within ``radius_km``, then the rest of the country,
    then everywhere, so it stays filled; each row carries ``distance_km``.
    Without a location it spans all regions and shows no distance."""
    community.init_db()
    sci = (scientific or "").strip().lower()
    if not sci:
        return []
    limit = max(1, min(int(limit), 50))
    extra = ""
    extra_params: list = []
    if exclude_user_pseudo_id:
        extra += " AND user_pseudo_id != ?"
        extra_params.append(exclude_user_pseudo_id)
    if exclude_account_actor_id:
        extra += " AND (account_actor_id IS NULL OR account_actor_id != ?)"
        extra_params.append(exclude_account_actor_id)
    blocked_clause, blocked_params = community._blocked_actor_clause(
        viewer_account_actor_id
    )
    extra += blocked_clause
    extra_params.extend(blocked_params)
    if _HIDDEN_COMMUNITY_DISEASES:
        extra += _hidden_clause()
        extra_params.extend(_HIDDEN_COMMUNITY_DISEASES)

    has_coords = lat is not None and lon is not None
    # Geo passes, nearest first; the last one has no geo predicate at all.
    passes: list[tuple[str, list, bool]] = []
    if has_coords:
        passes.append((
            " AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
            list(_bbox(lat, lon, radius_km)), True,
        ))
    if country:
        passes.append((" AND country = ?", [country], False))
    passes.append(("", [], False))

    out: list[dict] = []
    seen: set[str] = set()
    with community._connect() as conn:
        for geo_clause, geo_params, refine in passes:
            if len(out) >= limit:
                break
            # Over-fetch: the radius pass trims a box to a circle and later
            # passes repeat rows an earlier pass already took.
            take = limit * 3 if refine else limit + len(out)
            # _FEED_COLUMNS and all clauses are fixed server-owned SQL.
            rows = conn.execute(
                f"SELECT {_FEED_COLUMNS} "  # nosec B608
                "FROM community_scans WHERE species_lower = ?"
                + geo_clause
                + extra
                + " ORDER BY created_at DESC LIMIT ?",
                (sci, *geo_params, *extra_params, take),
            ).fetchall()
            for raw in rows:
                row = _row_to_dict(raw)
                if refine and (
                    row["lat"] is None or row["lon"] is None
                    or community._haversine_km(lat, lon, row["lat"], row["lon"]) > radius_km
                ):
                    continue
                if row["scan_id"] in seen:
                    continue
                seen.add(row["scan_id"])
                out.append(row)
    out = out[:limit]
    # A viewer gets an approximate distance, never another user's coords.
    for row in out:
        rlat, rlon = row.pop("lat", None), row.pop("lon", None)
        row["distance_km"] = (
            round(community._haversine_km(lat, lon, rlat, rlon), 1)
            if has_coords and rlat is not None and rlon is not None
            else None
        )
    return out


def recommended_scans(
    *,
    country: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = NEARBY_RADIUS_KM,
    limit: int = 20,
    exclude_user_pseudo_id: str | None = None,
    exclude_account_actor_id: str | None = None,
    viewer_account_actor_id: str | None = None,
    scope: str = "nearby",
    cursor: tuple[int, int, int] | None = None,
) -> list[dict]:
    """Ranked Community feed with composite keyset pagination.

    Ranking is deterministic for a database snapshot and deliberately easy to
    explain, because every term is denominated in hours of freshness (see the
    ``_RANK_*`` weights): recency is the baseline, and engagement (distinct
    views) plus the deterministic exploration term share a seven-day
    promotion cap. Thus an ordinary scan at least a week newer always wins,
    even against maximum engagement. The cursor carries the complete ``(score,
    created_at, row_id)`` order key, so tied timestamps never repeat or
    disappear between pages.
    """
    community.init_db()
    country_norm = to_iso_alpha2(country) if country else None
    scope_norm = (scope or "nearby").strip().lower()
    if scope_norm not in {"nearby", "country", "worldwide"}:
        scope_norm = "nearby"
    limit = max(1, min(int(limit), 100))
    has_coords = lat is not None and lon is not None

    where = ["1=1"]
    params: list = []
    if scope_norm == "nearby" and has_coords:
        min_lat, max_lat, min_lon, max_lon = _bbox(float(lat), float(lon), radius_km)
        where.append("s.lat BETWEEN ? AND ? AND s.lon BETWEEN ? AND ?")
        params.extend((min_lat, max_lat, min_lon, max_lon))
        where.append("community_distance_km(?, ?, s.lat, s.lon) <= ?")
        params.extend((float(lat), float(lon), float(radius_km)))
    elif scope_norm in {"nearby", "country"} and country_norm:
        where.append("s.country = ?")
        params.append(country_norm)
    elif scope_norm != "worldwide":
        return []

    viewer = (exclude_user_pseudo_id or "").strip()
    if viewer:
        where.append("s.user_pseudo_id != ?")
        params.append(viewer)
    if exclude_account_actor_id:
        where.append("(s.account_actor_id IS NULL OR s.account_actor_id != ?)")
        params.append(exclude_account_actor_id)
    # Same mutual-block and deleted-actor predicates the recent feed applies —
    # the ranked ordering must not become a way around either.
    blocked_clause, blocked_params = community._blocked_actor_clause(
        viewer_account_actor_id, actor_column="s.account_actor_id"
    )
    if blocked_clause:
        where.append(blocked_clause.removeprefix(" AND "))
        params.extend(blocked_params)
    where.append(
        "(s.account_actor_id IS NULL OR s.account_actor_id NOT IN "
        "(SELECT actor_id FROM community_deleted_actors))"
    )
    if _HIDDEN_COMMUNITY_DISEASES:
        where.append(
            "s.disease NOT IN ("
            + ", ".join("?" for _ in _HIDDEN_COMMUNITY_DISEASES)
            + ")"
        )
        params.extend(_HIDDEN_COMMUNITY_DISEASES)

    promotion_sql = (
        "MIN("
        f"MIN(COALESCE(vc.n, 0), {_RANK_VIEW_CAP}) * {_RANK_PER_VIEW} "
        f"+ ((s.id * 37) % {_RANK_JITTER_MOD}), "
        f"{_RANK_PROMOTION_CAP})"
    )
    score_sql = (
        f"CAST(s.created_at / {_RANK_FRESHNESS_BUCKET_S} AS INTEGER) "
        f"+ {promotion_sql}"
    )
    cursor_sql = ""
    cursor_params: list[int] = []
    if cursor is not None:
        score, created_at, row_id = cursor
        cursor_sql = (
            "WHERE recommendation_score < ? "
            "OR (recommendation_score = ? AND created_at < ?) "
            "OR (recommendation_score = ? AND created_at = ? AND row_id < ?)"
        )
        cursor_params.extend((score, score, created_at, score, created_at, row_id))

    sql = f"""
        WITH view_counts_cte AS (
            SELECT scan_id, COUNT(DISTINCT CASE
                WHEN viewer_author_id IS NOT NULL THEN 'a:' || viewer_author_id
                ELSE 'p:' || viewer_pseudo_id END) AS n
            FROM community_scan_views GROUP BY scan_id
        ), ranked AS (
            SELECT {_qualified_feed_columns('s')},
                   s.id AS row_id,
                   {score_sql} AS recommendation_score,
                   COALESCE(vc.n, 0) AS view_count
            FROM community_scans s
            LEFT JOIN view_counts_cte vc ON vc.scan_id = s.scan_id
            WHERE {' AND '.join(where)}
        )
        SELECT * FROM ranked
        {cursor_sql}
        ORDER BY recommendation_score DESC, created_at DESC, row_id DESC
        LIMIT ?
    """
    with community._connect() as conn:
        raw_rows = conn.execute(
            sql,
            (*params, *cursor_params, limit),
        ).fetchall()

    rows: list[dict] = []
    for raw in raw_rows:
        n = _FEED_COLUMN_COUNT
        row = _row_to_dict(raw[:n])
        row_id = int(raw[n])
        score = int(raw[n + 1])
        row["view_count"] = int(raw[n + 2])
        row["recommendation_score"] = score
        row["_cursor_score"] = score
        row["_cursor_row_id"] = row_id
        rlat, rlon = row.get("lat"), row.get("lon")
        row["distance_km"] = (
            round(community._haversine_km(float(lat), float(lon), rlat, rlon), 1)
            if has_coords and rlat is not None and rlon is not None
            else None
        )
        row.pop("lat", None)
        row.pop("lon", None)
        rows.append(row)
    return rows


def nearby_scans(
    *,
    country: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = NEARBY_RADIUS_KM,
    disease: str | None = None,
    species: str | None = None,
    limit: int = 20,
    exclude_user_pseudo_id: str | None = None,
    exclude_account_actor_id: str | None = None,
    viewer_account_actor_id: str | None = None,
    scope: str = "nearby",
    cursor: tuple[int, int] | None = None,
) -> list[dict]:
    """Most recent shared scans for one scope.

    ``scope="nearby"`` (default) selects scans within ``radius_km`` of
    ``lat``/``lon`` (the requester's coarse location); ``scope="country"``
    selects the whole ``country``; ``scope="worldwide"`` drops the geo
    predicate entirely (the whole corpus, most recent first — the requester's
    location is irrelevant). A *disease-scoped* call (the result-screen
    "similar scans" carousel) widens nearby→country to stay filled but never
    crosses to a different disease; the disease-agnostic feed stays strictly
    in its scope so the Nearby/Country/Worldwide toggle actually changes what's
    shown.

    ``cursor`` is a keyset-pagination cursor — the previous page's last
    ``(created_at, id)`` pair, past which only OLDER rows are returned.
    ``None`` (default) fetches the newest page.

    A cursor is SINGLE-PASS ONLY — it must not be combined with ``disease`` or
    ``species``. Those widen the query into several independently ordered
    passes concatenated together, so the last row of a page is not the page's
    global minimum: paging it would repeat rows across pages and drop a later
    pass's rows entirely. The caller (``/api/community/nearby``) rejects the
    combination with a 422; a proper cursor for it would need one UNION ALL
    with a single global ORDER BY, or a per-pass watermark token.
    """
    community.init_db()
    if country:
        country = to_iso_alpha2(country)
    limit = max(1, min(int(limit), 100))
    has_coords = lat is not None and lon is not None
    scope = (scope or "nearby").strip().lower()
    if scope not in ("nearby", "country", "worldwide"):
        scope = "nearby"
    nearby = scope == "nearby"

    with community._connect() as conn:
        common = ""
        cparams: list = []
        if exclude_user_pseudo_id:
            common += " AND user_pseudo_id != ?"
            cparams.append(exclude_user_pseudo_id)
        if exclude_account_actor_id:
            common += " AND (account_actor_id IS NULL OR account_actor_id != ?)"
            cparams.append(exclude_account_actor_id)
        blocked_clause, blocked_params = community._blocked_actor_clause(
            viewer_account_actor_id
        )
        common += blocked_clause
        cparams.extend(blocked_params)
        if _HIDDEN_COMMUNITY_DISEASES:
            common += _hidden_clause()
            cparams.extend(_HIDDEN_COMMUNITY_DISEASES)
        if cursor is not None:
            cursor_created, cursor_row_id = cursor
            common += " AND (created_at < ? OR (created_at = ? AND id < ?))"
            cparams.extend((int(cursor_created), int(cursor_created), int(cursor_row_id)))

        # Geo scopes, most specific first. A disease-scoped carousel widens
        # nearby→country; the plain feed stays in a single scope.
        widen = disease is not None
        geo_scopes: list[tuple[str, list, bool]] = []
        if scope == "worldwide":
            # No geo predicate at all — every country, newest first. The
            # requester's coords/country are intentionally ignored so the
            # feed works even without a location fix.
            geo_scopes.append(("", [], False))
        else:
            if nearby and has_coords:
                min_lat, max_lat, min_lon, max_lon = _bbox(lat, lon, radius_km)
                geo_scopes.append((
                    " AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                    [min_lat, max_lat, min_lon, max_lon], True,
                ))
            if country and (scope == "country" or widen or not has_coords):
                geo_scopes.append((" AND country = ?", [country], False))

        species_lower = (species or "").strip().lower()
        seen: set[str] = set()
        out: list[dict] = []

        def _run(geo_clause, geo_params, dz_clause, dz_params, refine):
            # Over-fetch when we'll refine the box to a true circle so the
            # post-filter still has ~limit rows to offer.
            take = limit * 3 if refine else limit
            # The column list and clauses are fixed; all request values are bound.
            sql = (
                f"SELECT {_FEED_COLUMNS}, id FROM community_scans WHERE 1=1"  # nosec B608
                + geo_clause + common + dz_clause
                + " ORDER BY created_at DESC, id DESC LIMIT ?"
            )
            rows = [
                {
                    **_row_to_dict(r[:_FEED_COLUMN_COUNT]),
                    "_cursor_row_id": int(r[_FEED_COLUMN_COUNT]),
                }
                for r in conn.execute(
                    sql, (*geo_params, *cparams, *dz_params, take),
                ).fetchall()
            ]
            if refine:
                rows = [
                    r for r in rows
                    if r["lat"] is not None and r["lon"] is not None
                    and community._haversine_km(lat, lon, r["lat"], r["lon"]) <= radius_km
                ]
            for row in rows:
                if row["scan_id"] in seen:
                    continue
                out.append(row)
                seen.add(row["scan_id"])

        for geo_clause, geo_params, refine in geo_scopes:
            if len(out) >= limit:
                break
            if disease and species_lower:
                _run(geo_clause, geo_params,
                     " AND disease = ? AND species_lower = ?",
                     [disease, species_lower], refine)
            if len(out) < limit and disease:
                _run(geo_clause, geo_params, " AND disease = ?", [disease], refine)
            if len(out) < limit and not disease:
                _run(geo_clause, geo_params, "", [], refine)
        result = out[:limit]
        # Attach an approximate distance from the requester ("~5 km near you")
        # and DROP the raw coords — a viewer gets the distance, never another
        # user's ~1 km location. Distance is omitted when we lack either point.
        for row in result:
            rlat, rlon = row.get("lat"), row.get("lon")
            if has_coords and rlat is not None and rlon is not None:
                row["distance_km"] = round(community._haversine_km(lat, lon, rlat, rlon), 1)
            else:
                row["distance_km"] = None
            row.pop("lat", None)
            row.pop("lon", None)
        return result


def map_scans(
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    *,
    disease: str | None = None,
    days: int = 30,
    limit: int = 300,
    exclude_user_pseudo_id: str | None = None,
    exclude_account_actor_id: str | None = None,
    viewer_account_actor_id: str | None = None,
) -> dict:
    """Individual shared scans inside a bbox — the zoomed-in map markers.

    Unlike the feed (:func:`nearby_scans`), this KEEPS each scan's ~1 km-rounded
    ``lat``/``lon`` so the client can pin a marker: the user explicitly wants
    individual scans on the map, and the coords are already coarse + anonymous
    (public token only, never the real scan_id or the author). ``not_a_plant`` excluded;
    newest-first, capped at ``limit`` (``truncated`` flags an over-full bbox so
    the UI can prompt "zoom in to see all")."""
    community.init_db()
    limit = max(1, min(int(limit), 500))
    days = max(1, min(int(days), 90))
    cutoff = int(time.time()) - days * 86_400
    clause = (
        " AND lat IS NOT NULL AND lon IS NOT NULL"
        " AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND created_at >= ?"
    )
    params: list = [min_lat, max_lat, min_lon, max_lon, cutoff]
    if exclude_user_pseudo_id:
        clause += " AND user_pseudo_id != ?"
        params.append(exclude_user_pseudo_id)
    if exclude_account_actor_id:
        clause += " AND (account_actor_id IS NULL OR account_actor_id != ?)"
        params.append(exclude_account_actor_id)
    blocked_clause, blocked_params = community._blocked_actor_clause(
        viewer_account_actor_id
    )
    clause += blocked_clause
    params.extend(blocked_params)
    if disease:
        clause += " AND disease = ?"
        params.append(disease)
    clause += _hidden_clause()
    params.extend(_HIDDEN_COMMUNITY_DISEASES)
    with community._connect() as conn:
        # The column list and clauses are fixed or allowlisted; values are bound.
        rows = conn.execute(
            f"SELECT {_FEED_COLUMNS} FROM community_scans WHERE 1=1"  # nosec B608
            + clause
            + " ORDER BY created_at DESC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
    truncated = len(rows) > limit
    # _row_to_dict keeps lat/lon — the map needs them (the feed strips them).
    items = [_row_to_dict(r) for r in rows[:limit]]
    return {"items": items, "truncated": truncated}


_DENSITY_K = 3


def _density_cell_size(lat_span: float) -> float:
    """Grid-cell size (deg) for a bbox's latitude span — coarser when zoomed
    out, floored at 0.05° (~5.5 km, ~5x the ~1.1 km storage rounding) so a cell
    always blends many distinct positions and no single ~1 km scan is
    pinpointable."""
    if lat_span > 40:
        return 1.0
    if lat_span > 15:
        return 0.5
    if lat_span > 5:
        return 0.2
    if lat_span > 2:
        return 0.1
    return 0.05


def density_grid(
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    *,
    disease: str | None = None,
    days: int = 30,
    k: int = _DENSITY_K,
) -> dict:
    """K-anonymized scan density for the zoomed-out heat layer.

    Buckets the (already 2-dp-rounded) coords into grid cells and returns only
    cells holding >= ``k`` scans AFTER the disease/time filter, so a returned
    cell never represents fewer than ``k`` scans; ``not_a_plant`` excluded. The
    map receives cell centers + counts only — never per-scan coords."""
    community.init_db()
    days = max(1, min(int(days), 90))
    cutoff = int(time.time()) - days * 86_400
    cell = _density_cell_size(max_lat - min_lat)
    disease_clause = ""
    disease_params: list = []
    if disease:
        disease_clause = " AND disease = ?"
        disease_params.append(disease)
    hidden_clause = _hidden_clause()
    # SQLite CAST(.. AS INTEGER) truncates toward zero — wrong floor for negative
    # (W/S) coords — so floor with ROUND(z/cell - 0.5) to bucket on a stable grid.
    with community._connect() as conn:
        # The optional disease clause is fixed and all request values are bound.
        rows = conn.execute(
            "SELECT CAST(ROUND(lat/? - 0.5) AS INTEGER) AS ilat,"  # nosec B608
            "       CAST(ROUND(lon/? - 0.5) AS INTEGER) AS ilon, COUNT(*) AS n "
            "FROM community_scans "
            "WHERE lat IS NOT NULL AND lon IS NOT NULL "
            "  AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND created_at >= ?"
            + disease_clause + hidden_clause
            + " GROUP BY ilat, ilon HAVING n >= ?",
            (cell, cell, min_lat, max_lat, min_lon, max_lon, cutoff,
             *disease_params, *_HIDDEN_COMMUNITY_DISEASES, int(k)),
        ).fetchall()
    cells = [
        {"lat": round(r[0] * cell, 6), "lon": round(r[1] * cell, 6), "count": int(r[2])}
        for r in rows
    ]
    return {
        "cells": cells,
        "cell_size_deg": cell,
        "max_count": max((c["count"] for c in cells), default=0),
        "total": sum(c["count"] for c in cells),
        "k": int(k),
        "days": days,
    }


def region_summary(
    country: str,
    region: str | None = None,
    *,
    exclude_user_pseudo_id: str | None = None,
    exclude_account_actor_id: str | None = None,
    viewer_account_actor_id: str | None = None,
) -> dict:
    """Aggregate the corpus for one country (or country+region)."""
    community.init_db()
    country = to_iso_alpha2(country)
    now = int(time.time())
    week_ago = now - 7 * 86_400
    region_clean = (region or "").strip()
    exclude_clause = ""
    exclude_params: tuple = ()
    if exclude_user_pseudo_id:
        exclude_clause = " AND user_pseudo_id != ?"
        exclude_params = (exclude_user_pseudo_id,)
    if exclude_account_actor_id:
        exclude_clause += " AND (account_actor_id IS NULL OR account_actor_id != ?)"
        exclude_params = (*exclude_params, exclude_account_actor_id)
    blocked_clause, blocked_params = community._blocked_actor_clause(
        viewer_account_actor_id
    )
    exclude_clause += blocked_clause
    exclude_params = (*exclude_params, *blocked_params)
    if _HIDDEN_COMMUNITY_DISEASES:
        exclude_clause += _hidden_clause()
        exclude_params = (*exclude_params, *_HIDDEN_COMMUNITY_DISEASES)
    with community._connect() as conn:
        if region_clean:
            # exclude_clause is assembled from fixed predicates; values are bound.
            total = conn.execute(
                "SELECT COUNT(*) FROM community_scans "  # nosec B608
                "WHERE country = ? AND region = ?" + exclude_clause,
                (country, region_clean, *exclude_params),
            ).fetchone()[0]
            last_week = conn.execute(
                "SELECT COUNT(*) FROM community_scans "  # nosec B608
                "WHERE country = ? AND region = ? AND created_at >= ?"
                + exclude_clause,
                (country, region_clean, week_ago, *exclude_params),
            ).fetchone()[0]
            disease_rows = conn.execute(
                "SELECT disease, COUNT(*) AS n FROM community_scans "  # nosec B608
                "WHERE country = ? AND region = ? AND created_at >= ?"
                + exclude_clause
                + " GROUP BY disease ORDER BY n DESC LIMIT 3",
                (country, region_clean, week_ago, *exclude_params),
            ).fetchall()
            top_region_rows: list[tuple] = []
        else:
            total = conn.execute(
                "SELECT COUNT(*) FROM community_scans WHERE country = ?"  # nosec B608
                + exclude_clause,
                (country, *exclude_params),
            ).fetchone()[0]
            last_week = conn.execute(
                "SELECT COUNT(*) FROM community_scans "  # nosec B608
                "WHERE country = ? AND created_at >= ?" + exclude_clause,
                (country, week_ago, *exclude_params),
            ).fetchone()[0]
            disease_rows = conn.execute(
                "SELECT disease, COUNT(*) AS n FROM community_scans "  # nosec B608
                "WHERE country = ? AND created_at >= ?" + exclude_clause
                + " GROUP BY disease ORDER BY n DESC LIMIT 3",
                (country, week_ago, *exclude_params),
            ).fetchall()
            top_region_rows = conn.execute(
                "SELECT region, COUNT(*) AS n FROM community_scans "  # nosec B608
                "WHERE country = ? AND region != '' AND created_at >= ?"
                + exclude_clause
                + " GROUP BY region ORDER BY n DESC LIMIT 3",
                (country, week_ago, *exclude_params),
            ).fetchall()
    top_diseases = [
        {"disease": str(r[0]), "count": int(r[1])} for r in disease_rows
    ]
    top_regions = [
        {"region": str(r[0]), "count": int(r[1])} for r in top_region_rows
    ]
    return {
        "country": country,
        "region": region_clean or None,
        "total": int(total),
        "last_7d": int(last_week),
        "top_diseases": top_diseases,
        "top_regions": top_regions,
    }


def _row_to_dict(row: tuple) -> dict:
    return {
        # Real scan_id — INTERNAL ONLY. Viewer-facing endpoints must
        # replace it with ``public_id`` before serialising (see the
        # nearby / by-species handlers in routes_community.py). Column order
        # matches ``_FEED_COLUMNS``. Rows carry no author: scans are shown
        # without identity.
        "scan_id": row[0],
        "public_id": community.public_scan_token(str(row[0])),
        "country": row[1] or "",
        "region": row[2] or "",
        "city": row[3] or "",
        "lat": float(row[4]) if row[4] is not None else None,
        "lon": float(row[5]) if row[5] is not None else None,
        "disease": row[6],
        "confidence": float(row[7]) if row[7] is not None else None,
        "species": row[8],
        "species_score": float(row[9]) if row[9] is not None else None,
        "thumb_path": row[10],
        "locale": row[11],
        "created_at": int(row[12]),
    }
