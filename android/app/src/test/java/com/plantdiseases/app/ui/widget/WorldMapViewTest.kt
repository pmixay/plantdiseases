package com.plantdiseases.app.ui.widget

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import kotlin.math.abs

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class WorldMapViewTest {

    @Test
    fun projection_roundTripsAndFitsHighlightedRange() {
        val points = listOf(
            GeoPoint(-74.0, -34.0),
            GeoPoint(-34.0, 5.0),
            GeoPoint(-50.0, -10.0),
        )
        val projection = WorldMapProjection.fit(points, width = 400f, height = 240f, padding = 16f)

        points.forEach { point ->
            val screen = projection.project(point)
            assertThat(screen.x).isAtLeast(16f)
            assertThat(screen.x).isAtMost(384f)
            assertThat(screen.y).isAtLeast(16f)
            assertThat(screen.y).isAtMost(224f)

            val restored = projection.unproject(screen.x, screen.y)
            assertThat(restored.longitude).isWithin(0.0001).of(point.longitude)
            assertThat(restored.latitude).isWithin(0.0001).of(point.latitude)
        }
    }

    @Test
    fun projection_usesShortArcAcrossAntimeridian() {
        val points = listOf(
            GeoPoint(179.0, -18.0),
            GeoPoint(-179.0, -16.0),
        )
        val bounds = CircularLongitudeBounds.from(points.map { it.longitude })
        val projection = WorldMapProjection.fit(points, width = 360f, height = 220f, padding = 12f)
        val left = projection.project(points[0])
        val right = projection.project(points[1])

        assertThat(bounds.span).isWithin(0.0001).of(2.0)
        assertThat(abs(left.x - right.x)).isLessThan(100f)
        assertThat(projection.centerLongitude).isAnyOf(-180.0, 180.0)
    }

    @Test
    fun polygonLongitudes_areUnwrappedInsteadOfDrawingAcrossTheMap() {
        assertThat(unwrapLongitudeDelta(-179.0, 179.0)).isWithin(0.0001).of(181.0)
        assertThat(unwrapLongitudeDelta(179.0, -179.0)).isWithin(0.0001).of(-181.0)
    }

    @Test
    fun hitTesting_respectsOuterRingBoundaryAndHole() {
        val outer = listOf(
            GeoPoint(0.0, 0.0),
            GeoPoint(10.0, 0.0),
            GeoPoint(10.0, 10.0),
            GeoPoint(0.0, 10.0),
            GeoPoint(0.0, 0.0),
        )
        val hole = listOf(
            GeoPoint(4.0, 4.0),
            GeoPoint(6.0, 4.0),
            GeoPoint(6.0, 6.0),
            GeoPoint(4.0, 6.0),
            GeoPoint(4.0, 4.0),
        )
        val country = CountryGeometry(
            code = "TST",
            nameEn = "Test",
            nameRu = "Тест",
            aliases = setOf("Test"),
            regions = emptySet(),
            polygons = listOf(CountryPolygon(listOf(outer, hole))),
        )

        assertThat(country.contains(GeoPoint(2.0, 2.0), centerLongitude = 5.0)).isTrue()
        assertThat(country.contains(GeoPoint(0.0, 5.0), centerLongitude = 5.0)).isTrue()
        assertThat(country.contains(GeoPoint(5.0, 5.0), centerLongitude = 5.0)).isFalse()
        assertThat(country.contains(GeoPoint(12.0, 2.0), centerLongitude = 5.0)).isFalse()
    }

    @Test
    fun hitTesting_handlesPolygonAcrossAntimeridian() {
        val ring = listOf(
            GeoPoint(178.0, -20.0),
            GeoPoint(-178.0, -20.0),
            GeoPoint(-178.0, -14.0),
            GeoPoint(178.0, -14.0),
            GeoPoint(178.0, -20.0),
        )

        assertThat(pointInRing(GeoPoint(179.0, -17.0), ring, centerLongitude = 180.0)).isTrue()
        assertThat(pointInRing(GeoPoint(-179.0, -17.0), ring, centerLongitude = 180.0)).isTrue()
        assertThat(pointInRing(GeoPoint(170.0, -17.0), ring, centerLongitude = 180.0)).isFalse()
    }

    @Test
    fun bundledAsset_matchesEnglishRussianAliasesAndRegions() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val map = WorldMapAsset.load(context)

        assertThat(map.countries).hasSize(177)
        assertThat(map.match("Brazil").single().code).isEqualTo("BRA")
        assertThat(map.match("PER").single().code).isEqualTo("PER")
        assertThat(map.match("Бразилия").single().code).isEqualTo("BRA")
        assertThat(map.match("southern Brazil").single().code).isEqualTo("BRA")
        assertThat(map.match("Southeast Asia").map { it.code }).contains("THA")
        assertThat(map.match("Юго-Восточная Азия").map { it.code }).contains("VNM")
    }
}
