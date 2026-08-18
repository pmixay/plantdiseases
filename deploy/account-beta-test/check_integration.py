"""Synthetic loopback smoke test for the account/community trust boundary."""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests


def _loopback_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("integration URLs must use loopback HTTP")
    return value.rstrip("/")


def _require(response: requests.Response, status: int) -> dict:
    if response.status_code != status:
        raise RuntimeError(
            f"synthetic integration request failed: {response.request.method} "
            f"{response.request.path_url} -> {response.status_code}"
        )
    return response.json() if response.content else {}


def _signup(account_url: str, provider: str, suffix: str) -> tuple[str, str]:
    provider_token = f"mock-{provider}-generated-{suffix}"
    installation_id = str(uuid.uuid4())
    exchange = _require(
        requests.post(
            f"{account_url}/v1/auth/{provider}/exchange",
            json={
                "provider_access_token": provider_token,
                "installation_id": installation_id,
            },
            timeout=5,
        ),
        200,
    )
    complete = _require(
        requests.post(
            f"{account_url}/v1/auth/signup/complete",
            json={
                "pending_signup_token": exchange["pending_signup_token"],
                "nickname": f"BetaFern{suffix[:8]}",
                "installation_id": installation_id,
            },
            timeout=5,
        ),
        201,
    )
    return complete["access_token"], provider_token


def run(*, account_url: str, app_url: str, image_path: Path) -> None:
    account_url = _loopback_url(account_url)
    app_url = _loopback_url(app_url)
    image = image_path.read_bytes()
    if not image:
        raise ValueError("synthetic integration image is empty")

    owner_suffix = uuid.uuid4().hex
    viewer_suffix = uuid.uuid4().hex
    owner_access, owner_provider_token = _signup(
        account_url, "yandex", owner_suffix
    )
    viewer_access, _viewer_provider_token = _signup(account_url, "vk", viewer_suffix)
    owner_headers = {"Authorization": f"Bearer {owner_access}"}
    viewer_headers = {"Authorization": f"Bearer {viewer_access}"}

    analysis = _require(
        requests.post(
            f"{app_url}/api/analyze",
            files={"image": ("synthetic.png", image, "image/png")},
            timeout=10,
        ),
        200,
    )
    scan_id = analysis["scan_id"]
    disease = analysis["class_name"]
    _require(
        requests.post(
            f"{app_url}/api/community/scan",
            headers=owner_headers,
            data={
                "scan_id": scan_id,
                "user_pseudo_id": "spoofed-legacy-owner",
                "nickname": "Spoofed Nickname",
                "country": "GB",
                "region": "London",
                "disease": disease,
            },
            files={"image": ("synthetic.png", image, "image/png")},
            timeout=10,
        ),
        200,
    )
    public_feed = _require(
        requests.get(
            f"{app_url}/api/community/nearby",
            params={"country": "GB", "scope": "country"},
            timeout=5,
        ),
        200,
    )
    public_id = public_feed["items"][0]["scan_id"]
    if public_feed["items"][0]["nickname"] != f"BetaFern{owner_suffix[:8]}":
        raise RuntimeError("community authorship did not come from signed claims")

    guest_write = requests.post(
        f"{app_url}/api/community/opinion",
        json={"scan_id": public_id, "suggested_disease": "leaf_spot"},
        timeout=5,
    )
    if guest_write.status_code != 401:
        raise RuntimeError("guest community mutation did not fail closed")

    _require(
        requests.post(
            f"{app_url}/api/community/request-opinion",
            headers=owner_headers,
            json={"scan_id": scan_id},
            timeout=5,
        ),
        200,
    )
    _require(
        requests.put(
            f"{account_url}/v1/push/device",
            headers=owner_headers,
            json={
                "fcm_token": f"synthetic-fcm-{uuid.uuid4().hex}",
                "platform": "android",
            },
            timeout=5,
        ),
        200,
    )
    _require(
        requests.post(
            f"{app_url}/api/community/opinion",
            headers=viewer_headers,
            json={
                "scan_id": public_id,
                "user_pseudo_id": "spoofed-owner",
                "nickname": "Spoofed Responder",
                "suggested_disease": "leaf_spot",
                "note": "synthetic integration opinion",
            },
            timeout=5,
        ),
        200,
    )
    private_counts = _require(
        requests.get(
            f"{app_url}/api/community/my-scan-opinions",
            headers=owner_headers,
            timeout=5,
        ),
        200,
    )
    if private_counts["total_opinions"] != 1:
        raise RuntimeError("private owner counts were not actor-scoped")

    reauth = _require(
        requests.post(
            f"{account_url}/v1/auth/reauth",
            headers=owner_headers,
            json={
                "provider": "yandex",
                "provider_access_token": owner_provider_token,
                "scope": "deletion",
            },
            timeout=5,
        ),
        200,
    )
    _require(
        requests.delete(
            f"{account_url}/v1/me",
            headers=owner_headers,
            json={"reauth_token": reauth["reauth_token"]},
            timeout=10,
        ),
        202,
    )
    after_deletion = _require(
        requests.get(
            f"{app_url}/api/community/nearby",
            params={"country": "GB", "scope": "country"},
            timeout=5,
        ),
        200,
    )
    if any(item["scan_id"] == public_id for item in after_deletion["items"]):
        raise RuntimeError("deleted actor content remained visible")
    revoked_write = requests.post(
        f"{app_url}/api/community/view",
        headers=owner_headers,
        json={"scan_id": public_id},
        timeout=5,
    )
    if revoked_write.status_code != 401:
        raise RuntimeError("deleted account token remained authorized")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-url", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--image", type=Path, required=True)
    args = parser.parse_args()
    run(
        account_url=args.account_url,
        app_url=args.app_url,
        image_path=args.image,
    )
    print("synthetic account/community integration: passed")


if __name__ == "__main__":
    main()
