"""Image validation + dedup gates (ported from train_notebook.ipynb §2 + the
DATASET_LABELING_PLAN.md §4 acceptance criteria).

Eval-grade defaults: short side >= 384 px, real-photo only, and SHA +
perceptual-hash de-duplication. Content gates (foliage/subject checks) live
in the maintainer's offline collection workflow, not here.
"""

from __future__ import annotations

from hashlib import sha256 as _sha256
from io import BytesIO

from PIL import Image

try:
    import imagehash
except Exception:  # pragma: no cover - imagehash is a declared dep
    imagehash = None

MIN_SIDE = 384  # eval-grade floor (DATASET_LABELING_PLAN §4)
PHASH_HAMMING = 6


def valid_image_bytes(body: bytes, min_side: int = MIN_SIDE):
    """Decode to RGB and enforce the min-side gate. Returns a PIL image or None."""
    try:
        im = Image.open(BytesIO(body)).convert("RGB")
        im.load()
    except Exception:  # noqa: BLE001
        return None
    if min(im.size) < min_side:
        return None
    return im


def sha256_bytes(body: bytes) -> str:
    return _sha256(body).hexdigest()


def phash_bytes(body: bytes) -> str | None:
    if imagehash is None:
        return None
    try:
        return str(imagehash.phash(Image.open(BytesIO(body)).convert("RGB")))
    except Exception:  # noqa: BLE001
        return None


def _hamming(a: str, b: str) -> int:
    try:
        return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)
    except Exception:  # noqa: BLE001
        return 999


def is_near_dup(phash: str | None, existing: set[str], hamming: int = PHASH_HAMMING) -> bool:
    """True if ``phash`` is within ``hamming`` of any seen hash."""
    if not phash or imagehash is None:
        return False
    return any(_hamming(phash, e) <= hamming for e in existing)
