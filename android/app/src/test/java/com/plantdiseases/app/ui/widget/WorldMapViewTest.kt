package com.plantdiseases.app.ui.widget

import android.app.Application
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class WorldMapViewTest {

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

    @Test
    fun ambiguousSovereignNameResolvesToTheCountryItself() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val map = WorldMapAsset.load(context)

        // The shipped asset indexes SOVEREIGNT/SOV_A3, so "France" is an alias
        // of French Southern Territories and New Caledonia as well as France
        // itself. Matching all three fitted the viewport across ~200 degrees of
        // longitude and highlighted two territories instead of the country.
        assertThat(map.match("France").single().code).isEqualTo("FRA")
        assertThat(map.match("Denmark").single().code).isEqualTo("DNK")
        assertThat(map.match("United Kingdom").single().code).isEqualTo("GBR")
    }

    @Test
    fun setZones_reportsWhetherAnythingCouldBePlaced() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val view = WorldMapView(context)

        assertThat(
            view.setZones(
                listOf(
                    WorldMapView.CountryZone("Brazil", WorldMapView.ZoneStatus.GROWS_MORE, "BRA"),
                ),
            ),
        ).isTrue()

        // Small island states are absent from the bundled 110m dataset. When
        // nothing can be placed the caller must learn about it, otherwise the
        // map fits the whole globe and highlights nothing while the card above
        // still announces the countries.
        assertThat(
            view.setZones(
                listOf(
                    WorldMapView.CountryZone("Mauritius", WorldMapView.ZoneStatus.GROWS_MORE, "MUS"),
                    WorldMapView.CountryZone("Seychelles", WorldMapView.ZoneStatus.GROWS_MORE, "SYC"),
                ),
            ),
        ).isFalse()

        assertThat(view.setZones(emptyList())).isFalse()
    }
}
