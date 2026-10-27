"""Inference admission control on /api/analyze and /api/identify.

Both routes claim a slot before they read or decode the upload; a request
that finds the server full (globally, or for its own IP) gets 503 with a
``Retry-After`` header, and every slot is returned however the request ends.
"""

from __future__ import annotations

import time

import pytest

ROUTES = ["/api/analyze", "/api/identify"]
# Starlette's TestClient connects as this peer; with no TRUSTED_PROXIES,
# ``_client_ip`` returns it unchanged.
TEST_PEER = "testclient"


@pytest.fixture
def admission(monkeypatch):
    import main as server_main

    monkeypatch.setattr(server_main, "INFER_MAX_INFLIGHT", 3)
    monkeypatch.setattr(server_main, "INFER_MAX_INFLIGHT_PER_IP", 2)
    monkeypatch.setattr(server_main, "_inference_inflight", 0)
    monkeypatch.setattr(server_main, "_inference_inflight_by_ip", {})
    monkeypatch.setattr(server_main, "_inference_busy_rejections", 0)
    return server_main


def test_global_and_per_ip_caps(admission):
    m = admission
    assert m._try_admit_inference("a")
    assert m._try_admit_inference("a")
    assert not m._try_admit_inference("a")  # per-IP cap (2)
    assert m._try_admit_inference("b")
    assert not m._try_admit_inference("c")  # global cap (3)
    assert m._inference_busy_rejections == 2

    m._release_inference("a")
    assert m._try_admit_inference("c")
    for ip in ("a", "b", "c"):
        m._release_inference(ip)
    assert m._inference_inflight == 0
    assert m._inference_inflight_by_ip == {}


def test_zero_disables_a_cap(admission, monkeypatch):
    m = admission
    monkeypatch.setattr(m, "INFER_MAX_INFLIGHT", 0)
    monkeypatch.setattr(m, "INFER_MAX_INFLIGHT_PER_IP", 0)
    assert all(m._try_admit_inference("a") for _ in range(50))


@pytest.mark.parametrize("route", ROUTES)
def test_full_server_answers_503_with_retry_after(client, admission, png_bytes, route):
    m = admission
    for ip in ("x", "y", "z"):
        assert m._try_admit_inference(ip)

    r = client.post(route, files={"image": ("leaf.png", png_bytes, "image/png")})

    assert r.status_code == 503, r.text
    assert r.headers["Retry-After"] == str(m.INFER_BUSY_RETRY_AFTER_SECONDS)
    assert m._inference_inflight == 3  # the rejected request held nothing


@pytest.mark.parametrize("route", ROUTES)
def test_one_ip_cannot_take_every_slot(client, admission, png_bytes, route):
    m = admission
    assert m._try_admit_inference(TEST_PEER)
    assert m._try_admit_inference(TEST_PEER)

    r = client.post(route, files={"image": ("leaf.png", png_bytes, "image/png")})
    assert r.status_code == 503, r.text

    # Another caller still gets in: the global cap has room.
    assert m._try_admit_inference("someone-else")


@pytest.mark.parametrize("route", ROUTES)
def test_slot_is_released_on_success_and_on_error(client, admission, png_bytes, route):
    m = admission
    ok = client.post(route, files={"image": ("leaf.png", png_bytes, "image/png")})
    assert ok.status_code == 200, ok.text
    bad = client.post(route, files={"image": ("leaf.png", b"not an image", "image/png")})
    assert bad.status_code == 400, bad.text

    assert m._inference_inflight == 0
    assert m._inference_inflight_by_ip == {}


def test_busy_rejections_reach_metrics(client, admission, png_bytes):
    m = admission
    for ip in ("x", "y", "z"):
        m._try_admit_inference(ip)
    client.post("/api/analyze", files={"image": ("leaf.png", png_bytes, "image/png")})

    body = client.get("/api/metrics").json()
    assert body["inference_busy_rejections"] == 1
    assert body["inference_inflight"] == 3
    prom = client.get("/api/metrics/prometheus").text
    assert "plantdiseases_inference_busy_rejections_total 1" in prom


@pytest.mark.parametrize("route", ROUTES)
def test_no_slot_is_held_while_waiting_on_plantnet(client, admission, png_bytes, route, monkeypatch):
    """The slot covers read/decode/inference only. A slow PlantNet must not
    hold it, or an upstream stall turns into 503s while inference sits idle."""
    m = admission
    seen: list[int] = []

    def fake_identify(image_bytes, *, organ="leaf", lang="en"):
        # /api/analyze starts PlantNet beside inference, so wait for the handler
        # to finish inference and hand the slot back before sampling it; if the
        # slot were held across the PlantNet wait this would time out at 1.
        deadline = time.monotonic() + 3.0
        while m._inference_inflight and time.monotonic() < deadline:
            time.sleep(0.01)
        seen.append(m._inference_inflight)
        return {"available": False, "results": [], "source": "no_api_key"}

    monkeypatch.setattr(m, "identify_species", fake_identify)
    r = client.post(route, files={"image": ("leaf.png", png_bytes, "image/png")})
    assert r.status_code == 200, r.text
    assert seen == [0]


def test_busy_503_does_not_spend_the_daily_quota(client, admission, png_bytes, monkeypatch):
    m = admission
    monkeypatch.setattr(m._analyze_quota, "limit", 1)
    for ip in ("x", "y", "z"):
        m._try_admit_inference(ip)
    busy = client.post("/api/analyze", files={"image": ("leaf.png", png_bytes, "image/png")})
    assert busy.status_code == 503
    for ip in ("x", "y", "z"):
        m._release_inference(ip)

    ok = client.post("/api/analyze", files={"image": ("leaf.png", png_bytes, "image/png")})
    assert ok.status_code == 200, ok.text


def test_ipv6_clients_are_counted_per_64(admission):
    m = admission
    assert m._client_key("2001:db8:1:2:aaaa::1") == m._client_key("2001:db8:1:2:ffff::9")
    assert m._client_key("2001:db8:1:2::1") != m._client_key("2001:db8:1:3::1")
    assert m._client_key("203.0.113.7") == "203.0.113.7"
    assert m._client_key("testclient") == "testclient"
