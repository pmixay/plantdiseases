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
import sys
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
        if not any(name.lower() == "authorization" for name in headers):
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


# ── Fake pipeline (no weights required) ─────────────────────────────


class _FakeClassifier:
    backend = "fake"
    img_size = 224
    # Mirror the real classifier's metadata-driven ``architecture`` attr
    # (classes.json -> architecture) that the /version endpoint surfaces.
    architecture = "dinov2_vits14+mlp_head"
    # Capacity tier (classes.json -> model_family), surfaced by /version.
    model_family = "gamma"

    def __init__(self):
        from classifier import DEFAULT_CLASS_NAMES

        self.CLASS_NAMES = list(DEFAULT_CLASS_NAMES)
        self.NUM_CLASSES = len(self.CLASS_NAMES)


class _FakeDetector:
    is_loaded = True


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
    import feedback
    import labeling
    import main as server_main
    import pipeline as pipeline_module
    import push
    import telemetry

    server_main.PlantDiseasePipeline = FakePipeline  # type: ignore[assignment]
    pipeline_module.PlantDiseasePipeline = FakePipeline  # type: ignore[assignment]
    server_main.account_authenticator = _TestAccountAuthenticator()

    state_dir = tmp_path_factory.mktemp("server-state")

    feedback.FEEDBACK_DB_PATH = state_dir / "feedback.db"
    feedback.FEEDBACK_IMG_DIR = state_dir / "feedback-images"
    feedback._initialised = False

    community.COMMUNITY_DB_PATH = state_dir / "community.db"
    community.COMMUNITY_THUMB_DIR = state_dir / "community-thumbs"
    community._initialised = False
    # main imports this path by value for the thumbnail response endpoint.
    server_main.COMMUNITY_THUMB_DIR = community.COMMUNITY_THUMB_DIR

    labeling.LABELING_DB_PATH = state_dir / "labeling.db"
    labeling.LABELING_IMG_DIR = state_dir / "labeling-images"
    labeling._initialised = False

    telemetry.TELEMETRY_DB_PATH = state_dir / "telemetry.db"
    telemetry._initialised = False

    push.PUSH_DB_PATH = state_dir / "push.db"
    push._initialised = False

    async def _skip_external_warmup() -> None:
        return None

    server_main._warm_placeholder_images = _skip_external_warmup
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

    class AccountAwareTestClient(_AccountAwareTestClientMixin, TestClient):
        pass

    import main as server_main

    with AccountAwareTestClient(server_main.app) as c:
        mw = _find_rate_limit_middleware(server_main.app)
        if mw is not None:
            mw.min_interval = 0.0
            with mw._lock:
                mw._last_request.clear()
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

    Capacity is pinned to 1.0 so the token-bucket reduces to the
    strict "min_interval between requests" behaviour the rate-limit
    test asserts. Production keeps the larger default burst.
    """
    from fastapi.testclient import TestClient

    class AccountAwareTestClient(_AccountAwareTestClientMixin, TestClient):
        pass

    import main as server_main

    with AccountAwareTestClient(server_main.app) as c:
        mw = _find_rate_limit_middleware(server_main.app)
        if mw is not None:
            mw.min_interval = 1.0
            mw.capacity = 1.0
            with mw._lock:
                mw._last_request.clear()
        yield c
        if mw is not None:
            with mw._lock:
                mw._last_request.clear()
