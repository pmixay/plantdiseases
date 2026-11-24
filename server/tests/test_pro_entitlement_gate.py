from __future__ import annotations

import uuid

import httpx2
import pytest

import main as server_main
from account_entitlements import EntitlementDecision
from tests.conftest import FakePipeline, _test_access_token


class _AllowingEntitlements:
    def __init__(self):
        self.calls = []

    def consume(self, *, actor_id, feature, request_id):
        self.calls.append((actor_id, feature, request_id))
        return EntitlementDecision(
            allowed=True,
            replayed=False,
            plan="free",
            feature=feature,
            limit=5,
            remaining=4,
            reset_at="2026-08-27T00:00:00Z",
        )


class _ReleasingEntitlements:
    def __init__(self):
        self.consumes = []
        self.releases = []

    def consume(self, *, actor_id, feature, request_id):
        self.consumes.append((actor_id, feature, request_id))
        return EntitlementDecision(
            allowed=True,
            replayed=False,
            plan="pro",
            feature=feature,
            limit=20,
            remaining=19,
            reset_at="2026-08-30T00:00:00Z",
            policy_version="pro-launch-v1",
        )

    def release(self, *, actor_id, feature, request_id):
        self.releases.append((actor_id, feature, request_id))
        return EntitlementDecision(
            allowed=True,
            replayed=True,
            plan="pro",
            feature=feature,
            limit=20,
            remaining=19,
            reset_at="2026-08-30T00:00:00Z",
            policy_version="pro-launch-v1",
            released=True,
        )


def _other_png(seed: int) -> bytes:
    """A valid PNG whose bytes differ from ``png_bytes`` (no cache hit)."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=(40, 150, (60 + seed) % 256)).save(buf, format="PNG")
    return buf.getvalue()


def _post_analyze(client, png_bytes, *, headers=None, family="gamma"):
    return client.post(
        "/api/analyze",
        params={"family": family},
        files={"image": ("leaf.png", png_bytes, "image/png")},
        headers=headers or {},
    )


def test_guest_gamma_has_a_useful_but_bounded_allowance(client, png_bytes):
    original = server_main._GUEST_FEATURE_LIMITS["gamma_analysis"]
    server_main._GUEST_FEATURE_LIMITS["gamma_analysis"] = 1
    try:
        first = _post_analyze(client, png_bytes)
        second = _post_analyze(client, _other_png(1))
    finally:
        server_main._GUEST_FEATURE_LIMITS["gamma_analysis"] = original

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"detail": "quota_exhausted"}
    assert second.headers["x-quota-limit"] == "1"
    assert second.headers["x-quota-remaining"] == "0"


def test_repeated_photo_is_served_from_cache_without_spending_allowance(client, png_bytes):
    original = server_main._GUEST_FEATURE_LIMITS["gamma_analysis"]
    server_main._GUEST_FEATURE_LIMITS["gamma_analysis"] = 1
    try:
        first = _post_analyze(client, png_bytes)
        repeat = _post_analyze(client, png_bytes)
    finally:
        server_main._GUEST_FEATURE_LIMITS["gamma_analysis"] = original

    assert first.status_code == 200
    assert repeat.status_code == 200
    assert repeat.json()["is_repeat"] is True
    assert sum(server_main._guest_feature_counts.values()) == 1


def test_rejected_photo_does_not_spend_allowance(client, png_bytes):
    response = client.post(
        "/api/analyze",
        files={"image": ("leaf.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 400
    assert not server_main._guest_feature_counts
    assert _post_analyze(client, png_bytes).status_code == 200


def test_guest_retry_with_same_idempotency_key_cannot_rerun_inference(client, png_bytes):
    original = server_main._GUEST_FEATURE_LIMITS["gamma_analysis"]
    server_main._GUEST_FEATURE_LIMITS["gamma_analysis"] = 1
    operation_id = str(uuid.uuid4())
    try:
        first = _post_analyze(
            client,
            png_bytes,
            headers={"X-Idempotency-Key": operation_id},
        )
        # Different bytes, so the retry misses the same-photo cache and would
        # need fresh inference.
        retry = _post_analyze(
            client,
            _other_png(2),
            headers={"X-Idempotency-Key": operation_id},
        )
    finally:
        server_main._GUEST_FEATURE_LIMITS["gamma_analysis"] = original

    assert first.status_code == 200
    assert retry.status_code == 409
    assert retry.json() == {"detail": "operation_already_consumed"}
    assert sum(server_main._guest_feature_counts.values()) == 1


def test_signed_in_usage_is_authorized_by_account_service(client, png_bytes):
    import account_entitlements
    fake = _AllowingEntitlements()
    original = account_entitlements.account_entitlement_client
    account_entitlements.account_entitlement_client = fake
    operation_id = str(uuid.uuid4())
    try:
        response = _post_analyze(
            client,
            png_bytes,
            headers={
                "Authorization": f"Bearer {_test_access_token('pro-gate')}",
                "X-Idempotency-Key": operation_id,
            },
        )
    finally:
        account_entitlements.account_entitlement_client = original

    assert response.status_code == 200
    assert len(fake.calls) == 1
    actor_id, feature, request_id = fake.calls[0]
    assert str(uuid.UUID(actor_id)) == actor_id
    assert feature == "gamma_analysis"
    assert request_id == operation_id


def test_valid_legacy_api_key_uses_its_own_daily_quota_and_replay_guard(
    client,
    png_bytes,
    monkeypatch,
):
    key = "legacy-integration-test-key"
    monkeypatch.setattr(
        server_main,
        "API_KEYS",
        {key: {"quota_daily": 1, "rate_limit_rps": 0}},
    )
    operation_id = str(uuid.uuid4())
    headers = {
        "X-API-Key": key,
        "X-Idempotency-Key": operation_id,
    }

    first = _post_analyze(client, png_bytes, headers=headers)
    replay = _post_analyze(client, _other_png(3), headers=headers)
    exhausted = _post_analyze(
        client,
        _other_png(4),
        headers={
            "X-API-Key": key,
            "X-Idempotency-Key": str(uuid.uuid4()),
        },
    )

    assert first.status_code == 200
    assert replay.status_code == 409
    assert replay.json() == {"detail": "operation_already_consumed"}
    assert exhausted.status_code == 429
    assert exhausted.json() == {"detail": "quota_exhausted"}
    assert exhausted.headers["x-quota-limit"] == "1"
    assert not server_main._guest_feature_counts


def test_guest_gets_one_frontier_analysis_when_the_operator_serves_it(client, png_bytes):
    frontier = FakePipeline()
    frontier.classifier.model_family = "frontier"
    original = server_main.pipeline_frontier
    server_main.pipeline_frontier = frontier
    try:
        first = _post_analyze(client, png_bytes, family="frontier")
        repeat = _post_analyze(client, png_bytes, family="frontier")
        second = _post_analyze(client, _other_png(5), family="frontier")
    finally:
        server_main.pipeline_frontier = original

    assert first.status_code == 200
    assert repeat.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"detail": "quota_exhausted"}
    assert second.headers["x-quota-limit"] == "1"


def test_unconfigured_plantnet_does_not_spend_identification_allowance(client, png_bytes):
    response = client.post(
        "/api/identify",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["available"] is False
    assert not server_main._guest_feature_counts


def test_identify_budget_denial_releases_exact_signed_in_allowance(
    client,
    png_bytes,
    monkeypatch,
):
    import account_entitlements
    import plantnet
    fake = _ReleasingEntitlements()
    operation_id = str(uuid.uuid4())
    monkeypatch.setattr(account_entitlements, "account_entitlement_client", fake)
    monkeypatch.setattr(plantnet, "plantnet_configured", lambda: True)
    monkeypatch.setattr(plantnet, "plantnet_cache_lookup", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        plantnet, "identify_species",
        lambda *_a, **_kw: {
            "available": False,
            "source": "budget_exhausted",
            "results": [],
            "_upstream_attempted": False,
        },
    )

    response = client.post(
        "/api/identify",
        files={"image": ("leaf.png", png_bytes, "image/png")},
        headers={
            "Authorization": f"Bearer {_test_access_token('budget-release')}",
            "X-Idempotency-Key": operation_id,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["source"] == "budget_exhausted"
    assert "_upstream_attempted" not in response.json()
    assert len(fake.consumes) == 1
    assert fake.releases == fake.consumes
    assert fake.releases[0][1:] == ("plant_identification", operation_id)


def test_guest_budget_denial_returns_unit_without_enabling_uuid_reuse(
    client,
    png_bytes,
    monkeypatch,
):
    import plantnet
    operation_id = str(uuid.uuid4())
    monkeypatch.setattr(plantnet, "plantnet_configured", lambda: True)
    monkeypatch.setattr(plantnet, "plantnet_cache_lookup", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        plantnet, "identify_species",
        lambda *_a, **_kw: {
            "available": False,
            "source": "budget_exhausted",
            "results": [],
            "_upstream_attempted": False,
        },
    )
    headers = {"X-Idempotency-Key": operation_id}

    first = client.post(
        "/api/identify",
        files={"image": ("leaf.png", png_bytes, "image/png")},
        headers=headers,
    )
    replay = client.post(
        "/api/identify",
        files={"image": ("leaf.png", png_bytes, "image/png")},
        headers=headers,
    )

    assert first.status_code == 200, first.text
    assert sum(server_main._guest_feature_counts.values()) == 0
    receipt = next(iter(server_main._guest_feature_receipts.values()))
    assert receipt.released is True
    assert replay.status_code == 409
    assert replay.json() == {"detail": "operation_already_consumed"}


def test_identify_provider_attempt_retains_signed_in_allowance(
    client,
    png_bytes,
    monkeypatch,
):
    import account_entitlements
    import plantnet
    fake = _ReleasingEntitlements()
    monkeypatch.setattr(account_entitlements, "account_entitlement_client", fake)
    monkeypatch.setattr(plantnet, "plantnet_configured", lambda: True)
    monkeypatch.setattr(plantnet, "plantnet_cache_lookup", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        plantnet, "identify_species",
        lambda *_a, **_kw: {
            "available": False,
            "source": "plantnet_error",
            "results": [],
            "_upstream_attempted": True,
        },
    )

    response = client.post(
        "/api/identify",
        files={"image": ("leaf.png", png_bytes, "image/png")},
        headers={
            "Authorization": f"Bearer {_test_access_token('attempt-retained')}",
            "X-Idempotency-Key": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 200, response.text
    assert len(fake.consumes) == 1
    assert fake.releases == []


def test_analyze_budget_denial_releases_only_optional_identification_allowance(
    client,
    png_bytes,
    monkeypatch,
):
    import account_entitlements
    import plantnet
    fake = _ReleasingEntitlements()
    operation_id = str(uuid.uuid4())
    monkeypatch.setattr(account_entitlements, "account_entitlement_client", fake)
    monkeypatch.setattr(plantnet, "plantnet_configured", lambda: True)
    monkeypatch.setattr(plantnet, "plantnet_cache_lookup", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        plantnet, "identify_species",
        lambda *_a, **_kw: {
            "available": False,
            "source": "budget_unavailable",
            "results": [],
            "_upstream_attempted": False,
        },
    )

    response = _post_analyze(
        client,
        png_bytes,
        headers={
            "Authorization": f"Bearer {_test_access_token('fanout-release')}",
            "X-Idempotency-Key": operation_id,
        },
    )

    assert response.status_code == 200, response.text
    assert [call[1] for call in fake.consumes] == [
        "gamma_analysis",
        "plant_identification",
    ]
    assert len(fake.releases) == 1
    assert fake.releases[0] == fake.consumes[1]
    assert fake.releases[0][2] == operation_id
    assert response.json()["species"]["source"] == "budget_unavailable"
    assert "_upstream_attempted" not in response.json()["species"]


def test_identify_concurrent_inner_cache_fill_releases_allowance(
    client,
    png_bytes,
    monkeypatch,
):
    import account_entitlements
    import plantnet

    fake = _ReleasingEntitlements()
    operation_id = str(uuid.uuid4())
    cached = {
        "available": True,
        "source": "plantnet",
        "results": [
            {
                "scientific_name": "Ficus elastica",
                "common_names": ["Rubber tree"],
                "score": 0.91,
            }
        ],
        "cached": True,
    }
    monkeypatch.setattr(account_entitlements, "account_entitlement_client", fake)
    monkeypatch.setattr(plantnet, "plantnet_configured", lambda: True)
    # The endpoint misses, consumes an allowance, then another request fills
    # the shared AI-service cache before identify_species performs its probe:
    # one lookup, the endpoint's, misses; every later one hits.
    lookups = []

    def cache_lookup(*_a, **_kw):
        lookups.append(1)
        return None if len(lookups) == 1 else cached

    monkeypatch.setattr(plantnet, "plantnet_cache_lookup", cache_lookup)
    monkeypatch.setattr(plantnet, "PLANTNET_API_KEY", "test-only-key")

    response = client.post(
        "/api/identify",
        files={"image": ("leaf.png", png_bytes, "image/png")},
        headers={
            "Authorization": f"Bearer {_test_access_token('identify-cache-race')}",
            "X-Idempotency-Key": operation_id,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["cached"] is True
    assert "_upstream_attempted" not in response.json()
    assert fake.releases == fake.consumes
    assert fake.releases[0][1:] == ("plant_identification", operation_id)


def test_analyze_concurrent_inner_cache_fill_releases_optional_allowance(
    client,
    png_bytes,
    monkeypatch,
):
    import account_entitlements
    import plantnet

    fake = _ReleasingEntitlements()
    operation_id = str(uuid.uuid4())
    cached = {
        "available": True,
        "source": "plantnet",
        "results": [
            {
                "scientific_name": "Ficus elastica",
                "common_names": ["Rubber tree"],
                "score": 0.91,
            }
        ],
        "cached": True,
    }
    monkeypatch.setattr(account_entitlements, "account_entitlement_client", fake)
    monkeypatch.setattr(plantnet, "plantnet_configured", lambda: True)
    lookups = []

    def cache_lookup(*_a, **_kw):
        lookups.append(1)
        return None if len(lookups) == 1 else cached

    monkeypatch.setattr(plantnet, "plantnet_cache_lookup", cache_lookup)
    monkeypatch.setattr(plantnet, "PLANTNET_API_KEY", "test-only-key")

    response = _post_analyze(
        client,
        png_bytes,
        headers={
            "Authorization": f"Bearer {_test_access_token('analyze-cache-race')}",
            "X-Idempotency-Key": operation_id,
        },
    )

    assert response.status_code == 200, response.text
    assert [call[1] for call in fake.consumes] == [
        "gamma_analysis",
        "plant_identification",
    ]
    assert fake.releases == [fake.consumes[1]]
    assert response.json()["species"]["best"]["scientific_name"] == "Ficus elastica"
    assert "_upstream_attempted" not in response.json()["species"]


class _PolishCache:
    """Stands in for generate_care_plan: a polish is cached per species."""

    def __init__(self):
        self.polished: set[str | None] = set()
        self.paid_calls = 0
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        species = kwargs.get("species")
        if species in self.polished:
            return {"summary_md": "polished", "cached": True}
        if kwargs.get("cached_polish_only") or not kwargs.get("polish"):
            return {"summary_md": "rules"}
        self.paid_calls += 1
        self.polished.add(species)
        return {"summary_md": "polished"}


def test_rules_based_care_plan_costs_no_allowance(client):
    original = server_main._GUEST_FEATURE_LIMITS["care_plan"]
    server_main._GUEST_FEATURE_LIMITS["care_plan"] = 1
    try:
        first = client.post("/api/care-plan", json={"disease": "unknown"})
        second = client.post("/api/care-plan", json={"disease": "unknown"})
    finally:
        server_main._GUEST_FEATURE_LIMITS["care_plan"] = original

    # No LLM is configured in tests, so every plan is the free rules-based one.
    assert first.status_code == 200
    assert second.status_code == 200
    assert not server_main._guest_feature_counts


def test_only_uncached_care_plan_polish_spends_allowance(client, monkeypatch):
    import ai_services
    import care_plan
    fake = _PolishCache()
    monkeypatch.setattr(care_plan, "generate_care_plan", fake)
    monkeypatch.setattr(ai_services, "openrouter_configured", lambda: True)
    monkeypatch.setitem(server_main._GUEST_FEATURE_LIMITS, "care_plan", 1)

    first = client.post("/api/care-plan", json={"disease": "unknown", "species": "Ficus elastica"})
    cached = client.post("/api/care-plan", json={"disease": "unknown", "species": "Ficus elastica"})
    other = client.post("/api/care-plan", json={"disease": "unknown", "species": "Monstera deliciosa"})

    assert first.status_code == 200
    assert first.json()["summary_md"] == "polished"
    assert cached.status_code == 200
    assert cached.json()["cached"] is True
    assert other.status_code == 429
    assert other.json() == {"detail": "quota_exhausted"}
    assert fake.paid_calls == 1
    assert sum(server_main._guest_feature_counts.values()) == 1


def test_species_page_plan_is_never_metered(client, monkeypatch):
    import ai_services
    import care_plan
    fake = _PolishCache()
    monkeypatch.setattr(care_plan, "generate_care_plan", fake)
    monkeypatch.setattr(ai_services, "openrouter_configured", lambda: True)
    monkeypatch.setitem(server_main._GUEST_FEATURE_LIMITS, "care_plan", 1)

    for species in ("Ficus elastica", "Monstera deliciosa"):
        response = client.post(
            "/api/care-plan",
            # A disease sent with the species purpose is ignored: the general
            # plan is always the healthy-plant one.
            json={"disease": "root_rot", "species": species, "purpose": "species"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["summary_md"] == "polished"

    assert fake.paid_calls == 2
    assert {call["disease"] for call in fake.calls} == {"healthy"}
    assert not server_main._guest_feature_counts


def test_garden_checklist_plan_is_never_polished_or_metered(client, monkeypatch):
    import ai_services
    import care_plan
    fake = _PolishCache()
    monkeypatch.setattr(care_plan, "generate_care_plan", fake)
    monkeypatch.setattr(ai_services, "openrouter_configured", lambda: True)
    monkeypatch.setitem(server_main._GUEST_FEATURE_LIMITS, "care_plan", 1)

    for _ in range(2):
        response = client.post(
            "/api/care-plan",
            json={"disease": "root_rot", "species": "Ficus elastica", "purpose": "checklist"},
        )
        assert response.status_code == 200, response.text

    assert fake.paid_calls == 0
    assert all(call["polish"] is False for call in fake.calls)
    assert not server_main._guest_feature_counts


def test_unknown_care_plan_purpose_is_rejected(client):
    response = client.post(
        "/api/care-plan",
        json={"disease": "healthy", "purpose": "free_please"},
    )
    assert response.status_code == 422


def test_chat_refused_before_the_model_costs_no_allowance(client, monkeypatch):
    import ai_services
    import plant_doctor
    monkeypatch.setattr(ai_services, "openrouter_configured", lambda: True)
    monkeypatch.setattr(
        plant_doctor, "chat_reply",
        lambda **_: {"available": False, "reply": "", "reason": "session_turn_cap"},
    )
    monkeypatch.setitem(server_main._GUEST_FEATURE_LIMITS, "ai_doctor", 1)

    for _ in range(2):
        response = client.post(
            "/api/chat",
            json={"session_id": "s1", "message": "Why are the leaves yellow?"},
        )
        assert response.status_code == 200, response.text
    # Each message reserved a unit and handed it back.
    assert sum(server_main._guest_feature_counts.values()) == 0


@pytest.mark.anyio
async def test_replayed_operation_is_stopped_without_testclient_lifespan(png_bytes):
    """Keep the quota boundary runnable on Python/AnyIO combinations whose
    synchronous Starlette TestClient portal cannot enter app lifespan."""

    import ai_services
    import care_plan
    original_pipeline = server_main.pipeline
    original_limit = server_main._GUEST_FEATURE_LIMITS["care_plan"]
    original_plan = care_plan.generate_care_plan
    original_configured = ai_services.openrouter_configured
    server_main.pipeline = FakePipeline()
    server_main._GUEST_FEATURE_LIMITS["care_plan"] = 1
    # A paid (uncached) polish, so the care plan reaches the allowance.
    care_plan.generate_care_plan = lambda **_: {"summary_md": "rules"}
    ai_services.openrouter_configured = lambda: True
    operation_id = str(uuid.uuid4())
    transport = httpx2.ASGITransport(app=server_main.app, client=("127.0.0.1", 43123))
    try:
        with server_main._guest_feature_lock:
            server_main._guest_feature_counts.clear()
            server_main._guest_feature_receipts.clear()
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            invalid = await async_client.post(
                "/api/analyze",
                files={"image": ("leaf.txt", b"not an image", "text/plain")},
            )
            first = await async_client.post(
                "/api/care-plan",
                json={"disease": "unknown"},
                headers={"X-Idempotency-Key": operation_id},
            )
            replay = await async_client.post(
                "/api/care-plan",
                json={"disease": "unknown"},
                headers={"X-Idempotency-Key": operation_id},
            )
    finally:
        server_main.pipeline = original_pipeline
        server_main._GUEST_FEATURE_LIMITS["care_plan"] = original_limit
        care_plan.generate_care_plan = original_plan
        ai_services.openrouter_configured = original_configured

    assert invalid.status_code == 400
    assert first.status_code == 200
    assert replay.status_code == 409
    assert replay.json() == {"detail": "operation_already_consumed"}
    assert sum(server_main._guest_feature_counts.values()) == 1


@pytest.mark.anyio
async def test_analyze_plantnet_fanout_reserves_shared_identification_unit(monkeypatch):
    import plantnet
    calls = []

    async def allow(_request, *, feature):
        calls.append(feature)

    monkeypatch.setattr(plantnet, "plantnet_configured", lambda: True)
    monkeypatch.setattr(server_main, "_authorize_metered_feature", allow)

    unavailable = await server_main._authorize_optional_species_lookup(object())

    assert unavailable is None
    assert calls == ["plant_identification"]


@pytest.mark.anyio
async def test_analyze_survives_exhausted_optional_identification_quota(monkeypatch):
    import plantnet
    async def deny(_request, *, feature):
        assert feature == "plant_identification"
        raise server_main.HTTPException(status_code=429, detail="quota_exhausted")

    monkeypatch.setattr(plantnet, "plantnet_configured", lambda: True)
    monkeypatch.setattr(server_main, "_authorize_metered_feature", deny)

    unavailable = await server_main._authorize_optional_species_lookup(object())

    assert unavailable == {
        "available": False,
        "source": "quota_exhausted",
        "best": None,
        "candidates": [],
    }


def test_guest_gets_ten_species_identifications_a_day():
    """Guests (no account) may identify ten photos per day; the eleventh is
    refused until the daily reset, and a new day starts a fresh allowance."""
    import main as server_main

    assert server_main._GUEST_FEATURE_LIMITS["plant_identification"] == 10
    day = 20_000
    address = "203.0.113.77"
    decisions = [
        server_main._consume_guest_feature(
            feature="plant_identification", address=address, request_id=f"r{i}", day=day,
        )
        for i in range(11)
    ]
    assert all(d.allowed for d in decisions[:10])
    assert not decisions[10].allowed
    assert decisions[9].remaining == 0
    tomorrow = server_main._consume_guest_feature(
        feature="plant_identification", address=address, request_id="next", day=day + 1,
    )
    assert tomorrow.allowed
