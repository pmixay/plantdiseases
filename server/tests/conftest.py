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

import base64
import io
import json
import os
import shutil
import sys
import tempfile
import uuid
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


def _test_access_token(identity: str, nickname: str | None = None) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps([identity, nickname or "Test Fern"], ensure_ascii=False).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    return f"test.{encoded}.signature"


class _TestAccountAuthenticator:
    def authenticate(self, token: str):
        from account_auth import AccountAuthenticationError, AccountPrincipal

        try:
            prefix, encoded, signature = token.split(".")
            if prefix != "test":
                raise ValueError
            if signature != "signature":
                raise ValueError
            identity, nickname = json.loads(
                base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            )
            actor = uuid.uuid5(uuid.NAMESPACE_URL, f"test-account:{identity}")
            session = uuid.uuid5(uuid.NAMESPACE_URL, f"test-session:{identity}")
            return AccountPrincipal(
                session_id=str(session),
                actor_id=str(actor),
                nickname=str(nickname),
                authorization_version=1,
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            raise AccountAuthenticationError from None


class _AccountAwareTestClientMixin:
    """Translate legacy test identity fields into signed-test principals.

    Production handlers never inspect these fields; this adapter keeps older
    endpoint fixtures readable while exercising the new claim-derived path.
    """

    def request(self, method, url, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        request_path = str(url).split("?", 1)[0]
        if (
            request_path.startswith("/api/community/")
            and not any(name.lower() == "authorization" for name in headers)
        ):
            data = kwargs.get("data") if isinstance(kwargs.get("data"), dict) else {}
            body = kwargs.get("json") if isinstance(kwargs.get("json"), dict) else {}
            params = kwargs.get("params") if isinstance(kwargs.get("params"), dict) else {}
            identity = (
                data.get("user_pseudo_id")
                or body.get("user_pseudo_id")
                or params.get("user_pseudo_id")
                or "default-account"
            )
            nickname = data.get("nickname") or body.get("nickname") or "Test Fern"
            headers["Authorization"] = f"Bearer {_test_access_token(str(identity), str(nickname))}"
        return super().request(method, url, headers=headers, **kwargs)


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

    def detect(self, _img: Image.Image) -> dict:
        return {"detections": [], "confidence": 0.0, "primary_box": None}


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
    keep every persistent service hermetic: point its DB/directories at session
    temp paths so tests never write into ``server/`` or depend on the network.

    The production lifespan also warms curated Wikimedia photos.  That is a
    useful best-effort optimisation in a deployed service, but it would make
    the local suite depend on external HTTP and can extend ``TestClient``
    shutdown while worker calls time out. Replacing only that warm-up coroutine
    keeps HTTP contract tests deterministic while leaving production untouched.
    """
    import community
    import main as server_main
    import pipeline as pipeline_module
    import push
    import telemetry
    import training_store

    # Keep a handle on the real class before it is shadowed. Swapping in the
    # fake makes the genuine pipeline unreachable for the whole session, so a
    # test that wants to exercise real gate plumbing (rather than the HTTP
    # layer) has nothing to instantiate. Tests use
    # ``pipeline._RealPipelineForTests``; nothing in production reads it.
    pipeline_module._RealPipelineForTests = pipeline_module.PlantDiseasePipeline
    server_main.PlantDiseasePipeline = FakePipeline  # type: ignore[assignment]
    pipeline_module.PlantDiseasePipeline = FakePipeline  # type: ignore[assignment]
    server_main.account_authenticator = _TestAccountAuthenticator()

    state_dir = tmp_path_factory.mktemp("server-state")

    training_store.TRAINING_DB_PATH = state_dir / "training.db"
    training_store.TRAINING_IMAGE_DIR = state_dir / "training-images"
    training_store._initialised = False

    community.COMMUNITY_DB_PATH = state_dir / "community.db"
    community.COMMUNITY_THUMB_DIR = state_dir / "community-thumbs"
    community._initialised = False
    # main imports this path by value for the thumbnail response endpoint.
    server_main.COMMUNITY_THUMB_DIR = community.COMMUNITY_THUMB_DIR

    # Same story for the proxied-photo cache: a route that fetches one would
    # otherwise create ``server/plant_photo_cache/`` in the working tree. The
    # env var matters because a per-test ``importlib.reload`` re-reads it.
    import plant_photos
    plant_photos.PHOTO_CACHE_DIR = state_dir / "plant-photos"
    os.environ["PLANT_PHOTO_CACHE_DIR"] = str(plant_photos.PHOTO_CACHE_DIR)

    telemetry.TELEMETRY_DB_PATH = state_dir / "telemetry.db"
    telemetry._initialised = False

    push.PUSH_DB_PATH = state_dir / "push.db"
    push._initialised = False

    async def _skip_external_warmup() -> None:
        return None

    server_main._warm_placeholder_images = _skip_external_warmup
    # /api/metrics shares its store aggregates for a few seconds in production.
    # Tests read their own writes back through it, so the snapshot is off here;
    # the caching itself is tested with the TTL switched back on.
    server_main.METRICS_SNAPSHOT_TTL_SECONDS = 0.0
    server_main._daily_write_counts.clear()
    yield


# ── Community store isolation ───────────────────────────────────────


@pytest.fixture
def community_store(tmp_path, monkeypatch):
    """A private, empty community store for one test.

    Redirects the store's paths and re-arms its lazy schema init; monkeypatch
    puts all of it back afterwards, so the next test is on the session store
    again. Deliberately no ``importlib.reload(community)``: a reload swaps in
    new exception classes and function objects, which every fixture then had
    to re-bind on ``main`` by hand (some thirty names) and nothing restored.
    """
    import community
    import main as server_main

    monkeypatch.setattr(community, "COMMUNITY_DB_PATH", tmp_path / "community.db")
    monkeypatch.setattr(community, "COMMUNITY_THUMB_DIR", tmp_path / "thumbs")
    monkeypatch.setattr(community, "_initialised", False)
    monkeypatch.setattr(community, "_last_expiry_sweep", 0.0)
    # main imports this path by value for the thumbnail response endpoint.
    monkeypatch.setattr(server_main, "COMMUNITY_THUMB_DIR", tmp_path / "thumbs")
    return community


@pytest.fixture
def community_http_store(community_store, monkeypatch):
    """``community_store`` for HTTP tests that post synthetic scan ids.

    ``/api/community/scan`` publishes a scan the server itself analysed: it
    needs a server-issued analysis token and takes disease and species from
    that record. These tests post made-up ids with client-supplied fields, so
    every id counts as issued and the write goes through ``record_scan`` with
    the client's values. The rejection paths are tested explicitly elsewhere.
    """
    import main as server_main

    comm = community_store

    def legacy_analysis_write(**kwargs):
        return comm.record_scan(
            scan_id=kwargs["scan_id"],
            account_actor_id=kwargs["account_actor_id"],
            country=kwargs["country"],
            region=kwargs.get("region", ""),
            city=kwargs.get("city"),
            lat=kwargs.get("lat"),
            lon=kwargs.get("lon"),
            disease=kwargs.get("client_disease") or "healthy",
            confidence=kwargs.get("client_confidence"),
            species=kwargs.get("client_species"),
            species_score=kwargs.get("client_species_score"),
            image_bytes=kwargs.get("image_bytes"),
            locale=kwargs.get("locale"),
            nickname=kwargs.get("nickname"),
        )

    monkeypatch.setattr(server_main, "community_record_analysis_scan", legacy_analysis_write)
    monkeypatch.setattr(server_main, "community_analysis_token_issued", lambda *a, **k: True)
    server_main._daily_write_counts.clear()
    return comm


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

    class AccountAwareTestClient(_AccountAwareTestClientMixin, TestClient):
        pass

    import main as server_main

    with AccountAwareTestClient(server_main.app) as c:
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
        with server_main._guest_feature_lock:
            server_main._guest_feature_counts.clear()
            server_main._guest_feature_receipts.clear()
        with server_main._quota_lock:
            server_main._in_memory_quotas.clear()
            server_main._api_key_feature_receipts.clear()
        yield c


@pytest.fixture
def rate_limited_client():
    """Variant of ``client`` that keeps the rate limiter active and strict.

    Capacity is pinned to 1.0 and the bucket refills one token a MINUTE, so
    the limiter reduces to "one request, then 429" for the length of a test.
    The refill used to be one token a second, which made every test here a
    race against the wall clock: on a loaded CI runner (the suite runs in
    parallel) the first request could take over a second, the bucket refilled
    and the second request was served instead of throttled. Production keeps
    its own rate and the larger default burst.
    """
    from fastapi.testclient import TestClient

    class AccountAwareTestClient(_AccountAwareTestClientMixin, TestClient):
        pass

    import main as server_main

    with AccountAwareTestClient(server_main.app) as c:
        mw = _find_rate_limit_middleware(server_main.app)
        if mw is not None:
            mw.rate = 1.0 / 60.0
            mw.capacity = 1.0
            with mw._lock:
                mw._buckets.clear()
        with server_main._guest_feature_lock:
            server_main._guest_feature_counts.clear()
            server_main._guest_feature_receipts.clear()
        with server_main._quota_lock:
            server_main._in_memory_quotas.clear()
            server_main._api_key_feature_receipts.clear()
        yield c
        if mw is not None:
            with mw._lock:
                mw._buckets.clear()
