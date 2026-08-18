"""Fail-closed switches used only by the synthetic account-beta stack."""

from __future__ import annotations


def allow_synthetic_pipeline(
    *,
    environment: str,
    requested: bool,
    synthetic_staging_acknowledged: bool = False,
) -> bool:
    """Permit weight-free inference only in test or acknowledged staging."""
    if not requested:
        return False
    normalized = environment.strip().lower()
    if normalized == "test":
        return True
    if normalized == "staging" and synthetic_staging_acknowledged:
        return True
    raise RuntimeError(
        "Synthetic inference requires ENVIRONMENT=test or an explicitly "
        "acknowledged synthetic-only staging environment"
    )
