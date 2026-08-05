package com.plantdiseases.app.ui.widget

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class WorldMapGeometryTest {

    @Test
    fun farCountryDoesNotWrapAcrossAmericasViewport() {
        val eastAsiaRing = listOf(
            GeoPoint(70.0, 20.0),
            GeoPoint(140.0, 20.0),
            GeoPoint(140.0, 50.0),
            GeoPoint(70.0, 50.0),
            GeoPoint(70.0, 20.0),
        )

        assertThat(
            pointInRing(
                point = GeoPoint(-100.0, 35.0),
                ring = eastAsiaRing,
                centerLongitude = -80.0,
            ),
        ).isFalse()
    }

    @Test
    fun actualCountryRemainsSelectableInFocusedViewport() {
        val northAmericaRing = listOf(
            GeoPoint(-125.0, 25.0),
            GeoPoint(-65.0, 25.0),
            GeoPoint(-65.0, 50.0),
            GeoPoint(-125.0, 50.0),
            GeoPoint(-125.0, 25.0),
        )

        assertThat(
            pointInRing(
                point = GeoPoint(-100.0, 35.0),
                ring = northAmericaRing,
                centerLongitude = -80.0,
            ),
        ).isTrue()
    }
}
