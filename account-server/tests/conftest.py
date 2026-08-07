"""Synthetic, fail-closed settings shared by account-api tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ACCOUNT_SERVER_DIR = Path(__file__).resolve().parent.parent
if str(ACCOUNT_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(ACCOUNT_SERVER_DIR))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("AUTH_PROVIDER_MODE", "mock")
os.environ.setdefault("ACCOUNT_ISSUER", "http://beta-account-api:8000")
os.environ.setdefault("ACCOUNT_AUDIENCE", "plantdiseases-app")
os.environ.setdefault(
    "ACCOUNT_DATABASE_URL",
    "postgresql+psycopg://beta:synthetic-test-only@beta-postgres/plantdisease_beta",
)
