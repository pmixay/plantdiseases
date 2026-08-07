"""Fail-closed switches used only by the synthetic account-beta stack."""

from __future__ import annotations


def allow_synthetic_pipeline(*, environment: str, requested: bool) -> bool:
    """Permit weight-free inference only in an explicit test environment."""
    if not requested:
        return False
    if environment.strip().lower() != "test":
        raise RuntimeError(
            "ACCOUNT_BETA_SYNTHETIC_PIPELINE is allowed only when ENVIRONMENT=test"
        )
    return True
