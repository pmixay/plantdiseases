"""Shared fixtures for the server test suite.

The server module imports (`main`, `pipeline`, ...) use top-level imports
that expect the `server/` directory to be on ``sys.path``, so we prepend
it here before any test collects. We also disable the rate limiter at
process level (it is exercised explicitly in ``test_middleware.py``).
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest
from PIL import Image

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

# Make sure main.py reads a permissive rate limit on import. Tests that
# want to exercise the 429 path re-tune the middleware through the
# ``_rate_limit`` fixture below.
os.environ.setdefault("RATE_LIMIT_RPS", "1000")


def _encode_image(img: Image.Image, fmt: str) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def png_bytes() -> bytes:
    """A small solid-green PNG — a valid image without any disease cues."""
    img = Image.new("RGB", (64, 64), color=(40, 150, 60))
    return _encode_image(img, "PNG")


@pytest.fixture
def jpeg_bytes() -> bytes:
    img = Image.new("RGB", (64, 64), color=(160, 80, 40))
    return _encode_image(img, "JPEG")


@pytest.fixture
def brown_png_bytes() -> bytes:
    """A brown-toned image — the demo detector should flag this as diseased."""
    img = Image.new("RGB", (128, 128), color=(130, 80, 40))
    return _encode_image(img, "PNG")


def _find_rate_limit_middleware(app):
    """Walk the built middleware stack to find the RateLimitMiddleware instance."""
    from main import RateLimitMiddleware  # local import to respect sys.path tweak

    node = app.middleware_stack
    while node is not None:
        if isinstance(node, RateLimitMiddleware):
            return node
        node = getattr(node, "app", None)
    return None


@pytest.fixture
def client():
    """FastAPI TestClient with the rate limiter effectively disabled.

    The TestClient context manager drives the lifespan, which builds
    the middleware stack — so we clear the limiter *after* __enter__.
    """
    from fastapi.testclient import TestClient

    import main as server_main

    with TestClient(server_main.app) as c:
        mw = _find_rate_limit_middleware(server_main.app)
        if mw is not None:
            mw.min_interval = 0.0
            with mw._lock:
                mw._last_request.clear()
        yield c


@pytest.fixture
def rate_limited_client():
    """Variant of ``client`` that keeps a 1 req/s rate limit active."""
    from fastapi.testclient import TestClient

    import main as server_main

    with TestClient(server_main.app) as c:
        mw = _find_rate_limit_middleware(server_main.app)
        if mw is not None:
            mw.min_interval = 1.0
            with mw._lock:
                mw._last_request.clear()
        yield c
        if mw is not None:
            with mw._lock:
                mw._last_request.clear()
