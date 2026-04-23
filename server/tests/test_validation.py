"""Upload validation: magic bytes, size caps, dimension caps."""

from __future__ import annotations

import io

import pytest
from PIL import Image


def _post_image(client, data: bytes, filename: str, content_type: str):
    return client.post(
        "/api/analyze",
        files={"image": (filename, data, content_type)},
    )


def test_empty_file_rejected(client):
    r = _post_image(client, b"", "empty.png", "image/png")
    assert r.status_code == 400
    assert "empty" in r.json()["detail"].lower()


def test_bogus_magic_bytes_rejected(client):
    # Right content-type but the bytes are not an image.
    r = _post_image(client, b"NOT_AN_IMAGE" * 100, "fake.png", "image/png")
    assert r.status_code == 400


def test_unsupported_content_type_rejected(client, png_bytes):
    r = _post_image(client, png_bytes, "doc.pdf", "application/pdf")
    assert r.status_code == 400
    assert "unsupported" in r.json()["detail"].lower()


def test_too_large_file_rejected(client, monkeypatch):
    import main as server_main

    monkeypatch.setattr(server_main, "MAX_FILE_SIZE", 1024)
    img = Image.new("RGB", (256, 256), color=(80, 150, 80))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    oversized = buf.getvalue() + b"\x00" * 2048
    r = _post_image(client, oversized, "big.png", "image/png")
    assert r.status_code == 400
    assert "too large" in r.json()["detail"].lower()


def test_too_high_resolution_rejected(client, monkeypatch):
    import main as server_main

    monkeypatch.setattr(server_main, "MAX_IMAGE_DIMENSION", 100)
    img = Image.new("RGB", (200, 80), color=(70, 140, 70))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    r = _post_image(client, buf.getvalue(), "big.png", "image/png")
    assert r.status_code == 400
    assert "resolution too large" in r.json()["detail"].lower()


@pytest.mark.parametrize("fmt,content_type", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_valid_image_accepted(client, fmt, content_type):
    img = Image.new("RGB", (64, 64), color=(90, 160, 90))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    r = _post_image(client, buf.getvalue(), f"leaf.{fmt.lower()}", content_type)
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("disease_name", "confidence", "all_probs", "pipeline_mode", "detection"):
        assert key in body
