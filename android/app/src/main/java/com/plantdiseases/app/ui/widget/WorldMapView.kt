package com.plantdiseases.app.ui.widget

import android.content.Context
import android.graphics.Canvas
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PointF
import android.graphics.RectF
import android.graphics.Region
import android.graphics.Shader
import android.graphics.drawable.GradientDrawable
import android.util.AttributeSet
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import com.plantdiseases.app.R
import java.io.BufferedInputStream
import java.io.DataInputStream
import java.text.Normalizer
import java.util.Locale
import kotlin.math.PI
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.tan

/**
 * Offline native-range map backed by bundled Natural Earth 1:110m geometry.
 *
 * This is deliberately a composite XML-compatible View rather than a screen
 * controller: the inner [Canvas] draws the map, a focusable chip per matched
 * country sits below it (the only way to select a country with TalkBack or
 * switch access, since the canvas selection comes from raw touch), and one
 * result chip at the bottom reports the country selected by the user.
 */
class WorldMapView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : LinearLayout(context, attrs, defStyleAttr) {

    enum class ZoneStatus(val wireName: String) {
        GROWS_MORE("grows_more"),
        GROWS_LESS("grows_less"),
        ;

        companion object {
            fun fromWireName(value: String): ZoneStatus? =
                entries.firstOrNull { it.wireName == value }
        }
    }

    data class CountryZone(
        val country: String,
        val status: ZoneStatus,
        val countryCode: String? = null,
    )

    private val mapCanvas = MapCanvasView(context)

    // One focusable chip per matched country. The canvas only ever selects
    // from a MotionEvent, so these chips are what makes the map operable
    // without pointing at a pixel.
    private val countryChipRow = LinearLayout(context)
    private val countryChipScroll = HorizontalScrollView(context)
    private val selectionHint = TextView(context)

    // Material Chip is intentionally single-line and throws when callers try
    // to enable wrapping. The selected-country explanation can span several
    // lines (especially in Russian), so use a chip-styled TextView instead.
    private val selectedChip = TextView(context)
    private var zones: List<CountryZone> = emptyList()

    init {
        orientation = VERTICAL
        gravity = Gravity.START

        mapCanvas.layoutParams = LayoutParams(
            LayoutParams.MATCH_PARENT,
            dp(220f).toInt(),
        )
        mapCanvas.setOnCountrySelectedListener(::showSelectedCountry)
        mapCanvas.setOnSelectionClearedListener(::clearSelectedCountry)
        addView(mapCanvas)

        countryChipRow.orientation = HORIZONTAL
        countryChipScroll.apply {
            isHorizontalScrollBarEnabled = false
            clipToPadding = false
            isVisible = false
            layoutParams = LayoutParams(
                LayoutParams.MATCH_PARENT,
                LayoutParams.WRAP_CONTENT,
            ).apply {
                topMargin = dp(8f).toInt()
            }
            addView(
                countryChipRow,
                FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.WRAP_CONTENT,
                    FrameLayout.LayoutParams.WRAP_CONTENT,
                ),
            )
        }
        addView(countryChipScroll)

        selectionHint.apply {
            text = context.getString(R.string.native_range_tap_hint)
            setTextColor(color(R.color.on_surface_secondary))
            textSize = 14f
            isVisible = true
            layoutParams = LayoutParams(
                LayoutParams.WRAP_CONTENT,
                LayoutParams.WRAP_CONTENT,
            ).apply {
                topMargin = dp(8f).toInt()
            }
        }
        addView(selectionHint)

        selectedChip.apply {
            isClickable = false
            maxLines = 4
            gravity = Gravity.CENTER_VERTICAL
            minHeight = dp(48f).toInt()
            setPadding(
                dp(16f).toInt(),
                dp(10f).toInt(),
                dp(16f).toInt(),
                dp(10f).toInt(),
            )
            setTextColor(color(R.color.on_surface))
            textSize = 14f
            isVisible = false
            layoutParams = LayoutParams(
                LayoutParams.MATCH_PARENT,
                LayoutParams.WRAP_CONTENT,
            ).apply {
                topMargin = dp(8f).toInt()
            }
        }
        addView(selectedChip)
    }

    /**
     * Replaces the complete highlighted range. Blank labels and duplicate
     * (country, status) pairs are ignored; an empty result clears the widget.
     *
     * Returns whether at least one zone landed on the map. The bundled 1:110m
     * geometry has no polygon for small island states (Singapore, Maldives,
     * Malta…), so a range made only of those places nothing — callers should
     * hide the section rather than show a map that highlights nothing.
     */
    fun setZones(value: List<CountryZone>): Boolean {
        zones = value
            .filter { it.country.isNotBlank() }
            .distinctBy {
                "${it.countryCode?.trim()?.uppercase(Locale.ROOT).orEmpty()}:${normalizeMapName(it.country)}"
            }
            .take(6)
        clearSelectedCountry()
        val placed = mapCanvas.setZones(zones)
        renderCountryChips(mapCanvas.matches)
        // Name the countries from the bundled bilingual geometry, not from the
        // wire payload: the species profile is cached under a locale-free key,
        // so zone.country can still be English after a language switch.
        mapCanvas.contentDescription =
            if (placed) {
                context.getString(
                    R.string.native_range_map_content_description,
                    mapCanvas.matches.joinToString { it.displayName },
                )
            } else {
                context.getString(R.string.native_range_map_empty)
            }
        return placed
    }

    private fun renderCountryChips(matches: List<RangeMatch>) {
        countryChipRow.removeAllViews()
        matches.forEach { countryChipRow.addView(countryChip(it)) }
        countryChipScroll.isVisible = matches.isNotEmpty()
    }

    /** A tappable/focusable stand-in for pointing at the country on canvas. */
    private fun countryChip(match: RangeMatch): TextView {
        val status = statusLabel(match.source.status)
        return TextView(context).apply {
            text = context.getString(
                R.string.native_range_country_chip,
                match.displayName,
                status,
            )
            contentDescription = context.getString(
                R.string.native_range_country_accessibility,
                match.displayName,
                status,
            )
            maxLines = 1
            gravity = Gravity.CENTER
            minHeight = dp(48f).toInt()
            setPadding(
                dp(14f).toInt(),
                dp(8f).toInt(),
                dp(14f).toInt(),
                dp(8f).toInt(),
            )
            textSize = 13f
            isClickable = true
            isFocusable = true
            applyChipColors(this, match.source.status)
            layoutParams = LayoutParams(
                LayoutParams.WRAP_CONTENT,
                LayoutParams.WRAP_CONTENT,
            ).apply {
                marginEnd = dp(8f).toInt()
            }
            setOnClickListener { mapCanvas.selectCountry(match.geometry.code) }
        }
    }

    private fun clearSelectedCountry() {
        selectionHint.isVisible = true
        selectedChip.isVisible = false
    }

    private fun showSelectedCountry(name: String, status: ZoneStatus?) {
        selectionHint.isVisible = false
        if (status == null) {
            selectedChip.text = context.getString(
                R.string.native_range_outside_country,
                name,
            )
            applyNeutralChipColors(selectedChip)
            selectedChip.contentDescription = context.getString(
                R.string.native_range_outside_country_accessibility,
                name,
            )
        } else {
            selectedChip.text = context.getString(
                R.string.native_range_selected_country,
                name,
                statusLabel(status),
            )
            applyChipColors(selectedChip, status)
            selectedChip.contentDescription = context.getString(
                R.string.native_range_selected_country_accessibility,
                name,
                statusLabel(status),
            )
        }
        selectedChip.isVisible = true
    }

    private fun applyChipColors(chip: TextView, status: ZoneStatus) {
        val background = when (status) {
            ZoneStatus.GROWS_MORE -> color(R.color.primary_light)
            ZoneStatus.GROWS_LESS -> color(R.color.surface_variant)
        }
        val stroke = when (status) {
            ZoneStatus.GROWS_MORE -> color(R.color.primary)
            ZoneStatus.GROWS_LESS -> color(R.color.primary_variant)
        }
        chip.background = chipBackground(background, stroke)
        chip.setTextColor(color(R.color.on_surface))
    }

    private fun applyNeutralChipColors(chip: TextView) {
        chip.background = chipBackground(
            color(R.color.surface_variant),
            color(R.color.card_stroke),
        )
        chip.setTextColor(color(R.color.on_surface))
    }

    private fun chipBackground(fillColor: Int, strokeColor: Int) =
        GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = dp(12f)
            setColor(fillColor)
            setStroke(dp(1f).toInt().coerceAtLeast(1), strokeColor)
        }

    private fun statusLabel(status: ZoneStatus): String =
        context.getString(
            when (status) {
                ZoneStatus.GROWS_MORE -> R.string.native_range_grows_more
                ZoneStatus.GROWS_LESS -> R.string.native_range_grows_less
            },
        )

    private fun color(resource: Int): Int = ContextCompat.getColor(context, resource)

    private fun dp(value: Float): Float = value * resources.displayMetrics.density

    private class MapCanvasView(context: Context) : View(context) {
        private val data = WorldMapAsset.load(context)
        private val backgroundPaint = Paint(Paint.ANTI_ALIAS_FLAG)
        private val countryPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.FILL
        }
        private val borderPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
        }
        private val selectedPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
        }
        private val hatchPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
        }
        private val mapRect = RectF()
        private val clipPath = Path()
        private var projection: WorldMapProjection? = null
        private var renderedCountries: List<RenderedCountry> = emptyList()
        private var suppliedZones: List<CountryZone> = emptyList()

        /** Zones that resolved to bundled geometry, in the order supplied. */
        var matches: List<RangeMatch> = emptyList()
            private set
        private var selectedCode: String? = null
        private var onCountrySelected: ((String, ZoneStatus?) -> Unit)? = null
        private var onSelectionCleared: (() -> Unit)? = null

        init {
            isClickable = true
            importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_YES
        }

        fun setOnCountrySelectedListener(listener: (String, ZoneStatus?) -> Unit) {
            onCountrySelected = listener
        }

        fun setOnSelectionClearedListener(listener: () -> Unit) {
            onSelectionCleared = listener
        }

        /** Returns whether any supplied zone resolved to bundled geometry. */
        fun setZones(zones: List<CountryZone>): Boolean {
            suppliedZones = zones
            matches = zones.flatMap { zone ->
                val matchedCountries = zone.countryCode
                    ?.takeIf { it.isNotBlank() }
                    ?.let(data::match)
                    .orEmpty()
                    .ifEmpty { data.match(zone.country) }
                matchedCountries.map { country ->
                    RangeMatch(
                        geometry = country,
                        source = zone,
                        displayName = data.displayName(country),
                    )
                }
            }.distinctBy { "${it.geometry.code}:${it.source.status.wireName}" }
            selectedCode = null
            rebuildProjection()
            invalidate()
            return matches.isNotEmpty()
        }

        /** Selects a highlighted country from outside the canvas (the chips). */
        fun selectCountry(code: String) {
            val match = matches.firstOrNull { it.geometry.code == code } ?: return
            selectedCode = code
            onCountrySelected?.invoke(match.displayName, match.source.status)
            invalidate()
        }

        override fun onSizeChanged(width: Int, height: Int, oldWidth: Int, oldHeight: Int) {
            super.onSizeChanged(width, height, oldWidth, oldHeight)
            rebuildProjection()
        }

        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            if (width <= 0 || height <= 0 || data.countries.isEmpty()) return
            val radius = dp(18f)
            mapRect.set(0f, 0f, width.toFloat(), height.toFloat())
            backgroundPaint.shader = LinearGradient(
                0f,
                0f,
                width.toFloat(),
                height.toFloat(),
                color(R.color.surface_variant),
                color(R.color.surface),
                Shader.TileMode.CLAMP,
            )
            canvas.drawRoundRect(mapRect, radius, radius, backgroundPaint)

            clipPath.reset()
            clipPath.addRoundRect(mapRect, radius, radius, Path.Direction.CW)
            val save = canvas.save()
            canvas.clipPath(clipPath)

            if (projection == null) {
                canvas.restoreToCount(save)
                return
            }
            val highlighted = matches.associateBy { it.geometry.code }
            renderedCountries.forEach { rendered ->
                val country = rendered.geometry
                val path = rendered.path
                val match = highlighted[country.code]
                countryPaint.color = when (match?.source?.status) {
                    ZoneStatus.GROWS_MORE -> color(R.color.primary)
                    ZoneStatus.GROWS_LESS -> color(R.color.primary_light)
                    null -> color(R.color.surface_container)
                }
                canvas.drawPath(path, countryPaint)
                borderPaint.color = color(R.color.card_stroke)
                borderPaint.strokeWidth = dp(if (match == null) 0.45f else 0.9f)
                canvas.drawPath(path, borderPaint)
                if (match?.source?.status == ZoneStatus.GROWS_LESS) {
                    drawHatching(canvas, path)
                }
                if (selectedCode == country.code) {
                    selectedPaint.color = color(R.color.on_surface)
                    selectedPaint.strokeWidth = dp(2.2f)
                    canvas.drawPath(path, selectedPaint)
                }
            }
            canvas.restoreToCount(save)

            borderPaint.color = color(R.color.card_stroke)
            borderPaint.strokeWidth = dp(1f)
            canvas.drawRoundRect(mapRect, radius, radius, borderPaint)
        }

        private fun drawHatching(canvas: Canvas, path: Path) {
            val save = canvas.save()
            canvas.clipPath(path)
            hatchPaint.color = color(R.color.primary_variant)
            hatchPaint.alpha = 150
            hatchPaint.strokeWidth = dp(0.8f)
            val step = dp(9f)
            var x = -height.toFloat()
            while (x < width.toFloat()) {
                canvas.drawLine(x, height.toFloat(), x + height, 0f, hatchPaint)
                x += step
            }
            hatchPaint.alpha = 255
            canvas.restoreToCount(save)
        }

        override fun onTouchEvent(event: MotionEvent): Boolean {
            if (event.action != MotionEvent.ACTION_UP) return true
            performClick()
            // Hit-test the exact Paths used by onDraw. Geographic hit-testing
            // used to wrap every polygon vertex independently, so a far-away
            // country could stretch across a focused viewport (for example a
            // USA tap resolving to China on an Americas map).
            val hit = renderedCountries
                .asSequence()
                .filter {
                    it.hitRegion.contains(event.x.roundToInt(), event.y.roundToInt())
                }
                .minByOrNull { it.geometry.areaHint }
            if (hit != null) {
                selectedCode = hit.geometry.code
                val match = matches.firstOrNull { it.geometry.code == hit.geometry.code }
                val displayName = match?.displayName ?: data.displayName(hit.geometry)
                onCountrySelected?.invoke(displayName, match?.source?.status)
            } else {
                selectedCode = null
                onSelectionCleared?.invoke()
            }
            invalidate()
            return true
        }

        override fun performClick(): Boolean {
            super.performClick()
            return true
        }

        private fun rebuildProjection() {
            // With no zones at all the widget still shows the whole globe. A
            // range that supplied zones but placed none of them draws nothing
            // instead: a world-fitted map highlighting nothing reads as a bug.
            val points = when {
                matches.isNotEmpty() -> matches.flatMap { it.geometry.allPoints }
                suppliedZones.isEmpty() -> data.countries.flatMap { it.allPoints }
                else -> emptyList()
            }
            if (width <= 0 || height <= 0 || points.isEmpty()) {
                projection = null
                renderedCountries = emptyList()
                return
            }
            val updatedProjection = WorldMapProjection.fit(
                points = points,
                width = width.toFloat(),
                height = height.toFloat(),
                padding = dp(14f),
            )
            projection = updatedProjection
            val viewport = Region(0, 0, width, height)
            renderedCountries = data.countries.map { country ->
                val path = country.toPath(updatedProjection)
                RenderedCountry(
                    geometry = country,
                    path = path,
                    hitRegion = Region().apply { setPath(path, viewport) },
                )
            }
        }

        private fun CountryGeometry.toPath(projection: WorldMapProjection): Path =
            Path().apply {
                fillType = Path.FillType.EVEN_ODD
                polygons.forEach { polygon ->
                    polygon.rings.forEach { ring ->
                        var previousDelta: Double? = null
                        ring.forEachIndexed { index, point ->
                            var delta = longitudeDelta(point.longitude, projection.centerLongitude)
                            previousDelta?.let { previous ->
                                delta = unwrapLongitudeDelta(delta, previous)
                            }
                            previousDelta = delta
                            val projected = projection.projectUnwrapped(delta, point.latitude)
                            if (index == 0) {
                                moveTo(projected.x, projected.y)
                            } else {
                                lineTo(projected.x, projected.y)
                            }
                        }
                        close()
                    }
                }
            }

        private fun color(resource: Int): Int = ContextCompat.getColor(context, resource)

        private fun dp(value: Float): Float = value * resources.displayMetrics.density
    }
}

internal data class GeoPoint(val longitude: Double, val latitude: Double)

internal data class CountryPolygon(val rings: List<List<GeoPoint>>)

internal data class CountryGeometry(
    val code: String,
    val nameEn: String,
    val nameRu: String,
    val aliases: Set<String>,
    val regions: Set<String>,
    val polygons: List<CountryPolygon>,
) {
    val allPoints: List<GeoPoint> by lazy {
        polygons.flatMap { polygon -> polygon.rings.flatten() }
    }

    val areaHint: Double by lazy {
        val points = allPoints
        if (points.isEmpty()) {
            Double.MAX_VALUE
        } else {
            val longitudes = CircularLongitudeBounds.from(points.map { it.longitude })
            val latitudeSpan =
                (points.maxOf { it.latitude } - points.minOf { it.latitude }).coerceAtLeast(0.001)
            longitudes.span.coerceAtLeast(0.001) * latitudeSpan
        }
    }
}

internal data class RangeMatch(
    val geometry: CountryGeometry,
    val source: WorldMapView.CountryZone,
    val displayName: String,
)

private data class RenderedCountry(
    val geometry: CountryGeometry,
    val path: Path,
    val hitRegion: Region,
)

/**
 * Aspect-preserving equirectangular-x / Web-Mercator-y projection. It unwraps
 * longitudes around the fitted range center, so Fiji/Russia and other
 * antimeridian ranges do not zoom out to an almost-global 360-degree span.
 */
internal data class WorldMapProjection(
    val centerLongitude: Double,
    private val centerMercatorY: Double,
    private val scale: Double,
    private val centerX: Double,
    private val centerY: Double,
) {
    fun projectUnwrapped(longitudeDelta: Double, latitude: Double): PointF {
        val x = centerX + longitudeDelta * scale
        val y = centerY - (mercatorY(latitude) - centerMercatorY) * scale
        return PointF(x.toFloat(), y.toFloat())
    }

    companion object {
        fun fit(
            points: List<GeoPoint>,
            width: Float,
            height: Float,
            padding: Float,
            minimumSpanDegrees: Double = 8.0,
        ): WorldMapProjection {
            require(points.isNotEmpty()) { "At least one point is required" }
            val longitudeBounds = CircularLongitudeBounds.from(points.map { it.longitude })
            val latitudeMin = points.minOf { it.latitude }.coerceAtLeast(-85.0)
            val latitudeMax = points.maxOf { it.latitude }.coerceAtMost(85.0)
            val mercatorMin = mercatorY(latitudeMin)
            val mercatorMax = mercatorY(latitudeMax)
            val longitudeSpan = max(longitudeBounds.span, minimumSpanDegrees)
            val mercatorSpan = max(mercatorMax - mercatorMin, minimumSpanDegrees)
            val availableWidth = max(1.0, width.toDouble() - padding * 2.0)
            val availableHeight = max(1.0, height.toDouble() - padding * 2.0)
            val scale = min(availableWidth / longitudeSpan, availableHeight / mercatorSpan)
            return WorldMapProjection(
                centerLongitude = longitudeBounds.center,
                centerMercatorY = (mercatorMin + mercatorMax) / 2.0,
                scale = scale,
                centerX = width / 2.0,
                centerY = height / 2.0,
            )
        }
    }
}

internal data class CircularLongitudeBounds(
    val center: Double,
    val span: Double,
) {
    companion object {
        fun from(values: List<Double>): CircularLongitudeBounds {
            require(values.isNotEmpty()) { "At least one longitude is required" }
            if (values.size == 1) return CircularLongitudeBounds(wrapLongitude(values[0]), 0.0)
            val sorted = values.map(::wrapLongitude360).sorted()
            var largestGap = -1.0
            var gapStartIndex = 0
            sorted.indices.forEach { index ->
                val current = sorted[index]
                val next = if (index == sorted.lastIndex) sorted.first() + 360.0 else sorted[index + 1]
                val gap = next - current
                if (gap > largestGap) {
                    largestGap = gap
                    gapStartIndex = index
                }
            }
            val start = if (gapStartIndex == sorted.lastIndex) {
                sorted.first()
            } else {
                sorted[gapStartIndex + 1]
            }
            val span = (360.0 - largestGap).coerceIn(0.0, 360.0)
            return CircularLongitudeBounds(
                center = wrapLongitude(start + span / 2.0),
                span = span,
            )
        }
    }
}

private fun mercatorY(latitude: Double): Double {
    val clamped = latitude.coerceIn(-85.0, 85.0)
    return ln(tan(PI / 4.0 + Math.toRadians(clamped) / 2.0)) * 180.0 / PI
}

private fun longitudeDelta(longitude: Double, center: Double): Double =
    wrapLongitude(longitude - center)

internal fun unwrapLongitudeDelta(delta: Double, previous: Double): Double {
    var value = delta
    while (value - previous > 180.0) value -= 360.0
    while (value - previous < -180.0) value += 360.0
    return value
}

private fun wrapLongitude(longitude: Double): Double {
    var value = (longitude + 180.0) % 360.0
    if (value < 0.0) value += 360.0
    return value - 180.0
}

private fun wrapLongitude360(longitude: Double): Double {
    var value = longitude % 360.0
    if (value < 0.0) value += 360.0
    return value
}

internal fun normalizeMapName(value: String): String =
    Normalizer.normalize(value, Normalizer.Form.NFD)
        .replace(Regex("\\p{M}+"), "")
        .lowercase(Locale.ROOT)
        .replace("&", " and ")
        .replace(Regex("[^\\p{L}\\p{N}]+"), " ")
        .trim()
        .replace(Regex("\\s+"), " ")

internal class WorldMapData(val countries: List<CountryGeometry>) {
    private val byCode = countries.associateBy { it.code }
    private val identityIndex: Map<String, List<CountryGeometry>> =
        countries
            .flatMap { country -> country.aliases.map { normalizeMapName(it) to country } }
            .filter { (key, _) -> key.isNotBlank() }
            .groupBy({ it.first }, { it.second })
            // Several aliases of one country can normalize to the same key
            // ("CAN" and the "Can." abbreviation), which would otherwise make
            // a plain single-country hit look ambiguous below.
            .mapValues { (_, matched) -> matched.distinctBy(CountryGeometry::code) }
    private val regionIndex: Map<String, List<CountryGeometry>> = buildMap {
        countries.forEach { country ->
            country.regions.forEach { region ->
                val key = normalizeMapName(region)
                if (key.isNotBlank()) put(key, getOrElse(key) { emptyList() } + country)
            }
        }
    }

    fun displayName(country: CountryGeometry): String {
        val russian = Locale.getDefault().language == "ru"
        return if (russian) country.nameRu else country.nameEn
    }

    fun match(label: String): List<CountryGeometry> {
        val normalized = normalizeMapName(label)
        if (normalized.isBlank()) return emptyList()
        resolveIdentity(normalized)?.let { return it }
        COUNTRY_ALIASES[normalized]?.let { code -> byCode[code]?.let { return listOf(it) } }

        val regionKey = REGION_ALIASES[normalized] ?: normalized
        regionIndex[regionKey]?.let { return it }
        CUSTOM_REGIONS[regionKey]?.let { codes ->
            return codes.mapNotNull(byCode::get)
        }

        // The AI occasionally qualifies a real country ("southern Brazil",
        // "Mexico's tropical coast"). Prefer the longest whole-label match.
        val embedded = identityIndex.keys
            .asSequence()
            .filter { it.length >= 4 && Regex("(^| )${Regex.escape(it)}( |$)").containsMatchIn(normalized) }
            .maxByOrNull(String::length)
        return embedded?.let(::resolveIdentity).orEmpty()
    }

    /**
     * The shipped asset packs Natural Earth's SOVEREIGNT/SOV_A3 as identity
     * aliases (see tools/build_world_map_asset.py, which no longer does), so
     * "France" also names its dependencies and "Denmark" names Greenland.
     * Prefer the country that owns the label as its own code or bundled name;
     * only a label no single country claims keeps the whole group.
     */
    private fun resolveIdentity(normalized: String): List<CountryGeometry>? {
        val candidates = identityIndex[normalized] ?: return null
        if (candidates.size == 1) return candidates
        val owners = candidates.filter {
            normalizeMapName(it.code) == normalized ||
                normalizeMapName(it.nameEn) == normalized ||
                normalizeMapName(it.nameRu) == normalized
        }
        return if (owners.size == 1) owners else candidates
    }
}

internal object WorldMapAsset {
    private const val FILE_NAME = "natural_earth_countries.bin"
    private const val EXPECTED_MAGIC = 0x4E45574D
    private const val EXPECTED_VERSION = 1
    private const val COORDINATE_SCALE = 100_000.0

    @Volatile
    private var cached: WorldMapData? = null

    fun load(context: Context): WorldMapData =
        cached ?: synchronized(this) {
            cached ?: runCatching {
                DataInputStream(BufferedInputStream(context.assets.open(FILE_NAME))).use(::read)
            }.getOrElse { WorldMapData(emptyList()) }.also { cached = it }
        }

    private fun read(input: DataInputStream): WorldMapData {
        check(input.readInt() == EXPECTED_MAGIC) { "Invalid world map asset" }
        check(input.readUnsignedByte() == EXPECTED_VERSION) { "Unsupported world map asset" }
        val countries = List(input.readUnsignedShort()) {
            val code = input.readText()
            val nameEn = input.readText()
            val nameRu = input.readText()
            val aliases = input.readTextList().toSet()
            val regions = input.readTextList().map(::normalizeMapName).toSet()
            val polygons = List(input.readUnsignedShort()) {
                CountryPolygon(
                    rings = List(input.readUnsignedShort()) {
                        List(input.readUnsignedShort()) {
                            GeoPoint(
                                longitude = input.readInt() / COORDINATE_SCALE,
                                latitude = input.readInt() / COORDINATE_SCALE,
                            )
                        }
                    },
                )
            }
            CountryGeometry(code, nameEn, nameRu, aliases, regions, polygons)
        }
        return WorldMapData(countries)
    }

    private fun DataInputStream.readText(): String {
        val bytes = ByteArray(readUnsignedShort())
        readFully(bytes)
        return bytes.toString(Charsets.UTF_8)
    }

    private fun DataInputStream.readTextList(): List<String> =
        List(readUnsignedByte()) { readText() }
}

private val COUNTRY_ALIASES = mapOf(
    "usa" to "USA",
    "us" to "USA",
    "united states" to "USA",
    "america" to "USA",
    "uk" to "GBR",
    "great britain" to "GBR",
    "britain" to "GBR",
    "ivory coast" to "CIV",
    "cote d ivoire" to "CIV",
    "czech republic" to "CZE",
    "dr congo" to "COD",
    "drc" to "COD",
    "south korea" to "KOR",
    "north korea" to "PRK",
    "laos" to "LAO",
    "brunei" to "BRN",
    "tanzania" to "TZA",
    "bolivia" to "BOL",
    "venezuela" to "VEN",
    "vietnam" to "VNM",
    "burma" to "MMR",
    "myanmar" to "MMR",
    "swaziland" to "SWZ",
    "eswatini" to "SWZ",
    "cape verde" to "CPV",
    "east timor" to "TLS",
    "timor leste" to "TLS",
)

private val REGION_ALIASES = mapOf(
    "southeast asia" to "south eastern asia",
    "south east asia" to "south eastern asia",
    "south asia" to "southern asia",
    "east asia" to "eastern asia",
    "west asia" to "western asia",
    "central africa" to "middle africa",
    "tropical africa" to "sub saharan africa",
    "latin america" to "latin america and caribbean",
    "latin america and the caribbean" to "latin america and caribbean",
    "юго восточная азия" to "south eastern asia",
    "южная азия" to "southern asia",
    "восточная азия" to "eastern asia",
    "западная азия" to "western asia",
    "центральная азия" to "central asia",
    "северная америка" to "north america",
    "южная америка" to "south america",
    "центральная америка" to "central america",
    "латинская америка" to "latin america and caribbean",
    "карибский бассейн" to "caribbean",
    "карибы" to "caribbean",
    "европа" to "europe",
    "азия" to "asia",
    "африка" to "africa",
    "океания" to "oceania",
    "восточная африка" to "eastern africa",
    "западная африка" to "western africa",
    "центральная африка" to "middle africa",
    "южная африка" to "southern africa",
    "северная африка" to "northern africa",
    "тропическая африка" to "sub saharan africa",
    "меланезия" to "melanesia",
    "микронезия" to "micronesia",
    "полинезия" to "polynesia",
)

private val CUSTOM_REGIONS = mapOf(
    "amazon basin" to setOf("BRA", "PER", "COL", "BOL", "ECU", "VEN", "GUY", "SUR"),
    "amazon rainforest" to setOf("BRA", "PER", "COL", "BOL", "ECU", "VEN", "GUY", "SUR"),
    "амазония" to setOf("BRA", "PER", "COL", "BOL", "ECU", "VEN", "GUY", "SUR"),
    "бассейн амазонки" to setOf("BRA", "PER", "COL", "BOL", "ECU", "VEN", "GUY", "SUR"),
    "mediterranean" to setOf(
        "PRT", "ESP", "FRA", "ITA", "SVN", "HRV", "BIH", "MNE", "ALB", "GRC",
        "TUR", "CYP", "SYR", "LBN", "ISR", "EGY", "LBY", "TUN", "DZA", "MAR",
    ),
    "mediterranean region" to setOf(
        "PRT", "ESP", "FRA", "ITA", "GRC", "TUR", "CYP", "ISR", "EGY", "TUN", "DZA", "MAR",
    ),
    "средиземноморье" to setOf(
        "PRT", "ESP", "FRA", "ITA", "GRC", "TUR", "CYP", "ISR", "EGY", "TUN", "DZA", "MAR",
    ),
    "himalayas" to setOf("IND", "NPL", "BTN", "CHN", "PAK"),
    "himalayan region" to setOf("IND", "NPL", "BTN", "CHN", "PAK"),
    "гималаи" to setOf("IND", "NPL", "BTN", "CHN", "PAK"),
)
