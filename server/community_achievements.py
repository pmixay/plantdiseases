"""Community levels, achievements and the PRO-discount achievement count.

Everything here is recomputed from the forum's current state, so content that
is removed or deleted simply stops counting, and an appeal that restores it
brings the credit back.

Levels ("leaves") unlock trust, not money:

    accepted answer +15, expert-verified answer +20, like on an answer +2
    (at most 40 a day), like on a post +1 (at most 20 a day), a recovery
    update on a plant you diagnosed +25.

    1 Seedling 0 · 2 Sprout 50 (may mark answers "not helpful"; reports count
    toward auto-hide) · 3 Gardener 300 (diagnosis tags weigh 1.5x in labels)
    · 4 Botanist 1000 · 5 Plant Doctor 3000 and >= 70 % agreement with the
    community on 10+ labelled posts.

Achievements reward help someone else confirmed. There are 30 tiers; each
earned tier is one achievement and is worth 2 % off PRO, up to 40 %. The
account service derives the discount from the count reported here.

Anti-farming:
* only accepted answers on questions with a photo count, at most one per
  asker per week and three per asker in total;
* the discount count stays 0 until the member's first forum activity is 14
  days old, and while an admin-visible freeze is in place;
* a nightly pass freezes pairs who mostly accept or like each other.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections import defaultdict

try:
    from . import community, community_forum, community_labels  # type: ignore[import-not-found]
except ImportError:
    import community
    import community_forum
    import community_labels

LEVELS = (
    ("seedling", 0),
    ("sprout", 50),
    ("gardener", 300),
    ("botanist", 1_000),
    ("plant_doctor", 3_000),
)
PLANT_DOCTOR_AGREEMENT = 0.70
PLANT_DOCTOR_MIN_LABELS = 10
LEAVES_ACCEPTED = 15
LEAVES_VERIFIED = 20
LEAVES_ANSWER_LIKE = 2
LEAVES_ANSWER_LIKE_DAILY_CAP = 40
LEAVES_POST_LIKE = 1
LEAVES_POST_LIKE_DAILY_CAP = 20
LEAVES_RESCUE = 25

DISCOUNT_PER_ACHIEVEMENT = 2
DISCOUNT_CAP = 40
DISCOUNT_MIN_ACCOUNT_DAYS = 14
ACCEPTS_PER_ASKER_TOTAL = 3
ACCEPT_ASKER_COOLDOWN_S = 7 * 86_400
LIFELINE_WAIT_S = 86_400
RING_ACCEPTS = 3
RING_LIKES = 20
RING_WINDOW_DAYS = 60

# key -> (tier thresholds, group, condition labels or None)
ACHIEVEMENTS: dict[str, tuple[tuple[int, ...], str, frozenset[str] | None]] = {
    "first_aid": ((1,), "helping", None),
    "lifeline": ((1, 5, 20), "helping", None),
    "rescue_ranger": ((1, 10, 50), "helping", None),
    "pest_control": ((5, 25, 100), "specialist", frozenset({"pests", "mealybug", "scale"})),
    "fungus_detective": (
        (5, 25, 100),
        "specialist",
        frozenset({"powdery_mildew", "gray_mold_botrytis", "leaf_spot"}),
    ),
    "water_whisperer": (
        (5, 25, 100),
        "specialist",
        frozenset({"leaf_scorch_underwatering", "edema", "sunburn_or_scorch"}),
    ),
    "nutrient_nerd": ((5, 25, 100), "specialist", frozenset({"nutrient_deficiency"})),
    "virus_hunter": ((3, 10, 30), "specialist", frozenset({"viral_disease"})),
    "model_teacher": ((10, 100, 500), "model", None),
    "bilingual": ((1,), "helping", None),
    "welcome_wagon": ((10,), "community", None),
    "good_question": ((1,), "asking", None),
    "closing_the_loop": ((1,), "asking", None),
    "photo_journal": ((1,), "asking", None),
}
TOTAL_TIERS = sum(len(tiers) for tiers, _group, _labels in ACHIEVEMENTS.values())

_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_LATIN = re.compile(r"[A-Za-z]")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS forum_achievements (
    actor_id    TEXT    NOT NULL,
    achievement TEXT    NOT NULL,
    tier        INTEGER NOT NULL,
    awarded_at  INTEGER NOT NULL,
    PRIMARY KEY (actor_id, achievement, tier)
);
CREATE TABLE IF NOT EXISTS forum_reward_freezes (
    actor_id    TEXT    PRIMARY KEY,
    reason      TEXT    NOT NULL,
    created_at  INTEGER NOT NULL,
    cleared_at  INTEGER
);
CREATE TABLE IF NOT EXISTS forum_reward_reports (
    actor_id    TEXT    PRIMARY KEY,
    reported    INTEGER NOT NULL,
    reported_at INTEGER NOT NULL
);
"""

_schema_ready_for: str | None = None


def init_db() -> None:
    global _schema_ready_for
    community_labels.init_db()
    key = str(community.COMMUNITY_DB_PATH)
    if _schema_ready_for == key:
        return
    with community._write_lock, community._connect() as conn:
        conn.executescript(_SCHEMA)
    _schema_ready_for = key


# ── metrics ─────────────────────────────────────────────────────────────

_LIVE = "NOT IN (SELECT actor_id FROM community_deleted_actors)"


def _countable_accepts(conn: sqlite3.Connection, actor: str) -> list[tuple]:
    """Accepted answers that count: photo questions, capped per asker."""
    rows = conn.execute(
        "SELECT a.id, a.body, a.diagnosis, p.id, p.author_actor_id, "
        "COALESCE(p.accepted_at, p.last_activity_at), p.condition "
        "FROM forum_posts p JOIN forum_answers a ON a.id = p.accepted_answer_id "
        "WHERE a.author_actor_id = ? AND a.is_ai = 0 AND a.state = 'visible' "
        "AND p.state = 'visible' AND p.author_actor_id != ? "
        f"AND p.author_actor_id {_LIVE} "  # nosec B608
        "AND EXISTS (SELECT 1 FROM forum_media m WHERE m.post_id = p.id) "
        "ORDER BY COALESCE(p.accepted_at, p.last_activity_at), p.id",
        (actor, actor),
    ).fetchall()
    counted: list[tuple] = []
    per_asker: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        asker, at = str(row[4]), int(row[5])
        history = per_asker[asker]
        if len(history) >= ACCEPTS_PER_ASKER_TOTAL:
            continue
        if history and at - history[-1] < ACCEPT_ASKER_COOLDOWN_S:
            continue
        history.append(at)
        counted.append(row)
    return counted


def _first_human_answers(conn: sqlite3.Connection, actor: str) -> list[tuple]:
    """Posts where the actor gave the first visible human answer."""
    return conn.execute(
        "SELECT a.id, p.id, p.author_actor_id, p.created_at, a.created_at, "
        "p.accepted_answer_id = a.id, "
        "EXISTS (SELECT 1 FROM forum_answer_reactions r WHERE r.answer_id = a.id "
        "AND r.actor_id = p.author_actor_id AND r.reaction = 'like') "
        "FROM forum_answers a JOIN forum_posts p ON p.id = a.post_id "
        "WHERE a.author_actor_id = ? AND a.is_ai = 0 AND a.state = 'visible' "
        "AND p.state = 'visible' AND p.author_actor_id != ? "
        f"AND p.author_actor_id {_LIVE} "  # nosec B608
        "AND NOT EXISTS (SELECT 1 FROM forum_answers e WHERE e.post_id = p.id "
        "AND e.is_ai = 0 AND e.state = 'visible' AND e.id < a.id)",
        (actor, actor),
    ).fetchall()


def metrics(conn: sqlite3.Connection, actor: str) -> dict[str, int]:
    accepts = _countable_accepts(conn, actor)
    by_condition: dict[str, int] = defaultdict(int)
    has_ru = has_en = False
    for _answer, body, diagnosis, _post, _asker, _at, condition in accepts:
        label = diagnosis or condition
        for key, (_tiers, _group, labels) in ACHIEVEMENTS.items():
            if labels and label in labels:
                by_condition[key] += 1
        text = body or ""
        if _CYRILLIC.search(text):
            has_ru = True
        elif _LATIN.search(text):
            has_en = True

    firsts = _first_human_answers(conn, actor)
    lifeline_per_asker: dict[str, int] = defaultdict(int)
    for _answer, _post, asker, post_created, answer_created, accepted, liked in firsts:
        if answer_created - post_created >= LIFELINE_WAIT_S and (accepted or liked):
            if lifeline_per_asker[str(asker)] < ACCEPTS_PER_ASKER_TOTAL:
                lifeline_per_asker[str(asker)] += 1
    welcome = 0
    for _answer, _post_id, asker, post_created, _answer_created, _accepted, _liked in firsts:
        earlier = conn.execute(
            "SELECT 1 FROM forum_posts WHERE author_actor_id = ? AND created_at < ? LIMIT 1",
            (asker, post_created),
        ).fetchone()
        if earlier is None:
            welcome += 1

    rescues = int(
        conn.execute(
            "SELECT COUNT(DISTINCT parent.id) FROM forum_posts rec "
            "JOIN forum_posts parent ON parent.id = rec.parent_post_id "
            "JOIN forum_answers a ON a.id = parent.accepted_answer_id "
            "WHERE rec.kind = 'recovery' AND rec.state = 'visible' AND parent.state = 'visible' "
            "AND a.author_actor_id = ? AND a.state = 'visible' AND rec.author_actor_id != ?",
            (actor, actor),
        ).fetchone()[0]
    )
    good_question = int(
        conn.execute(
            "SELECT COUNT(*) FROM forum_posts p WHERE p.author_actor_id = ? AND p.kind = 'question' "
            "AND p.state = 'visible' AND p.species IS NOT NULL AND p.body != '' "
            "AND p.like_count >= 5 "
            "AND (SELECT COUNT(*) FROM forum_media m WHERE m.post_id = p.id) >= 2",
            (actor,),
        ).fetchone()[0]
    )
    closing = int(
        conn.execute(
            "SELECT COUNT(DISTINCT p.id) FROM forum_posts p "
            "JOIN forum_answers a ON a.id = p.accepted_answer_id "
            "JOIN forum_posts rec ON rec.parent_post_id = p.id "
            "WHERE p.author_actor_id = ? AND p.state = 'visible' AND a.state = 'visible' "
            "AND (a.is_ai = 1 OR a.author_actor_id != ?) "
            "AND rec.kind = 'recovery' AND rec.state = 'visible' AND rec.author_actor_id = ?",
            (actor, actor, actor),
        ).fetchone()[0]
    )
    journals = 0
    for (parent_id,) in conn.execute(
        "SELECT DISTINCT parent_post_id FROM forum_posts WHERE author_actor_id = ? "
        "AND kind = 'recovery' AND state = 'visible' AND parent_post_id IS NOT NULL",
        (actor,),
    ).fetchall():
        times = [
            int(r[0])
            for r in conn.execute(
                "SELECT created_at FROM forum_posts WHERE parent_post_id = ? AND kind = 'recovery' "
                "AND state = 'visible' AND author_actor_id = ? ORDER BY created_at",
                (parent_id, actor),
            ).fetchall()
        ]
        streak, last = 0, None
        for at in times:
            if last is None or at - last >= 7 * 86_400:
                streak += 1
                last = at
        if streak >= 3:
            journals += 1

    result = {
        "first_aid": len(accepts),
        "lifeline": sum(lifeline_per_asker.values()),
        "rescue_ranger": rescues,
        "model_teacher": community_labels.taught_labels_for(conn, actor),
        "bilingual": 1 if has_ru and has_en else 0,
        "welcome_wagon": welcome,
        "good_question": good_question,
        "closing_the_loop": closing,
        "photo_journal": journals,
        "accepted_answers": len(accepts),
    }
    for key in ACHIEVEMENTS:
        if ACHIEVEMENTS[key][2] is not None:
            result[key] = by_condition[key]
    return result


def leaves(conn: sqlite3.Connection, actor: str) -> int:
    accepted = int(
        conn.execute(
            "SELECT COUNT(*) FROM forum_posts p JOIN forum_answers a ON a.id = p.accepted_answer_id "
            "WHERE a.author_actor_id = ? AND a.is_ai = 0 AND a.state = 'visible' "
            f"AND p.state = 'visible' AND p.author_actor_id != ? AND p.author_actor_id {_LIVE}",  # nosec B608
            (actor, actor),
        ).fetchone()[0]
    )
    verified = int(
        conn.execute(
            "SELECT COUNT(*) FROM forum_answers WHERE author_actor_id = ? AND state = 'visible' "
            "AND verified_at IS NOT NULL",
            (actor,),
        ).fetchone()[0]
    )
    answer_like_days = conn.execute(
        "SELECT COUNT(*) FROM forum_answer_reactions r JOIN forum_answers a ON a.id = r.answer_id "
        "WHERE a.author_actor_id = ? AND a.state = 'visible' AND r.reaction = 'like' "
        f"AND r.actor_id != ? AND r.actor_id {_LIVE} GROUP BY r.created_at / 86400",  # nosec B608
        (actor, actor),
    ).fetchall()
    post_like_days = conn.execute(
        "SELECT COUNT(*) FROM forum_post_likes l JOIN forum_posts p ON p.id = l.post_id "
        "WHERE p.author_actor_id = ? AND p.state = 'visible' "
        f"AND l.actor_id != ? AND l.actor_id {_LIVE} GROUP BY l.created_at / 86400",  # nosec B608
        (actor, actor),
    ).fetchall()
    rescues = int(
        conn.execute(
            "SELECT COUNT(DISTINCT parent.id) FROM forum_posts rec "
            "JOIN forum_posts parent ON parent.id = rec.parent_post_id "
            "JOIN forum_answers a ON a.id = parent.accepted_answer_id "
            "WHERE rec.kind = 'recovery' AND rec.state = 'visible' AND a.author_actor_id = ? "
            "AND rec.author_actor_id != ?",
            (actor, actor),
        ).fetchone()[0]
    )
    return (
        accepted * LEAVES_ACCEPTED
        + verified * LEAVES_VERIFIED
        + sum(min(int(r[0]) * LEAVES_ANSWER_LIKE, LEAVES_ANSWER_LIKE_DAILY_CAP) for r in answer_like_days)
        + sum(min(int(r[0]) * LEAVES_POST_LIKE, LEAVES_POST_LIKE_DAILY_CAP) for r in post_like_days)
        + rescues * LEAVES_RESCUE
    )


def level_of(conn: sqlite3.Connection, actor: str) -> tuple[int, int]:
    """``(level 1..5, leaves)``."""
    points = leaves(conn, actor)
    level = 1
    for index, (_name, threshold) in enumerate(LEVELS, start=1):
        if points >= threshold:
            level = index
    if level == 5:
        matched, total = community_labels.agreed_votes_for(conn, actor)
        if total < PLANT_DOCTOR_MIN_LABELS or matched / total < PLANT_DOCTOR_AGREEMENT:
            level = 4
    return level, points


def _unlocked(values: dict[str, int]) -> list[tuple[str, int]]:
    earned = []
    for key, (tiers, _group, _labels) in ACHIEVEMENTS.items():
        for index, threshold in enumerate(tiers, start=1):
            if values.get(key, 0) >= threshold:
                earned.append((key, index))
    return earned


def _first_activity(conn: sqlite3.Connection, actor: str) -> int | None:
    value = conn.execute(
        "SELECT MIN(created_at) FROM (SELECT created_at FROM forum_posts WHERE author_actor_id = ? "
        "UNION ALL SELECT created_at FROM forum_answers WHERE author_actor_id = ?)",
        (actor, actor),
    ).fetchone()[0]
    return int(value) if value is not None else None


def _frozen(conn: sqlite3.Connection, actor: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM forum_reward_freezes WHERE actor_id = ? AND cleared_at IS NULL",
        (actor,),
    ).fetchone() is not None


def discount_count(conn: sqlite3.Connection, actor: str, earned: int, now: int) -> int:
    first = _first_activity(conn, actor)
    if first is None or now - first < DISCOUNT_MIN_ACCOUNT_DAYS * 86_400:
        return 0
    if _frozen(conn, actor):
        return 0
    return earned


# ── refresh and read ────────────────────────────────────────────────────


def refresh(actor_id: str, *, now: int | None = None) -> list[tuple[str, int]]:
    """Recompute one member's achievements; returns newly awarded tiers."""
    init_db()
    actor = community._normalise_actor_id(actor_id)
    if actor is None:
        return []
    timestamp = int(time.time() if now is None else now)
    with community._write_lock, community._connect() as conn:
        if community._actor_is_deleted(conn, actor):
            return []
        earned = set(_unlocked(metrics(conn, actor)))
        stored = {
            (str(r[0]), int(r[1]))
            for r in conn.execute(
                "SELECT achievement, tier FROM forum_achievements WHERE actor_id = ?", (actor,)
            ).fetchall()
        }
        new = sorted(earned - stored)
        for key, tier in new:
            conn.execute(
                "INSERT OR IGNORE INTO forum_achievements (actor_id, achievement, tier, awarded_at) "
                "VALUES (?, ?, ?, ?)",
                (actor, key, tier, timestamp),
            )
        for key, tier in stored - earned:
            conn.execute(
                "DELETE FROM forum_achievements WHERE actor_id = ? AND achievement = ? AND tier = ?",
                (actor, key, tier),
            )
    return new


def progress(actor_id: str, *, include_private: bool = False, now: int | None = None) -> dict:
    """Level, achievements with progress, and helper stats for one member."""
    init_db()
    actor = community._normalise_actor_id(actor_id)
    if actor is None:
        raise community_forum.ForumError("actor_not_found", 404)
    timestamp = int(time.time() if now is None else now)
    refresh(actor, now=timestamp)
    with community._connect() as conn:
        values = metrics(conn, actor)
        level, points = level_of(conn, actor)
        awarded = {
            (str(r[0]), int(r[1])): int(r[2])
            for r in conn.execute(
                "SELECT achievement, tier, awarded_at FROM forum_achievements WHERE actor_id = ?",
                (actor,),
            ).fetchall()
        }
        nickname = conn.execute(
            "SELECT nickname FROM forum_posts WHERE author_actor_id = ? AND nickname IS NOT NULL "
            "UNION ALL SELECT nickname FROM forum_answers WHERE author_actor_id = ? "
            "AND nickname IS NOT NULL LIMIT 1",
            (actor, actor),
        ).fetchone()
        rescued = values["rescue_ranger"]
        earned = len(awarded)
        private = {}
        if include_private:
            count = discount_count(conn, actor, earned, timestamp)
            first = _first_activity(conn, actor)
            private = {
                "discount": {
                    "achievements": count,
                    "percent": min(count * DISCOUNT_PER_ACHIEVEMENT, DISCOUNT_CAP),
                    "per_achievement": DISCOUNT_PER_ACHIEVEMENT,
                    "cap": DISCOUNT_CAP,
                    "unlocks_at": (
                        first + DISCOUNT_MIN_ACCOUNT_DAYS * 86_400
                        if first is not None
                        and timestamp - first < DISCOUNT_MIN_ACCOUNT_DAYS * 86_400
                        else None
                    ),
                    "frozen": _frozen(conn, actor),
                },
                "can_mark_not_helpful": level >= 2,
            }
    next_threshold = next((t for _n, t in LEVELS if t > points), None)
    achievements = []
    for key, (tiers, group, _labels) in ACHIEVEMENTS.items():
        tier_items = [
            {
                "tier": index,
                "threshold": threshold,
                "awarded_at": awarded.get((key, index)),
            }
            for index, threshold in enumerate(tiers, start=1)
        ]
        achievements.append(
            {
                "key": key,
                "group": group,
                "value": int(values.get(key, 0)),
                "tiers": tier_items,
                "earned_tiers": sum(1 for t in tier_items if t["awarded_at"] is not None),
            }
        )
    return {
        "author_token": community.public_account_author_token(actor),
        "nickname": (nickname[0] if nickname else "") or "",
        "avatar_version": community_forum.avatar_version(actor),
        "level": level,
        "level_name": LEVELS[level - 1][0],
        "leaves": points,
        "next_level_leaves": next_threshold,
        "stats": {
            "accepted_answers": int(values["accepted_answers"]),
            "plants_saved": int(rescued),
            "labels_taught": int(values["model_teacher"]),
        },
        "earned": earned,
        "total": TOTAL_TIERS,
        "achievements": achievements,
        **private,
    }


def progress_for_token(author_token: str) -> dict:
    try:
        from . import community_moderation  # type: ignore[import-not-found]
    except ImportError:
        import community_moderation
    actor = community_moderation.actor_for_token(author_token)
    return progress(actor)


# ── nightly: rings, refresh, report ────────────────────────────────────


def detect_rings(*, now: int | None = None) -> int:
    """Freeze the rewards of pairs who mostly accept or like each other."""
    init_db()
    timestamp = int(time.time() if now is None else now)
    since = timestamp - RING_WINDOW_DAYS * 86_400
    frozen = 0
    with community._write_lock, community._connect() as conn:
        accept_pairs = {
            (str(r[0]), str(r[1]))
            for r in conn.execute(
                "SELECT p.author_actor_id, a.author_actor_id FROM forum_posts p "
                "JOIN forum_answers a ON a.id = p.accepted_answer_id "
                "WHERE a.is_ai = 0 AND COALESCE(p.accepted_at, p.last_activity_at) >= ? "
                "GROUP BY p.author_actor_id, a.author_actor_id HAVING COUNT(*) >= ?",
                (since, RING_ACCEPTS),
            ).fetchall()
        }
        like_pairs = {
            (str(r[0]), str(r[1]))
            for r in conn.execute(
                "SELECT r.actor_id, a.author_actor_id FROM forum_answer_reactions r "
                "JOIN forum_answers a ON a.id = r.answer_id WHERE r.reaction = 'like' "
                "AND r.created_at >= ? AND a.author_actor_id IS NOT NULL "
                "GROUP BY r.actor_id, a.author_actor_id HAVING COUNT(*) >= ?",
                (since, RING_LIKES),
            ).fetchall()
        }
        suspects: set[str] = set()
        for pairs in (accept_pairs, like_pairs):
            for first, second in pairs:
                if first != second and (second, first) in pairs:
                    suspects.update({first, second})
        for actor in sorted(suspects):
            created = conn.execute(
                "INSERT OR IGNORE INTO forum_reward_freezes (actor_id, reason, created_at) "
                "VALUES (?, 'mutual_rewards', ?)",
                (actor, timestamp),
            ).rowcount
            frozen += int(created or 0)
    return frozen


def set_frozen(*, admin_actor_id: str, actor_id: str, frozen: bool, now: int | None = None) -> dict:
    """An admin confirms or clears a reward freeze; clearing is remembered."""
    init_db()
    timestamp = int(time.time() if now is None else now)
    try:
        from . import community_moderation  # type: ignore[import-not-found]
    except ImportError:
        import community_moderation
    with community._write_lock, community._connect() as conn:
        if frozen:
            conn.execute(
                "INSERT INTO forum_reward_freezes (actor_id, reason, created_at) "
                "VALUES (?, 'admin', ?) ON CONFLICT(actor_id) DO UPDATE SET "
                "reason = 'admin', created_at = excluded.created_at, cleared_at = NULL",
                (actor_id, timestamp),
            )
        else:
            conn.execute(
                "UPDATE forum_reward_freezes SET cleared_at = ? WHERE actor_id = ?",
                (timestamp, actor_id),
            )
    community_moderation.init_db()
    with community._write_lock, community._connect() as conn:
        community_moderation._log(
            conn,
            moderator=admin_actor_id,
            action="freeze_rewards" if frozen else "clear_rewards_freeze",
            target_type="actor",
            target_actor=actor_id,
            now=timestamp,
        )
    return {"frozen": frozen}


def frozen_queue(*, limit: int = 50) -> dict:
    init_db()
    with community._connect() as conn:
        rows = conn.execute(
            "SELECT actor_id, reason, created_at FROM forum_reward_freezes "
            "WHERE cleared_at IS NULL ORDER BY created_at LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        items = []
        for actor, reason, created_at in rows:
            nickname = conn.execute(
                "SELECT nickname FROM forum_posts WHERE author_actor_id = ? "
                "UNION ALL SELECT nickname FROM forum_answers WHERE author_actor_id = ? LIMIT 1",
                (actor, actor),
            ).fetchone()
            items.append(
                {
                    "author_token": community.public_account_author_token(actor),
                    "nickname": (nickname[0] if nickname else "") or "",
                    "reason": reason,
                    "created_at": int(created_at),
                }
            )
    return {"kind": "rewards", "items": items}


def sync_rewards(*, report=None, limit: int = 2_000, now: int | None = None) -> dict[str, int]:
    """Refresh recently active members and report changed discount counts.

    ``report(actor_id, count)`` sends the count to the account service; when
    it is ``None`` (unconfigured) only the local refresh runs.
    """
    init_db()
    timestamp = int(time.time() if now is None else now)
    rings = detect_rings(now=timestamp)
    since = timestamp - RING_WINDOW_DAYS * 86_400
    with community._connect() as conn:
        actors = [
            str(r[0])
            for r in conn.execute(
                f"SELECT actor_id FROM (SELECT author_actor_id AS actor_id FROM forum_posts "  # nosec B608
                "WHERE last_activity_at >= ? UNION SELECT author_actor_id FROM forum_answers "
                "WHERE created_at >= ? AND author_actor_id IS NOT NULL "
                "UNION SELECT actor_id FROM forum_achievements "
                "UNION SELECT actor_id FROM forum_reward_reports) "
                f"WHERE actor_id {_LIVE} LIMIT ?",
                (since, since, max(1, min(int(limit), 20_000))),
            ).fetchall()
        ]
    reported = 0
    failed = 0
    for actor in actors:
        refresh(actor, now=timestamp)
        if report is None:
            continue
        with community._connect() as conn:
            earned = int(
                conn.execute(
                    "SELECT COUNT(*) FROM forum_achievements WHERE actor_id = ?", (actor,)
                ).fetchone()[0]
            )
            count = discount_count(conn, actor, earned, timestamp)
            last = conn.execute(
                "SELECT reported FROM forum_reward_reports WHERE actor_id = ?", (actor,)
            ).fetchone()
        if last is not None and int(last[0]) == count:
            continue
        if last is None and count == 0:
            continue
        try:
            report(actor, count)
        except Exception:  # noqa: BLE001 — retried on the next pass
            failed += 1
            continue
        with community._write_lock, community._connect() as conn:
            conn.execute(
                "INSERT INTO forum_reward_reports (actor_id, reported, reported_at) VALUES (?, ?, ?) "
                "ON CONFLICT(actor_id) DO UPDATE SET reported = excluded.reported, "
                "reported_at = excluded.reported_at",
                (actor, count, timestamp),
            )
        reported += 1
    return {"refreshed": len(actors), "reported": reported, "report_failed": failed, "rings": rings}


# ── hooks ───────────────────────────────────────────────────────────────


def _level_at_least(minimum: int):
    def check(conn: sqlite3.Connection, actor: str) -> bool:
        return level_of(conn, actor)[0] >= minimum

    return check


def _voter_weight(conn: sqlite3.Connection, actor: str) -> float:
    return community_labels.TRUSTED_WEIGHT if level_of(conn, actor)[0] >= 3 else 1.0


def _on_event(event: str, payload: dict) -> None:
    targets: list[str] = []
    if event in {"answer_accepted", "answer_liked", "post_liked", "answer_verified"}:
        targets = [payload.get("author_id")]
    elif event == "post_created":
        targets = [payload.get("actor_id")]
    for actor in targets:
        if actor:
            refresh(str(actor))


def _purge_actor(conn: sqlite3.Connection, actor: str) -> None:
    for table in ("forum_achievements", "forum_reward_freezes", "forum_reward_reports"):
        try:
            conn.execute(f"DELETE FROM {table} WHERE actor_id = ?", (actor,))  # nosec B608
        except sqlite3.OperationalError:
            continue


def install_hooks() -> None:
    """Wire levels into the forum, moderation and label weighting. Idempotent."""
    try:
        from . import community_moderation  # type: ignore[import-not-found]
    except ImportError:
        import community_moderation
    community_forum.can_mark_not_helpful = _level_at_least(2)
    community_moderation.reporter_counts = _level_at_least(2)
    community_labels.voter_weight = _voter_weight
    if _on_event not in community_forum.event_listeners:
        community_forum.event_listeners.append(_on_event)


community.register_actor_hooks(purge=_purge_actor)
