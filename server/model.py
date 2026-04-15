"""
Backward-compatibility shim.

The real logic has moved to the two-stage pipeline:
    detector.py   — Stage 1 (region detection)
    classifier.py — Stage 2 (disease classification)
    pipeline.py   — orchestrator

This module re-exports the pipeline so old imports keep working.
"""

from pipeline import PlantDiseasePipeline  # noqa: F401
from classifier import CLASS_NAMES  # noqa: F401
