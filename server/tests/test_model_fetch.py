"""Unit tests for the model bundle fetcher (``model_fetch.py``).

All tests are network-free: they exercise URL parsing, the presence
check, archive extraction + single-dir flattening, the path-traversal
guard, and the idempotent / fail-loud branches of ``ensure_models``. The
actual download path is covered by the live probe documented in the PR,
not in CI.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

import model_fetch as mf

_ARTEFACTS = ("detector.onnx", "classifier.onnx", "classes.json", "temperature.json")


def _member_bytes(name: str) -> bytes:
    return b"PK\x03\x04onnx" if name.endswith(".onnx") else b'{"k": 1}'


def _make_tar(path: Path, prefix: str = "") -> None:
    with tarfile.open(path, "w") as t:
        for name in _ARTEFACTS:
            data = _member_bytes(name)
            info = tarfile.TarInfo(prefix + name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))


def _make_zip(path: Path, prefix: str = "") -> None:
    with zipfile.ZipFile(path, "w") as z:
        for name in _ARTEFACTS:
            z.writestr(prefix + name, _member_bytes(name))


# ── URL parsing ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://drive.google.com/file/d/1s10Lo_-xLiHLCIzYZqKAsprC78F-xJgh/view?usp=sharing",
         "1s10Lo_-xLiHLCIzYZqKAsprC78F-xJgh"),
        ("https://drive.google.com/uc?export=download&id=ABC123_-x", "ABC123_-x"),
        ("https://drive.usercontent.google.com/download?id=ZZZ&export=download", "ZZZ"),
        ("https://example.com/models.tar", None),
    ],
)
def test_google_drive_id(url, expected):
    assert mf.google_drive_id(url) == expected


def test_is_google_drive():
    assert mf.is_google_drive("https://drive.google.com/file/d/abc/view")
    assert not mf.is_google_drive("https://s3.amazonaws.com/bucket/models.tar.gz")


# ── presence check ───────────────────────────────────────────────────


def test_missing_groups_and_present(tmp_path: Path):
    assert mf.missing_groups(tmp_path) == ["classifier", "detector", "metadata"]
    (tmp_path / "classifier_int8.onnx").write_bytes(b"x")  # int8 satisfies classifier
    (tmp_path / "detector.pt").write_bytes(b"x")           # .pt satisfies detector
    (tmp_path / "classes.json").write_text("{}")
    assert mf.models_present(tmp_path)
    assert mf.missing_groups(tmp_path) == []


# ── extraction + flatten ─────────────────────────────────────────────


@pytest.mark.parametrize("prefix", ["", "upgrmodels/"])
def test_extract_tar(tmp_path: Path, prefix):
    arch = tmp_path / "bundle.tar"
    _make_tar(arch, prefix=prefix)
    staging = tmp_path / "staging"
    staging.mkdir()
    mf._extract(arch, staging)
    out = tmp_path / "models"
    out.mkdir()
    mf._flatten_into(staging, out)
    assert mf.models_present(out)


def test_extract_zip(tmp_path: Path):
    arch = tmp_path / "bundle.zip"
    _make_zip(arch, prefix="models/")
    staging = tmp_path / "staging"
    staging.mkdir()
    mf._extract(arch, staging)
    out = tmp_path / "models"
    out.mkdir()
    mf._flatten_into(staging, out)
    assert mf.models_present(out)


def test_extract_rejects_unknown_format(tmp_path: Path):
    junk = tmp_path / "bundle.bin"
    junk.write_bytes(b"not an archive")
    with pytest.raises(mf.ModelFetchError, match="unrecognised bundle format"):
        mf._extract(junk, tmp_path / "staging")


def test_extract_rejects_path_traversal(tmp_path: Path):
    arch = tmp_path / "evil.tar"
    with tarfile.open(arch, "w") as t:
        data = b"x"
        info = tarfile.TarInfo("../escape.onnx")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(mf.ModelFetchError, match="unsafe path"):
        mf._extract(arch, staging)


# ── ensure_models branches ───────────────────────────────────────────


def test_ensure_models_skips_when_present(tmp_path: Path):
    for n in ("classifier.onnx", "detector.onnx", "classes.json"):
        (tmp_path / n).write_bytes(b"x")
    # URL is intentionally unreachable — a present cache must short-circuit
    # before any network call.
    assert mf.ensure_models(tmp_path, "https://unused.invalid/x.tar") is False


def test_ensure_models_missing_and_no_url_fails_loud(tmp_path: Path):
    with pytest.raises(mf.ModelFetchError, match="MODEL_BUNDLE_URL"):
        mf.ensure_models(tmp_path, "")


def test_ensure_models_force_with_no_url_fails_loud(tmp_path: Path):
    for n in ("classifier.onnx", "detector.onnx", "classes.json"):
        (tmp_path / n).write_bytes(b"x")
    with pytest.raises(mf.ModelFetchError):
        mf.ensure_models(tmp_path, "", force=True)
