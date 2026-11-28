"""Consensus diagnosis labels from community answers, for model retraining.

Likes mean "this helped", not "this is correct", so they never become labels.
A post gets a label only from structured diagnosis tags:

* an **expert-verified** answer decides on its own;
* otherwise the **accepted** answer's tag (or the AI verdict, if the asker
  accepted the AI answer) counts double, every other human answer's tag
  counts once (1.5 from level-3 helpers), one vote per person;
* a label needs ``MIN_VOTERS`` distinct people and ``MIN_AGREEMENT`` of the
  weighted votes.

The AI verdict itself never votes for its own label. Posts where a confident
AI verdict disagrees with the agreed label are flagged as hard examples, and
posts where helpers push back on a confident AI answer without agreeing yet
go to the experts' label-review list.

Labels ride along as hints in ``tools/export_training_bundle.py``, which
exports only photos whose author turned on training consent, from posts no
admin excluded. All community labels are train-only weak labels until an
offline review.
"""

from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable

try:
    from . import community, community_forum
except ImportError:
    import community
    import community_forum

MIN_VOTERS = 3
MIN_AGREEMENT = 0.70
CONFIDENT_AI = 0.70
REVIEW_NOT_HELPFUL = 3
ACCEPTED_WEIGHT = 2.0
TRUSTED_WEIGHT = 1.5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS forum_labels (
    post_id       INTEGER PRIMARY KEY,
    label         TEXT    NOT NULL,
    source        TEXT    NOT NULL CHECK(source IN ('expert', 'consensus')),
    agreement     REAL    NOT NULL,
    voters        INTEGER NOT NULL,
    ai_disease    TEXT,
    ai_confidence REAL,
    hard_example  INTEGER NOT NULL DEFAULT 0,
    computed_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS forum_label_votes (
    post_id  INTEGER NOT NULL,
    actor_id TEXT    NOT NULL,
    label    TEXT    NOT NULL,
    PRIMARY KEY (post_id, actor_id)
);
CREATE INDEX IF NOT EXISTS ix_forum_label_votes_actor ON forum_label_votes(actor_id);
"""

_schema_ready_for: str | None = None

# Set by the achievements module: helper weight (1.0 or TRUSTED_WEIGHT).
voter_weight: Callable[[sqlite3.Connection, str], float] | None = None


def init_db() -> None:
    global _schema_ready_for
    community_forum.init_db()
    key = str(community.COMMUNITY_DB_PATH)
    if _schema_ready_for == key:
        return
    with community._write_lock, community._connect() as conn:
        conn.executescript(_SCHEMA)
    _schema_ready_for = key


def _decide(conn, post_id: int, post_author: str, accepted_id: int | None, ai_disease: str | None):
    """Return ``(label, source, agreement, voters, votes)`` or ``None``."""
    rows = conn.execute(
        "SELECT id, author_actor_id, is_ai, diagnosis, verified_at FROM forum_answers "
        "WHERE post_id = ? AND state = 'visible' "
        "AND (author_actor_id IS NULL OR author_actor_id NOT IN "
        "(SELECT actor_id FROM community_deleted_actors)) ORDER BY id",
        (post_id,),
    ).fetchall()
    verified = {str(r[3]) for r in rows if r[4] is not None and r[3] and not r[2]}
    votes: dict[str, str] = {}
    weights: dict[str, float] = {}
    for answer_id, author, is_ai, diagnosis, _verified_at in rows:
        if answer_id == accepted_id:
            label = ai_disease if is_ai else diagnosis
            if label:
                # The asker accepting an answer is the asker's own vote.
                votes[post_author] = str(label)
                weights[post_author] = ACCEPTED_WEIGHT
                if not is_ai and author and author != post_author and author not in votes:
                    votes[author] = str(label)
                    weights[author] = _weight(conn, author)
            continue
        if is_ai or not diagnosis or not author or author in votes:
            continue
        votes[author] = str(diagnosis)
        weights[author] = _weight(conn, author)
    if len(verified) == 1:
        label = next(iter(verified))
        return label, "expert", 1.0, len(votes), votes
    if len(verified) > 1 or len(votes) < MIN_VOTERS:
        # Experts disagree, or too few people have weighed in yet.
        return None, None, 0.0, len(votes), votes
    tally: dict[str, float] = defaultdict(float)
    for actor, label in votes.items():
        tally[label] += weights[actor]
    label, top = max(tally.items(), key=lambda item: item[1])
    agreement = top / sum(tally.values())
    if agreement < MIN_AGREEMENT:
        return None, None, agreement, len(votes), votes
    return label, "consensus", agreement, len(votes), votes


def _weight(conn, actor: str) -> float:
    if voter_weight is None:
        return 1.0
    try:
        return float(voter_weight(conn, actor))
    except Exception:  # noqa: BLE001 — a weighting failure falls back to one vote
        return 1.0


def refresh(*, limit: int = 2_000, now: int | None = None) -> dict[str, int]:
    """Recompute labels for recently active posts. Idempotent."""
    init_db()
    timestamp = int(time.time() if now is None else now)
    labelled = 0
    cleared = 0
    with community._write_lock, community._connect() as conn:
        posts = conn.execute(
            "SELECT id, author_actor_id, accepted_answer_id, ai_disease, ai_confidence "
            "FROM forum_posts WHERE state = 'visible' "
            "AND author_actor_id NOT IN (SELECT actor_id FROM community_deleted_actors) "
            "ORDER BY last_activity_at DESC LIMIT ?",
            (max(1, min(int(limit), 20_000)),),
        ).fetchall()
        for post_id, author, accepted_id, ai_disease, ai_confidence in posts:
            decision = _decide(conn, int(post_id), str(author), accepted_id, ai_disease)
            conn.execute("DELETE FROM forum_label_votes WHERE post_id = ?", (post_id,))
            if decision is not None:
                for voter, label in decision[4].items():
                    conn.execute(
                        "INSERT INTO forum_label_votes (post_id, actor_id, label) VALUES (?, ?, ?)",
                        (post_id, voter, label),
                    )
            if decision is None or decision[0] is None:
                cleared += conn.execute(
                    "DELETE FROM forum_labels WHERE post_id = ?", (post_id,)
                ).rowcount
                continue
            label, source, agreement, voters, _votes = decision
            hard = bool(
                ai_disease
                and ai_confidence is not None
                and float(ai_confidence) >= CONFIDENT_AI
                and ai_disease != label
            )
            conn.execute(
                "INSERT INTO forum_labels (post_id, label, source, agreement, voters, ai_disease, "
                "ai_confidence, hard_example, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(post_id) DO UPDATE SET label = excluded.label, "
                "source = excluded.source, agreement = excluded.agreement, "
                "voters = excluded.voters, ai_disease = excluded.ai_disease, "
                "ai_confidence = excluded.ai_confidence, hard_example = excluded.hard_example, "
                "computed_at = excluded.computed_at",
                (
                    post_id, label, source, round(float(agreement), 4), int(voters),
                    ai_disease, ai_confidence, 1 if hard else 0, timestamp,
                ),
            )
            labelled += 1
        # Labels of posts that are no longer visible go too.
        cleared += conn.execute(
            "DELETE FROM forum_labels WHERE post_id NOT IN "
            "(SELECT id FROM forum_posts WHERE state = 'visible')"
        ).rowcount
        conn.execute(
            "DELETE FROM forum_label_votes WHERE post_id NOT IN "
            "(SELECT id FROM forum_posts WHERE state = 'visible')"
        )
    return {"labelled": labelled, "cleared": int(cleared or 0)}


def label_for(post_public_id: str) -> dict | None:
    init_db()
    with community._connect() as conn:
        row = conn.execute(
            "SELECT l.label, l.source, l.agreement, l.voters, l.hard_example "
            "FROM forum_labels l JOIN forum_posts p ON p.id = l.post_id WHERE p.public_id = ?",
            (post_public_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "label": row[0],
        "source": row[1],
        "agreement": float(row[2]),
        "voters": int(row[3]),
        "hard_example": bool(row[4]),
    }


def review_queue(*, limit: int = 50) -> dict:
    """Posts for experts: confident AI answers that helpers push back on.

    Either the agreed label contradicts a confident AI verdict, or no label
    exists yet while the AI answer has collected several "not helpful" marks.
    """
    init_db()
    bounded = max(1, min(int(limit), 100))
    with community._connect() as conn:
        rows = conn.execute(
            "SELECT p.public_id, p.title, p.ai_disease, p.ai_confidence, l.label, "
            "a.not_helpful_count, p.space, p.created_at FROM forum_posts p "
            "JOIN forum_answers a ON a.post_id = p.id AND a.is_ai = 1 "
            "LEFT JOIN forum_labels l ON l.post_id = p.id "
            "WHERE p.state = 'visible' AND p.ai_confidence >= ? "
            "AND ((l.label IS NOT NULL AND l.label != p.ai_disease AND l.source = 'consensus') "
            "OR (l.label IS NULL AND a.not_helpful_count >= ?)) "
            "AND NOT EXISTS (SELECT 1 FROM forum_answers v WHERE v.post_id = p.id "
            "AND v.verified_at IS NOT NULL) "
            "ORDER BY p.ai_confidence DESC, p.created_at LIMIT ?",
            (CONFIDENT_AI, REVIEW_NOT_HELPFUL, bounded),
        ).fetchall()
    return {
        "kind": "label_review",
        "items": [
            {
                "post_id": r[0],
                "title": r[1],
                "ai_disease": r[2],
                "ai_confidence": float(r[3]),
                "community_label": r[4],
                "ai_not_helpful": int(r[5]),
                "space": r[6],
                "created_at": int(r[7]),
            }
            for r in rows
        ],
    }


def agreed_votes_for(conn, actor: str) -> tuple[int, int]:
    """``(matched, total)``: labelled posts where this actor voted, and matches."""
    try:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(v.label = l.label), 0) FROM forum_label_votes v "
            "JOIN forum_labels l ON l.post_id = v.post_id "
            "JOIN forum_posts p ON p.id = v.post_id "
            "WHERE v.actor_id = ? AND p.author_actor_id != ?",
            (actor, actor),
        ).fetchone()
    except Exception:  # noqa: BLE001 — schema not created yet
        return 0, 0
    return int(row[1]), int(row[0])


def taught_labels_for(conn, actor: str) -> int:
    """Consented, labelled posts where this actor's tag matched the agreed label."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM forum_label_votes v "
            "JOIN forum_labels l ON l.post_id = v.post_id AND l.label = v.label "
            "JOIN forum_posts p ON p.id = v.post_id "
            "WHERE v.actor_id = ? AND p.author_actor_id != ? "
            "AND p.training_consent = 1 AND p.training_excluded = 0",
            (actor, actor),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return 0
    return int(row[0])


def _purge_actor(conn, actor: str) -> None:
    try:
        conn.execute("DELETE FROM forum_label_votes WHERE actor_id = ?", (actor,))
    except Exception:  # noqa: BLE001 — table may not exist yet
        return


community.register_actor_hooks(purge=_purge_actor)
