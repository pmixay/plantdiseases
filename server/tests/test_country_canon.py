"""Country bucket canonicalization to ISO-3166 alpha-2.

A community country must bucket the SAME whether the client sent a code
("RU"), an English name ("Russia") or a localized name ("Россия"). Before
this, the corpus split one country across representations and an
English-locale user saw a near-empty popular / nearby feed while Russian
worked. Covers the pure mapper, the write-side canonicalization, and the
one-off migration of legacy rows.
"""

from __future__ import annotations

import sqlite3
import time as _time
from contextlib import closing

import pytest

from countries import to_iso_alpha2


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("RU", "RU"),
        ("ru", "RU"),
        (" ru ", "RU"),
        ("Russia", "RU"),
        ("россия", "RU"),
        ("Россия", "RU"),
        ("Russian Federation", "RU"),
        ("USA", "US"),
        ("United States", "US"),
        ("США", "US"),
        ("uk", "GB"),
        ("England", "GB"),
        ("PL", "PL"),
        ("", ""),
        (None, ""),
        ("Narnia", "Narnia"),  # unknown -> passthrough, still a stable bucket
    ],
)
def test_to_iso_alpha2(raw, expected):
    assert to_iso_alpha2(raw) == expected


def test_record_scan_canonicalizes_country(community_store):
    """Two uploads for the same country in different spellings land in one
    ISO bucket, so popular_snapshot(country="RU") sees both."""
    comm = community_store

    comm.record_scan(
        scan_id="canonscan01", user_pseudo_id="pseudo-canon-a",
        country="Russia", region="Moscow", disease="leaf_spot",
        species="Spathiphyllum blandum",
    )
    comm.record_scan(
        scan_id="canonscan02", user_pseudo_id="pseudo-canon-b",
        country="Россия", region="Moscow", disease="leaf_spot",
        species="Monstera deliciosa",
    )

    snap = comm.popular_snapshot(country="RU")
    assert snap["total"] == 2
    species = {row["species"] for row in snap["top_species"]}
    assert species == {"Spathiphyllum blandum", "Monstera deliciosa"}

    # The query side canonicalizes too, so a localized spelling finds the same
    # bucket and an unknown country finds nothing.
    assert comm.popular_snapshot(country="Россия")["total"] == 2
    assert comm.popular_snapshot(country="Narnia")["total"] == 0


def test_migration_canonicalizes_legacy_country_rows(community_store, tmp_path):
    """Legacy rows stored under a localized country name are folded to the
    ISO code on init_db."""
    comm = community_store

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
    now = int(_time.time())
    conn.execute(
        "INSERT INTO community_scans (scan_id, user_pseudo_id, country, region,"
        " disease, created_at) VALUES (?,?,?,?,?,?)",
        ("legacyru0001", "pseudo-legacy-ru", "Россия", "Moscow", "leaf_spot", now),
    )
    conn.execute(
        "INSERT INTO community_scans (scan_id, user_pseudo_id, country, region,"
        " disease, created_at) VALUES (?,?,?,?,?,?)",
        ("legacyru0002", "pseudo-legacy-ru2", "Russia", "Moscow", "leaf_spot", now),
    )
    conn.commit()
    conn.close()

    comm.init_db()

    with closing(sqlite3.connect(tmp_path / "community.db")) as conn:
        buckets = {
            row[0]
            for row in conn.execute("SELECT DISTINCT country FROM community_scans")
        }
    assert buckets == {"RU"}
