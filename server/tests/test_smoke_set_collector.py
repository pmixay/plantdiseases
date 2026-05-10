"""Tests for ``server/tools/collect_smoke_set.py``.

The collector hits the network (Openverse, iNat) so we don't exercise
the full pipeline here. The unit under test is the ``LeakageGate`` —
it's the safety net for the val_realworld path and is pure: given some
training images on disk, calling ``reject_reason(content_bytes)``
should refuse anything that matches by SHA-256 (always) and by pHash
(when the optional ``imagehash`` dep is installed).
"""

from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path

import pytest
from PIL import Image

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import collect_smoke_set as css  # noqa: E402


def _png_bytes(color: tuple[int, int, int], size: int = 96) -> bytes:
    img = Image.new("RGB", (size, size), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_leakage_gate_blocks_train_sha_match(tmp_path):
    train = tmp_path / "train" / "healthy"
    sister = tmp_path / "val_indomain" / "healthy"
    payload = _png_bytes((42, 200, 99))
    _write(train / "leak.png", payload)
    _write(sister / "unrelated.png", _png_bytes((10, 10, 10)))

    gate = css.LeakageGate.build(
        tmp_path / "train", [tmp_path / "val_indomain"],
        use_phash=False,
    )
    assert gate.reject_reason(payload) == "sha:train"
    # An unrelated payload passes.
    assert gate.reject_reason(_png_bytes((1, 2, 3))) is None
    # The sister-heldout SHA also blocks (so a smoke top-up can't
    # accidentally re-seed the same photo into two held-out splits).
    sister_payload = _png_bytes((10, 10, 10))
    assert gate.reject_reason(sister_payload) == "sha:sister-heldout"
    summary = gate.summary()
    assert summary["dropped_sha_train"] == 1
    assert summary["dropped_sha_sister_heldout"] == 1


@pytest.mark.skipif(css.imagehash is None,
                    reason="imagehash not installed; pHash gate is opt-in")
def test_leakage_gate_blocks_train_phash_near_dup(tmp_path):
    train = tmp_path / "train" / "healthy"
    payload = _png_bytes((180, 60, 60), size=128)
    _write(train / "orig.png", payload)
    gate = css.LeakageGate.build(
        tmp_path / "train", [], use_phash=True, threshold=6,
    )
    # Identical bytes → caught by SHA before pHash gets a chance.
    assert gate.reject_reason(payload) == "sha:train"
    # Identical *image* re-encoded as JPEG — different SHA, same pHash.
    img = Image.open(io.BytesIO(payload)).convert("RGB")
    re_encoded = io.BytesIO()
    img.save(re_encoded, format="JPEG", quality=85)
    blob = re_encoded.getvalue()
    assert hashlib.sha256(blob).hexdigest() not in gate.train_sha
    reason = gate.reject_reason(blob)
    assert reason is not None and reason.startswith("phash:train")


def test_dest_root_resolution():
    assert css._dest_root("smoke") == css.SMOKE_ROOT
    assert css._dest_root("val_realworld") == css.VAL_REALWORLD_ROOT
    with pytest.raises(ValueError):
        css._dest_root("totally-unknown-target")
