"""Candidate-image ingestion for the data-labeling tool.

Kaggle-only: the maintainer's offline collection workflow owns scraping,
source-disjointness and vision-agent judging (documented in
``docs/DATASET_LABELING_PLAN.md``); this package just pulls the published
dataset, gates it (image quality + dedup) and model-routes the candidates.
"""

from .pipeline import run_ingestion  # noqa: F401
from .sources import dataset_version  # noqa: F401
