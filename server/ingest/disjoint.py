"""Source-disjointness gate — eval-leakage prevention (EVAL_DATASET_PLAN.md §3-A).

Scraped candidates must NOT originate from any source already in the training
corpus, or a held-out image could secretly be a training image and inflate the
ship-gate metric. Reject on any match before insert.
"""

from __future__ import annotations

# Hosts / slug fragments that appear in the training corpus.
_BANNED = (
    "inaturalist", "static.inaturalist", "inat.",
    "plantdoc",
    "plantvillage",
    "kaggle.com", "kagglehub", "storage.googleapis.com/kaggle",
    # named training-source slugs (EVAL_DATASET_PLAN §3-A)
    "abdulahad0296", "pappu54", "usmanafzaal", "guy007", "alexanderuzhinskiy",
    # maintainer asked to skip wikimedia/wikipedia (often inaccurate labels)
    "wikimedia.org", "wikipedia.org",
)


def is_disjoint(source_url: str | None) -> bool:
    """True if the URL is safe to ingest (not from a training/excluded source)."""
    if not source_url:
        return True
    u = source_url.lower()
    return not any(b in u for b in _BANNED)
