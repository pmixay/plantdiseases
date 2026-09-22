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


def test_riff_non_webp_rejected(client):
    """A bare RIFF container (e.g. WAV, AVI) used to slip past the
    magic-byte check because `RIFF` was in IMAGE_MAGIC. Now WebP gets
    its own explicit `RIFF<size>WEBP` validator and bare-RIFF is
    refused — Pillow would later fail the decode anyway, but we want
    the rejection at the magic-byte stage."""
    # WAV header: 'RIFF' + 4-byte size + 'WAVE'.
    wav = b"RIFF" + (1024).to_bytes(4, "little") + b"WAVE" + b"\x00" * 64
    r = _post_image(client, wav, "fake.webp", "image/webp")
    assert r.status_code == 400
    assert "unrecognised" in r.json()["detail"].lower()


def test_webp_passes_magic_check(client):
    """Real WebP — `RIFF<size>WEBP` — should pass the magic check (the
    decode might still fail downstream because TestClient doesn't run
    Pillow's decoder reliably for synthetic WebP, but the magic stage
    must accept it)."""
    webp = b"RIFF" + (1024).to_bytes(4, "little") + b"WEBP" + b"VP8 " + b"\x00" * 64
    r = _post_image(client, webp, "leaf.webp", "image/webp")
    # We're testing the magic stage. Pillow may or may not accept the
    # synthetic stream — both 200 (decoded) and 400 ("Cannot read image
    # file") are acceptable as long as it's NOT the magic-byte
    # "Unrecognised image format" message.
    body = r.json().get("detail", "").lower() if r.status_code == 400 else ""
    assert "unrecognised" not in body, (
        "WebP magic bytes were rejected by the magic stage — "
        "regression of audit fix S1"
    )


def test_original_filename_never_appears_in_application_logs(client, png_bytes, caplog):
    """Client filenames are unnecessary user-derived data, even sanitized."""
    import logging

    caplog.set_level(logging.INFO, logger="plantdiseases")
    sentinel = "PRIVATE_FILENAME_SENTINEL_9E0A"
    r = _post_image(
        client, png_bytes,
        f"{sentinel}\nFAKE INJECTED LINE.jpg",
        "image/png",
    )
    assert r.status_code == 200
    rendered = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "plantdiseases"
    )
    assert sentinel not in rendered
    assert "FAKE INJECTED LINE" not in rendered


def test_decode_exception_text_never_appears_in_logs_or_response(
    client, png_bytes, caplog, monkeypatch,
):
    import logging

    import main as server_main

    sentinel = "PRIVATE_EXCEPTION_PATH_/camera/owner_4F2C"

    def fail_decode(_contents):
        raise ValueError(sentinel)

    monkeypatch.setattr(server_main, "_open_image", fail_decode)
    caplog.set_level(logging.INFO, logger="plantdiseases")
    response = _post_image(client, png_bytes, "scan.jpg", "image/png")
    assert response.status_code == 400
    rendered = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "plantdiseases"
    )
    assert sentinel not in rendered
    assert sentinel not in response.text


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


def test_too_small_image_rejected(client):
    """A degenerate tiny upload (e.g. 8x8) carries no diagnostic signal
    but the blur gate passes it and the pipeline emits a confident, fake
    verdict (observed live: 8x8 noise -> viral_disease@0.72/high). It must
    be short-circuited to a clean 400 instead."""
    img = Image.new("RGB", (8, 8), color=(70, 140, 70))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    r = _post_image(client, buf.getvalue(), "tiny.png", "image/png")
    assert r.status_code == 400
    assert "too small" in r.json()["detail"].lower()


@pytest.mark.parametrize("fmt,content_type", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_valid_image_accepted(client, fmt, content_type):
    img = Image.new("RGB", (64, 64), color=(90, 160, 90))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    r = _post_image(client, buf.getvalue(), f"leaf.{fmt.lower()}", content_type)
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("disease_name", "confidence", "all_probs", "detection"):
        assert key in body


def test_unexpected_pil_error_returns_400(client, monkeypatch):
    """Non-HTTPException errors from PIL must surface as a clean 400
    rather than a 500 inference crash."""
    import main as server_main

    def boom(_contents):
        raise RuntimeError("synthetic PIL bug")

    monkeypatch.setattr(server_main, "_open_image", boom)
    img = Image.new("RGB", (64, 64), color=(80, 160, 80))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    r = _post_image(client, buf.getvalue(), "leaf.png", "image/png")
    assert r.status_code == 400
    assert "cannot read" in r.json()["detail"].lower()
