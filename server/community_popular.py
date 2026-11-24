"""Community "popular plants": the anonymous per-day tally and the
nearby/country snapshot the popular-plants bundle reads.

Reads and writes go through ``community._connect()`` at call time, so a test
that redirects ``community.COMMUNITY_DB_PATH`` is followed. The shared schema,
locks and hidden-disease clause stay in ``community.py``."""

from __future__ import annotations

import time

try:
    from . import community, community_feeds
    from .countries import to_iso_alpha2
except ImportError:
    import community
    import community_feeds
    from countries import to_iso_alpha2


_ANON_SPECIES_MAX = 128
ANON_POPULAR_WINDOW_DAYS = 30


def record_anonymous_scan(species: str | None, disease: str, *, day: int | None = None) -> None:
    """Add one accepted diagnosis to the anonymous popularity tally.

    Only the identified species (if any) and the diagnosis class are counted,
    per UTC day: nothing that identifies the user, the device, the photo or
    where it was taken. Hidden classes (``not_a_plant``) are not counted.
    """
    disease = (disease or "").strip()
    if not disease or disease in community_feeds._HIDDEN_COMMUNITY_DISEASES:
        return
    name = " ".join((species or "").split())[:_ANON_SPECIES_MAX]
    day = int(time.time()) // 86_400 if day is None else day
    community.init_db()
    with community._connect() as conn:
        conn.execute(
            "INSERT INTO popular_scan_counts (day, species_lower, species, disease, n) "
            "VALUES (?, ?, ?, ?, 1) "
            "ON CONFLICT (day, species_lower, disease) DO UPDATE SET n = n + 1",
            (day, name.lower(), name, disease),
        )


def anonymous_popular_snapshot(
    *,
    limit: int = 10,
    days: int = ANON_POPULAR_WINDOW_DAYS,
    today: int | None = None,
) -> dict:
    """Worldwide popular species + diagnoses from the anonymous tally over the
    last ``days`` UTC days, in :func:`popular_snapshot`'s shape."""
    community.init_db()
    limit = max(1, min(int(limit), 20))
    today = int(time.time()) // 86_400 if today is None else today
    since = today - max(1, int(days)) + 1
    with community._connect() as conn:
        total = int(conn.execute(
            "SELECT COALESCE(SUM(n), 0) FROM popular_scan_counts WHERE day >= ?", (since,),
        ).fetchone()[0])
        species_rows = conn.execute(
            "SELECT MIN(species), SUM(n) AS c FROM popular_scan_counts "
            "WHERE day >= ? AND species_lower != '' "
            "GROUP BY species_lower ORDER BY c DESC LIMIT ?",
            (since, limit),
        ).fetchall()
        disease_rows = conn.execute(
            "SELECT disease, SUM(n) AS c FROM popular_scan_counts WHERE day >= ? "
            "GROUP BY disease ORDER BY c DESC LIMIT ?",
            (since, limit),
        ).fetchall()

    def pct(n: int) -> int:
        return int(round((n / total) * 100)) if total > 0 else 0

    return {
        "total": total,
        "top_species": [
            {"species": str(r[0]), "count": int(r[1]), "percent": pct(int(r[1]))}
            for r in species_rows
        ],
        "top_diseases": [
            {"disease": str(r[0]), "count": int(r[1]), "percent": pct(int(r[1]))}
            for r in disease_rows
        ],
    }


def popular_snapshot(
    country: str | None = None,
    *,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = community_feeds.NEARBY_RADIUS_KM,
    limit: int = 10,
) -> dict:
    """Live popular species + diseases for one scope.

    Scope precedence: coords (the ~30 km "nearby" box) → ``country`` →
    worldwide (no filter). The caller runs the nearby → country → worldwide
    ladder by calling this with progressively wider arguments.

    The nearby scope uses the bounding box (not a refined circle) — for a
    popularity *aggregate* the square is plenty; the visible feed
    (:func:`nearby_scans`) is the one that refines to a true radius.
    """
    community.init_db()
    country_clean = to_iso_alpha2(country)
    limit = max(1, min(int(limit), 20))
    where = ""
    params: tuple = ()
    if lat is not None and lon is not None:
        min_lat, max_lat, min_lon, max_lon = community_feeds._bbox(lat, lon, radius_km)
        where = " AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"
        params = (min_lat, max_lat, min_lon, max_lon)
    elif country_clean:
        where = " AND country = ?"
        params = (country_clean,)
    hidden_clause = community_feeds._hidden_clause()
    with community._connect() as conn:
        # where/hidden_clause use fixed predicates and allowlisted identifiers.
        total = int(conn.execute(
            "SELECT COUNT(*) FROM community_scans WHERE 1=1"  # nosec B608
            + where + hidden_clause,
            (*params, *community_feeds._HIDDEN_COMMUNITY_DISEASES),
        ).fetchone()[0])
        species_rows = conn.execute(
            "SELECT MAX(species) AS sp, COUNT(*) AS n FROM community_scans "  # nosec B608
            "WHERE species_lower IS NOT NULL AND species_lower != ''"
            + where + hidden_clause
            + " GROUP BY species_lower ORDER BY n DESC LIMIT ?",
            (*params, *community_feeds._HIDDEN_COMMUNITY_DISEASES, limit),
        ).fetchall()
        disease_rows = conn.execute(
            "SELECT disease, COUNT(*) AS n FROM community_scans "  # nosec B608
            "WHERE 1=1" + where + hidden_clause
            + " GROUP BY disease ORDER BY n DESC LIMIT ?",
            (*params, *community_feeds._HIDDEN_COMMUNITY_DISEASES, limit),
        ).fetchall()

    def pct(n: int) -> int:
        return int(round((n / total) * 100)) if total > 0 else 0

    return {
        "total": total,
        "top_species": [
            {"species": str(r[0]), "count": int(r[1]), "percent": pct(int(r[1]))}
            for r in species_rows
        ],
        "top_diseases": [
            {"disease": str(r[0]), "count": int(r[1]), "percent": pct(int(r[1]))}
            for r in disease_rows
        ],
    }
