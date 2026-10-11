"""Shared fixtures for the server test suite.

The server module imports (`main`, `pipeline`, ...) use top-level imports
that expect the `server/` directory to be on ``sys.path``, so we prepend
it here before any test collects. We also disable the rate limiter at
process level (it is exercised explicitly in ``test_middleware.py``).

The production server is fail-fast: it refuses to start without trained
weights. Tests would never have weights available, so we substitute a
``FakePipeline`` that mirrors the real pipeline's response shape using
deterministic dummy probabilities. The fake honours the canonical class
list so all contract tests still bite.
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from PIL import Image

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

# Make sure main.py reads a permissive rate limit on import. Tests that
# want to exercise the 429 path re-tune the middleware through the
# ``rate_limited_client`` fixture below.
os.environ.setdefault("RATE_LIMIT_RPS", "1000")

# Redirect the AI disk caches (species profile/diseases, care-plan polish) at a
# throwaway directory. They default to ``server/ai_cache/``, so without this a
# test that exercises a real polish/lookup path writes into the working tree —
# and, worse, the entry it leaves behind is a cache HIT for the next run, which
# turns "did this call OpenRouter?" tests into order-dependent flakes. Set
# before any test module imports ai_services (it reads the dir at import).
_AI_CACHE_TMPDIR = tempfile.mkdtemp(prefix="plantvision-ai-cache-")
os.environ.setdefault("AI_CACHE_DIR", _AI_CACHE_TMPDIR)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_ai_cache_tmpdir():
    yield
    shutil.rmtree(_AI_CACHE_TMPDIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_abuse_counters():
    """Start every test with empty daily quotas and no held inference slots.

    The suite posts hundreds of scans from one TestClient peer, so the
    per-IP daily quotas would otherwise trip partway through the run.
    """
    import main as server_main

    for quota in server_main._DAILY_QUOTAS:
        quota.reset()
    with server_main._admission_lock:
        server_main._inference_inflight = 0
        server_main._inference_inflight_by_ip.clear()
        server_main._inference_busy_rejections = 0
    yield


def _encode_image(img: Image.Image, fmt: str) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def png_bytes() -> bytes:
    """A small solid-green PNG — a valid image without any disease cues."""
    img = Image.new("RGB", (64, 64), color=(40, 150, 60))
    return _encode_image(img, "PNG")


# ── Fake pipeline (no weights required) ─────────────────────────────


class _FakeClassifier:
    backend = "fake"
    img_size = 224
    # Mirror the real classifier's metadata-driven ``architecture`` attr
    # (classes.json -> architecture) that /api/capabilities surfaces.
    architecture = "dinov2_vits14+mlp_head"
    # Capacity tier (classes.json -> model_family), also surfaced by
    # /api/capabilities.
    model_family = "gamma"

    def __init__(self):
        from classifier import DEFAULT_CLASS_NAMES

        self.CLASS_NAMES = list(DEFAULT_CLASS_NAMES)
        self.NUM_CLASSES = len(self.CLASS_NAMES)


class _FakeDetector:
    """Stand-in for ``LeafDetector``. The real one carries a loaded ONNX
    session; nothing above the pipeline reads either attribute."""

    model = None
    input_name = ""


class FakePipeline:
    """Minimal stand-in for ``PlantDiseasePipeline`` used in tests.

    Returns the same response shape as the real pipeline but always
    classifies as ``healthy`` with low confidence. Tests that exercise
    behaviour beyond the contract patch ``main.pipeline`` directly.
    """

    def __init__(self, *args, **kwargs):
        # Accept (and ignore) the same kwargs as the real pipeline so
        # the lifespan handler can construct us without special-casing.
        self.classifier = _FakeClassifier()
        self.detector = _FakeDetector()

    def analyze(self, img: Image.Image) -> dict:
        names = self.classifier.CLASS_NAMES
        flat = 1.0 / len(names)
        probs = {n: round(flat, 4) for n in names}
        # Bias healthy slightly so verdicts are deterministic.
        probs["healthy"] = round(flat + 0.1, 4)
        s = sum(probs.values())
        probs = {n: round(p / s, 4) for n, p in probs.items()}
        top_k = sorted(probs.items(), key=lambda kv: -kv[1])[:3]
        return {
            "class_name": "healthy",
            "confidence": probs["healthy"],
            "all_probs": probs,
            "top_k": [{"class": n, "confidence": p} for n, p in top_k],
            "warnings": [],
            "severity": "low",
            "prediction_set": None,
            "detection": {
                "is_diseased": False,
                "detector_confidence": 0.0,
                "regions": [],
                "primary_region": None,
            },
            "elapsed_ms": 0.0,
        }


@pytest.fixture(autouse=True, scope="session")
def _stub_pipeline_for_lifespan(tmp_path_factory):
    """Replace ``PlantDiseasePipeline`` with the fake before lifespan runs, and
    keep every on-disk store hermetic: point the labeling, feedback, community,
    photo-cache and telemetry DBs/dirs at session tmp dirs so tests never write
    into ``server/``.

    Redirection is done twice on purpose. The module globals are what the
    ``_connect()`` helpers read at call time, and the matching env vars are what
    a per-test ``importlib.reload`` re-reads — without them a reloaded module
    would snap back to ``server/feedback.db`` mid-suite.
    """
    import community
    import feedback
    import labeling
    import main as server_main
    import pipeline as pipeline_module

    # Keep a handle on the real class before it is shadowed. Swapping in the
    # fake makes the genuine pipeline unreachable for the whole session, so a
    # test that wants to exercise real gate plumbing (rather than the HTTP
    # layer) has nothing to instantiate. Tests use
    # ``pipeline._RealPipelineForTests``; nothing in production reads it.
    pipeline_module._RealPipelineForTests = pipeline_module.PlantDiseasePipeline
    server_main.PlantDiseasePipeline = FakePipeline  # type: ignore[assignment]
    pipeline_module.PlantDiseasePipeline = FakePipeline  # type: ignore[assignment]

    lab_dir = tmp_path_factory.mktemp("labeling")
    labeling.LABELING_DB_PATH = lab_dir / "labeling.db"
    labeling.LABELING_IMG_DIR = lab_dir / "images"
    labeling._initialised = False

    # /api/analyze records every issued scan_id, which also mints
    # ``.ip_hash_pepper`` beside the feedback DB — both landed in the working
    # tree before this redirect.
    fb_dir = tmp_path_factory.mktemp("feedback")
    feedback.FEEDBACK_DB_PATH = fb_dir / "feedback.db"
    feedback.FEEDBACK_IMG_DIR = fb_dir / "images"
    feedback._initialised = False
    os.environ["FEEDBACK_DB_PATH"] = str(feedback.FEEDBACK_DB_PATH)
    os.environ["FEEDBACK_IMG_DIR"] = str(feedback.FEEDBACK_IMG_DIR)

    comm_dir = tmp_path_factory.mktemp("community")
    community.COMMUNITY_DB_PATH = comm_dir / "community.db"
    community.COMMUNITY_THUMB_DIR = comm_dir / "thumbs"
    community._initialised = False
    os.environ["COMMUNITY_DB_PATH"] = str(community.COMMUNITY_DB_PATH)
    os.environ["COMMUNITY_THUMB_DIR"] = str(community.COMMUNITY_THUMB_DIR)
    # main.py imported the thumb dir by value at module load, so the /api route
    # that serves thumbnails needs its own copy re-pointed too.
    server_main.COMMUNITY_THUMB_DIR = community.COMMUNITY_THUMB_DIR

    # Same story for the proxied-photo cache: a route that fetches one creates
    # ``server/plant_photo_cache/`` in the working tree.
    import plant_photos
    photo_dir = tmp_path_factory.mktemp("plant-photos")
    plant_photos.PHOTO_CACHE_DIR = photo_dir
    os.environ["PLANT_PHOTO_CACHE_DIR"] = str(photo_dir)

    import telemetry
    telemetry.TELEMETRY_DB_PATH = tmp_path_factory.mktemp("telemetry") / "telemetry.db"
    telemetry._initialised = False
    yield


# ── HTTP test clients ───────────────────────────────────────────────


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
            mw.rate = float("inf")
            with mw._lock:
                mw._buckets.clear()
        # The same-photo dedup cache leaks state across tests because
        # the test PNG bytes hash to a stable SHA-256 — without this
        # reset, a confident-pipeline test gets the unconfident response
        # the previous test cached for the identical bytes. Clearing
        # at fixture entry is enough; tests inside the same fixture run
        # are already isolated by ``client`` being function-scoped.
        with server_main._analyze_cache_lock:
            server_main._analyze_cache.clear()
        yield c


@pytest.fixture
def rate_limited_client():
    """Variant of ``client`` that keeps a 1 req/s rate limit active.

    Capacity is pinned to 1.0 so the token-bucket reduces to the strict
    "one request per second, no burst" behaviour the rate-limit test
    asserts. Production keeps the larger default burst.
    """
    from fastapi.testclient import TestClient

    import main as server_main

    with TestClient(server_main.app) as c:
        mw = _find_rate_limit_middleware(server_main.app)
        if mw is not None:
            mw.rate = 1.0
            mw.capacity = 1.0
            with mw._lock:
                mw._buckets.clear()
        yield c
        if mw is not None:
            with mw._lock:
                mw._buckets.clear()
