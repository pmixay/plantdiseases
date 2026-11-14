package com.plantdiseases.app.ui.widget

import android.app.Application
import android.content.Context
import android.graphics.PointF
import android.view.MotionEvent
import android.view.View
import android.widget.TextView
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Taps on the laid-out map, end to end: projection → country Path → Region
 * hit test → the chip under the map.
 *
 * Native graphics on purpose: the legacy shadows keep no real geometry in a
 * Region, so every tap would miss and the test would prove nothing.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class WorldMapTapTest {

    private lateinit var context: Context
    private lateinit var data: WorldMapData
    private lateinit var view: WorldMapView

    // Children in the order WorldMapView adds them: map, hint, result chip.
    private val canvas: View get() = view.getChildAt(0)
    private val hint: TextView get() = view.getChildAt(1) as TextView
    private val chip: TextView get() = view.getChildAt(2) as TextView

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        data = WorldMapAsset.load(context)
        view = WorldMapView(context)
        // Zones first, layout second — the order a screen goes through, so
        // the projection is built from onSizeChanged, not from setZones.
        assertThat(
            view.setZones(
                listOf(
                    WorldMapView.CountryZone("Brazil", WorldMapView.ZoneStatus.GROWS_MORE, "BRA"),
                ),
            ),
        ).isTrue()
        view.measure(
            View.MeasureSpec.makeMeasureSpec(MAP_WIDTH_PX, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED),
        )
        view.layout(0, 0, view.measuredWidth, view.measuredHeight)
    }

    @Test
    fun tappingAZonedCountry_namesItWithItsStatus() {
        tap(longitude = BRASILIA_LON, latitude = BRASILIA_LAT)

        assertThat(hint.visibility).isEqualTo(View.GONE)
        assertThat(chip.visibility).isEqualTo(View.VISIBLE)
        assertThat(chip.text.toString()).isEqualTo(
            context.getString(
                R.string.native_range_selected_country,
                displayName("BRA"),
                context.getString(R.string.native_range_grows_more),
            ),
        )
    }

    @Test
    fun tappingACountryOutsideTheRange_saysSo() {
        tap(longitude = COCHABAMBA_LON, latitude = COCHABAMBA_LAT)

        assertThat(chip.visibility).isEqualTo(View.VISIBLE)
        assertThat(chip.text.toString()).isEqualTo(
            context.getString(R.string.native_range_outside_country, displayName("BOL")),
        )
    }

    @Test
    fun tappingOpenSea_clearsTheSelection() {
        tap(longitude = BRASILIA_LON, latitude = BRASILIA_LAT)
        assertThat(chip.visibility).isEqualTo(View.VISIBLE)

        tap(longitude = SOUTH_ATLANTIC_LON, latitude = SOUTH_ATLANTIC_LAT)

        assertThat(chip.visibility).isEqualTo(View.GONE)
        assertThat(hint.visibility).isEqualTo(View.VISIBLE)
    }

    @Test
    fun onlyTheReleaseSelects() {
        val point = project(BRASILIA_LON, BRASILIA_LAT)
        dispatch(MotionEvent.ACTION_DOWN, point)
        dispatch(MotionEvent.ACTION_MOVE, point)

        assertThat(chip.visibility).isEqualTo(View.GONE)

        dispatch(MotionEvent.ACTION_UP, point)
        assertThat(chip.visibility).isEqualTo(View.VISIBLE)
    }

    private fun displayName(code: String): String =
        data.displayName(data.countries.single { it.code == code })

    private fun tap(longitude: Double, latitude: Double) {
        dispatch(MotionEvent.ACTION_UP, project(longitude, latitude))
    }

    private fun dispatch(action: Int, point: PointF) {
        val event = MotionEvent.obtain(0L, 0L, action, point.x, point.y, 0)
        try {
            canvas.dispatchTouchEvent(event)
        } finally {
            event.recycle()
        }
    }

    /** Projects a coordinate the way the view fits a Brazil-only range. */
    private fun project(longitude: Double, latitude: Double): PointF {
        assertThat(canvas.width).isGreaterThan(0)
        val brazil = data.countries.single { it.code == "BRA" }
        val projection = WorldMapProjection.fit(
            points = brazil.allPoints,
            width = canvas.width.toFloat(),
            height = canvas.height.toFloat(),
            padding = MAP_PADDING_DP * context.resources.displayMetrics.density,
        )
        // South America sits far from the antimeridian, so the plain
        // difference is already the wrapped delta the view uses.
        val point = projection.projectUnwrapped(longitude - projection.centerLongitude, latitude)
        assertThat(point.x).isGreaterThan(0f)
        assertThat(point.x).isLessThan(canvas.width.toFloat())
        assertThat(point.y).isGreaterThan(0f)
        assertThat(point.y).isLessThan(canvas.height.toFloat())
        return point
    }

    private companion object {
        const val MAP_WIDTH_PX = 1080
        const val MAP_PADDING_DP = 14f

        const val BRASILIA_LON = -47.88
        const val BRASILIA_LAT = -15.79
        const val COCHABAMBA_LON = -65.5
        const val COCHABAMBA_LAT = -17.4
        const val SOUTH_ATLANTIC_LON = -30.0
        const val SOUTH_ATLANTIC_LAT = -25.0
    }
}
