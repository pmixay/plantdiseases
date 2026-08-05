package com.plantdiseases.app.ui.camera

import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Verifies the genus → light-bucket heuristic that drives the Light Meter
 * gauge. Run under Robolectric so the host AppCompatActivity class loads
 * cleanly even though only the static mapping is exercised.
 *
 * Bucket indices (see LightMeterActivity): 0 general, 1 succulent,
 * 2 tropical, 3 fern, 4 vegetable.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class LightMeterBucketTest {

    @Test
    fun mapsKnownGeneraToCorrectBuckets() {
        assertThat(LightMeterActivity.pickSpeciesBucket("Echeveria elegans")).isEqualTo(1)
        assertThat(LightMeterActivity.pickSpeciesBucket("Crassula ovata")).isEqualTo(1)
        assertThat(LightMeterActivity.pickSpeciesBucket("Monstera deliciosa")).isEqualTo(2)
        assertThat(LightMeterActivity.pickSpeciesBucket("Ficus elastica")).isEqualTo(2)
        assertThat(LightMeterActivity.pickSpeciesBucket("Nephrolepis exaltata")).isEqualTo(3)
        assertThat(LightMeterActivity.pickSpeciesBucket("Solanum lycopersicum")).isEqualTo(4)
    }

    @Test
    fun unknownGenusFallsBackToGeneral() {
        assertThat(LightMeterActivity.pickSpeciesBucket("Quercus robur")).isEqualTo(0)
    }

    @Test
    fun isCaseAndWhitespaceInsensitive() {
        assertThat(LightMeterActivity.pickSpeciesBucket("  ECHEVERIA  agavoides")).isEqualTo(1)
    }

    @Test
    fun emptyStringFallsBackToGeneral() {
        assertThat(LightMeterActivity.pickSpeciesBucket("")).isEqualTo(0)
    }
}
