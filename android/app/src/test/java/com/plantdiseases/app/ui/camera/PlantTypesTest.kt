package com.plantdiseases.app.ui.camera

import com.google.common.truth.Truth.assertWithMessage
import org.junit.Test

/**
 * Verifies the genus → plant-type heuristic ([PlantTypes.fromName]) that
 * drives the Light Meter gauge and the Water Calculator's default type.
 *
 * Types: 0 general, 1 succulent, 2 tropical, 3 fern, 4 vegetable.
 */
class PlantTypesTest {

    @Test
    fun mapsAGenusToItsLightBucket() {
        val expected = mapOf(
            "Echeveria elegans" to 1,
            "Crassula ovata" to 1,
            "Monstera deliciosa" to 2,
            "Ficus elastica" to 2,
            "Nephrolepis exaltata" to 3,
            "Solanum lycopersicum" to 4,
            // Case and surrounding whitespace do not matter.
            "  ECHEVERIA  agavoides" to 1,
            // An unknown genus and an empty name fall back to "general".
            "Quercus robur" to 0,
            "" to 0,
        )
        for ((species, bucket) in expected) {
            assertWithMessage(species)
                .that(PlantTypes.fromName(species))
                .isEqualTo(bucket)
        }
    }
}
