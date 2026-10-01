"""The blur gate is one statistic on both tiers.

``pipeline._variance_of_laplacian`` (server, gamma) and the Android
``util/BlurGate.laplacianVariance`` (on-device, aqua) must score the same
photograph the same way, or the two tiers reject different frames. Both
suites compute one synthetic fixture and assert one reference value; the
fixture and the reference are duplicated verbatim in
``android/app/src/test/java/com/plantdiseases/app/util/BlurGateTest.kt``.

Fixture: 96x64 RGB, pixel (x, y) = ((7x + 3y) % 256, (x*x + y) % 256, (x*y) % 256).
Reference: PIL-luma grayscale, interior 3x3 Laplacian, population variance
over the 5828 interior pixels, computed in float64.
"""

import numpy as np
import pytest
from PIL import Image

from pipeline import BLUR_DOWNSCALE, BLUR_LAPLACIAN_FLOOR, _variance_of_laplacian

WIDTH, HEIGHT = 96, 64
REFERENCE_VARIANCE = 15219.0938899798
REFERENCE_N = 5828


def _fixture() -> Image.Image:
    arr = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            arr[y, x] = ((x * 7 + y * 3) % 256, (x * x + y) % 256, (x * y) % 256)
    return Image.fromarray(arr, "RGB")


def _reference(img: Image.Image) -> float:
    """Independent float64 implementation of the shared contract."""
    rgb = np.asarray(img.convert("RGB"), dtype=np.int64)
    gray = (rgb[..., 0] * 19595 + rgb[..., 1] * 38470 + rgb[..., 2] * 7471 + 0x8000) >> 16
    lap = (
        gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]
        - 4 * gray[1:-1, 1:-1]
    ).astype(np.float64)
    return float(lap.var())


def test_pil_luma_matches_the_integer_formula_the_app_uses():
    """PIL's ``convert("L")`` is the integer ITU-R 601-2 luma BlurGate.kt mirrors."""
    rgb = np.asarray(_fixture(), dtype=np.int64)
    expected = (rgb[..., 0] * 19595 + rgb[..., 1] * 38470 + rgb[..., 2] * 7471 + 0x8000) >> 16
    assert np.array_equal(np.asarray(_fixture().convert("L"), dtype=np.int64), expected)


def test_server_blur_score_matches_the_shared_reference():
    img = _fixture()
    assert (WIDTH - 2) * (HEIGHT - 2) == REFERENCE_N
    assert _reference(img) == pytest.approx(REFERENCE_VARIANCE, rel=1e-9)
    # float32 accumulation on the server side; the app uses doubles.
    assert _variance_of_laplacian(img) == pytest.approx(REFERENCE_VARIANCE, rel=1e-3)


def test_server_scores_the_interior_only_no_wraparound():
    """A frame whose top and bottom rows differ must not gain edge energy from
    a wrapped border: the app has no such border, so parity requires none."""
    arr = np.zeros((40, 60, 3), dtype=np.uint8)
    arr[:20] = 255  # sharp horizontal edge in the middle, nothing at the borders
    img = Image.fromarray(arr, "RGB")
    assert _variance_of_laplacian(img) == pytest.approx(_reference(img), rel=1e-3)


def test_blur_contract_constants_are_the_ones_the_app_pins():
    """BlurGateTest pins 50.0 and 320; a change on one side must fail the other."""
    assert BLUR_LAPLACIAN_FLOOR == 50.0
    assert BLUR_DOWNSCALE == 320
