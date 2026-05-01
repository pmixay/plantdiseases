"""Shared fixtures for the server test suite.

The server module imports (`main`, `pipeline`, ...) use top-level imports
that expect the `server/` directory to be on ``sys.path``, so we prepend
it here before any test collects. We also disable the rate limiter at
process level (it is exercised explicitly in ``test_middleware.py``).

Production refuses to start without trained weights. Tests don't ship
weights, so we replace the pipeline class with a small deterministic
stub that satisfies the same interface.
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


# ── Deterministic stub pipeline for HTTP-level tests ─────────────────


class _StubClassifier:
    """Minimal classifier surface — class list + backend identifier."""

    def __init__(self) -> None:
        from classifier import DEFAULT_CLASS_NAMES

        self.CLASS_NAMES = list(DEFAULT_CLASS_NAMES)
        self.NUM_CLASSES = len(self.CLASS_NAMES)
        self.img_size = 240
        self.backend = "stub"

    @property
    def is_loaded(self) -> bool:
        return True


class _StubDetector:
    @property
    def is_loaded(self) -> bool:
        return True


class StubPipeline:
    """Deterministic pipeline stub used by the test client.

    Returns a fixed "healthy" diagnosis. Tests that care about routing
    logic exercise the pure post-processing helpers in ``pipeline.py``
    directly without going through the HTTP layer.
    """

    def __init__(self, *_args, **_kwargs) -> None:
        self.detector = _StubDetector()
        self.classifier = _StubClassifier()
        self.temperature = 1.0

    @property
    def mode(self) -> str:
        return "full"

    def analyze(self, img: Image.Image) -> dict:
        names = self.classifier.CLASS_NAMES
        # Default to "healthy" with a comfortable margin so the
        # postprocessing soft-warnings stay quiet on the contract tests.
        top_prob = 0.9
        leftover_each = (1.0 - top_prob) / max(1, len(names) - 1)
        probs = {n: leftover_each for n in names}
        if "healthy" in probs:
            probs["healthy"] = top_prob
        ordered = sorted(probs.items(), key=lambda kv: -kv[1])[:3]
        top_k = [{"class": n, "confidence": round(v, 4)} for n, v in ordered]
        return {
            "class_name": "healthy",
            "confidence": top_prob,
            "all_probs": probs,
            "top_k": top_k,
            "warnings": [],
            "severity": "low",
            "detection": {
                "is_diseased": False,
                "detector_confidence": 0.9,
                "regions": [],
                "primary_region": None,
            },
            "pipeline_mode": "full",
            "elapsed_ms": 1.0,
        }


@pytest.fixture(autouse=True)
def _stub_pipeline(monkeypatch):
    """Replace the strict production pipeline with a deterministic stub
    for the lifespan of the test client. Applied automatically — tests
    that need a different pipeline can override
    ``main.PlantDiseasePipeline`` after this fixture runs.
    """
    import main as server_main

    monkeypatch.setattr(server_main, "PlantDiseasePipeline", StubPipeline)


# ── Image fixtures ───────────────────────────────────────────────────


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
    """A brown-toned image; legacy fixture preserved for symmetry."""
    img = Image.new("RGB", (128, 128), color=(130, 80, 40))
    return _encode_image(img, "PNG")


# ── TestClient fixtures ──────────────────────────────────────────────


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
