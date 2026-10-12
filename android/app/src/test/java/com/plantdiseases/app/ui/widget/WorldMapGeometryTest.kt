package com.plantdiseases.app.ui.widget

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Pure-JVM cover for the geometry/label helpers the map actually runs on: the
 * circular longitude fit behind [WorldMapProjection.fit], the per-ring
 * antimeridian unwrap used while building each country Path, and the label
 * normalizer behind the country lookup. The hit test itself is Region-based
 * and is driven end to end by real taps in [WorldMapTapTest].
 */
class WorldMapGeometryTest {

    @Test
    fun longitudeBounds_centerRangeWithoutWrapping() {
        val bounds = CircularLongitudeBounds.from(listOf(-74.0, -54.0, -34.0))

        assertThat(bounds.center).isWithin(0.0001).of(-54.0)
        assertThat(bounds.span).isWithin(0.0001).of(40.0)
    }

    @Test
    fun longitudeBounds_useShortArcAcrossAntimeridian() {
        val bounds = CircularLongitudeBounds.from(listOf(179.0, -179.0))

        // Not 358 degrees: Fiji-style ranges must stay a 2-degree window.
        assertThat(bounds.span).isWithin(0.0001).of(2.0)
        assertThat(bounds.center).isWithin(0.0001).of(-180.0)
    }

    @Test
    fun polygonLongitudes_areUnwrappedInsteadOfDrawingAcrossTheMap() {
        assertThat(unwrapLongitudeDelta(-179.0, 179.0)).isWithin(0.0001).of(181.0)
        assertThat(unwrapLongitudeDelta(179.0, -179.0)).isWithin(0.0001).of(-181.0)
        assertThat(unwrapLongitudeDelta(12.0, 10.0)).isWithin(0.0001).of(12.0)
    }

    @Test
    fun normalizeMapName_foldsAccentsPunctuationAndAmpersands() {
        assertThat(normalizeMapName("Côte d'Ivoire")).isEqualTo("cote d ivoire")
        assertThat(normalizeMapName("Trinidad & Tobago")).isEqualTo("trinidad and tobago")
        assertThat(normalizeMapName("  Юго-Восточная   Азия ")).isEqualTo("юго восточная азия")
        assertThat(normalizeMapName("   ")).isEmpty()
    }
}
