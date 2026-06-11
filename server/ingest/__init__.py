"""Candidate-image ingestion for the data-labeling tool.

Re-implements (does NOT import) the training notebook's source-probing
strategies as a clean, importable package: HTTP helpers with backoff, image
validation + dedup, source-disjointness (eval-leakage gate), per-source probes
and the deficit-driven ingestion pipeline.
"""

from .pipeline import run_ingestion  # noqa: F401
