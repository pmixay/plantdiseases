package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Pins [BlurGate.laplacianVariance] to `server/pipeline.py:_variance_of_laplacian`
 * on one synthetic fixture. `server/tests/test_blur_parity.py` computes the same
 * fixture with the server code and asserts the same reference value, so the
 * two tiers can only drift apart by failing one of the two suites.
 *
 * Fixture: 96x64 RGB, pixel (x, y) = ((7x + 3y) % 256, (x*x + y) % 256, (x*y) % 256).
 * Reference: PIL-luma grayscale, interior 3x3 Laplacian, population variance =
 * 15219.0938899798 over 5828 interior pixels.
 */
class BlurGateTest {

    private companion object {
        const val WIDTH = 96
        const val HEIGHT = 64
        const val REFERENCE_VARIANCE = 15219.0938899798
        const val RELATIVE_TOLERANCE = 1e-6
        const val OPAQUE = 0xFF shl 24
        const val BYTE = 256
    }

    private fun fixture(): IntArray {
        val out = IntArray(WIDTH * HEIGHT)
        for (y in 0 until HEIGHT) {
            for (x in 0 until WIDTH) {
                val r = (x * 7 + y * 3) % BYTE
                val g = (x * x + y) % BYTE
                val b = (x * y) % BYTE
                out[y * WIDTH + x] = OPAQUE or (r shl 16) or (g shl 8) or b
            }
        }
        return out
    }

    @Test
    fun laplacianVariance_matchesServerReferenceOnSharedFixture() {
        val score = BlurGate.laplacianVariance(fixture(), WIDTH, HEIGHT)
        assertThat(score).isWithin(REFERENCE_VARIANCE * RELATIVE_TOLERANCE).of(REFERENCE_VARIANCE)
    }

    @Test
    fun uniformImageScoresZeroAndIsTooBlurry() {
        val flat = IntArray(WIDTH * HEIGHT) { OPAQUE or 0x78A050 }
        val score = BlurGate.laplacianVariance(flat, WIDTH, HEIGHT)
        assertThat(score).isWithin(1e-9).of(0.0)
        assertThat(BlurGate.isTooBlurry(score)).isTrue()
    }

    @Test
    fun floorMatchesServerConstant() {
        assertThat(BlurGate.VARIANCE_LAPLACIAN_FLOOR).isEqualTo(50.0)
        assertThat(BlurGate.DOWNSCALE_MAX_SIDE).isEqualTo(320)
        assertThat(BlurGate.isTooBlurry(49.999)).isTrue()
        assertThat(BlurGate.isTooBlurry(50.0)).isFalse()
    }

    @Test
    fun imagesWithoutAnInteriorScoreZero() {
        assertThat(BlurGate.laplacianVariance(IntArray(4), 2, 2)).isEqualTo(0.0)
    }
}
