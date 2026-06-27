package com.plantdiseases.app.ui.community

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.Drawable
import android.os.Bundle
import android.view.View
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityHeatmapResponse
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityCommunityMapBinding
import com.plantdiseases.app.ui.result.CommunityOpinionSheet
import com.plantdiseases.app.util.DiseaseCatalog
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.RegionResolver
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import org.koin.android.ext.android.inject
import org.osmdroid.config.Configuration
import org.osmdroid.events.DelayedMapListener
import org.osmdroid.events.MapListener
import org.osmdroid.events.ScrollEvent
import org.osmdroid.events.ZoomEvent
import org.osmdroid.tileprovider.tilesource.TileSourceFactory
import org.osmdroid.util.GeoPoint
import org.osmdroid.views.overlay.Marker
import org.osmdroid.views.overlay.Polygon
import java.io.File
import java.util.Locale

/**
 * Full-screen community scans map (osmdroid). Zoomed out it paints a
 * k-anonymized density heat (/api/community/heatmap); zoomed in it drops a
 * marker per scan (/api/community/map). Tapping a scan opens the existing
 * [CommunityOpinionSheet]; a cluster of scans at the same ~1 km point opens a
 * list of them. Viewing the (anonymized, aggregate) map needs no opt-in; the
 * "near me" FAB uses coarse location and never flips upload sharing on.
 */
class CommunityMapActivity : AppCompatActivity() {

    private lateinit var binding: ActivityCommunityMapBinding
    private val scanRepository: ScanRepository by inject()
    private val isRu: Boolean get() = Locale.getDefault().language == "ru"
    private var loadJob: Job? = null

    private val locationPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> if (granted) centerOnUser(animateZoom = true) }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Configure osmdroid before inflating the MapView. Cache under the app's
        // own cacheDir (no storage permission) + a descriptive User-Agent so OSM
        // tile servers don't reject us.
        val osmConfig = Configuration.getInstance()
        osmConfig.load(this, getSharedPreferences("osmdroid", MODE_PRIVATE))
        osmConfig.userAgentValue = packageName
        osmConfig.osmdroidBasePath = File(cacheDir, "osmdroid")
        osmConfig.osmdroidTileCache = File(osmConfig.osmdroidBasePath, "tiles")

        binding = ActivityCommunityMapBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.fabNearMe.setOnClickListener { onNearMe() }

        binding.map.apply {
            setTileSource(TileSourceFactory.MAPNIK)
            setMultiTouchControls(true)
            controller.setZoom(DEFAULT_ZOOM)
            controller.setCenter(GeoPoint(20.0, 0.0))
            // Debounced refetch on any pan/zoom (cancels the in-flight load).
            addMapListener(
                DelayedMapListener(
                    object : MapListener {
                        override fun onScroll(event: ScrollEvent?): Boolean {
                            reload()
                            return true
                        }

                        override fun onZoom(event: ZoomEvent?): Boolean {
                            reload()
                            return true
                        }
                    },
                    MAP_DEBOUNCE_MS,
                ),
            )
            // boundingBox is only valid after layout — first real load fires here.
            addOnFirstLayoutListener { _, _, _, _, _ ->
                reload()
                centerOnUser(animateZoom = false) // recenter only if coords are cached
            }
        }
    }

    override fun onResume() {
        super.onResume()
        binding.map.onResume()
    }

    override fun onPause() {
        super.onPause()
        binding.map.onPause()
    }

    override fun onDestroy() {
        super.onDestroy()
        loadJob?.cancel()
    }

    private fun reload() {
        val box = binding.map.boundingBox
        val zoom = binding.map.zoomLevelDouble
        loadJob?.cancel()
        binding.mapProgress.visibility = View.VISIBLE
        loadJob = lifecycleScope.launch {
            if (zoom >= MARKER_ZOOM) {
                val res = scanRepository.communityMap(
                    minLat = box.latSouth,
                    minLon = box.lonWest,
                    maxLat = box.latNorth,
                    maxLon = box.lonEast,
                ).getOrNull()
                binding.mapProgress.visibility = View.GONE
                val items = res?.items.orEmpty()
                drawMarkers(items)
                showHint(
                    when {
                        res?.truncated == true -> R.string.community_map_truncated
                        items.isEmpty() -> R.string.community_map_empty
                        else -> 0
                    },
                )
            } else {
                val res = scanRepository.communityHeatmap(
                    minLat = box.latSouth,
                    minLon = box.lonWest,
                    maxLat = box.latNorth,
                    maxLon = box.lonEast,
                ).getOrNull()
                binding.mapProgress.visibility = View.GONE
                drawHeat(res)
                showHint(
                    if (res == null || res.cells.isEmpty()) {
                        R.string.community_map_empty
                    } else {
                        R.string.community_map_zoom_in
                    },
                )
            }
        }
    }

    /** One translucent square per density cell, green→red by relative count. */
    private fun drawHeat(res: CommunityHeatmapResponse?) {
        binding.map.overlays.clear()
        val size = res?.cellSizeDeg ?: 0.0
        val maxCount = (res?.maxCount ?: 0).coerceAtLeast(1)
        res?.cells.orEmpty().forEach { cell ->
            val poly = Polygon(binding.map).apply {
                points = listOf(
                    GeoPoint(cell.lat, cell.lon),
                    GeoPoint(cell.lat, cell.lon + size),
                    GeoPoint(cell.lat + size, cell.lon + size),
                    GeoPoint(cell.lat + size, cell.lon),
                )
                fillPaint.color = heatColor(cell.count.toFloat() / maxCount)
                outlinePaint.color = Color.TRANSPARENT
                outlinePaint.strokeWidth = 0f
            }
            binding.map.overlays.add(poly)
        }
        binding.map.invalidate()
    }

    /** One marker per scan; scans at the same ~1 km point collapse to a single
     *  marker that opens the group list on tap. */
    private fun drawMarkers(items: List<CommunityScanItem>) {
        binding.map.overlays.clear()
        items.filter { it.lat != null && it.lon != null }
            .groupBy { it.lat to it.lon }
            .forEach { (coord, group) ->
                val marker = Marker(binding.map).apply {
                    position = GeoPoint(coord.first!!, coord.second!!)
                    setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_BOTTOM)
                    icon = markerIcon(group.first().disease)
                    if (group.size > 1) {
                        title = getString(R.string.community_map_group_title, group.size)
                    }
                    setOnMarkerClickListener { _, _ ->
                        if (group.size == 1) openScan(group.first()) else openGroup(group)
                        true
                    }
                }
                binding.map.overlays.add(marker)
            }
        binding.map.invalidate()
    }

    private fun markerIcon(disease: String): Drawable? {
        val res = DiseaseCatalog.guideItemForClass(disease)?.iconRes ?: R.drawable.ic_plant_placeholder
        return ContextCompat.getDrawable(this, res)?.mutate()?.also {
            it.setTint(ContextCompat.getColor(this, R.color.disease_red))
        }
    }

    private fun openScan(item: CommunityScanItem) {
        CommunityOpinionSheet.show(
            supportFragmentManager,
            item,
            DiseaseLabels.displayName(item.disease, isRu) ?: item.disease,
        )
    }

    /** Bottom sheet listing the scans grouped at one point — reuses the feed
     *  adapter so each row opens the same opinion sheet. */
    private fun openGroup(group: List<CommunityScanItem>) {
        val dialog = BottomSheetDialog(this)
        val pad = (12 * resources.displayMetrics.density).toInt()
        val list = RecyclerView(this).apply {
            layoutManager = LinearLayoutManager(this@CommunityMapActivity)
            setPadding(0, pad, 0, pad)
            clipToPadding = false
        }
        val adapter = NearbyScansAdapter(
            displayName = { key -> DiseaseLabels.displayName(key, isRu) ?: key },
            onSecondOpinion = { item ->
                dialog.dismiss()
                openScan(item)
            },
        )
        list.adapter = adapter
        adapter.submit(group)
        dialog.setContentView(list)
        dialog.show()
    }

    private fun onNearMe() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION)
            != PackageManager.PERMISSION_GRANTED
        ) {
            locationPermission.launch(Manifest.permission.ACCESS_COARSE_LOCATION)
        } else {
            centerOnUser(animateZoom = true)
        }
    }

    /** Center on the user's coarse location when available. Viewing the public
     *  map never requires opt-in; this only reads an already-resolvable coarse
     *  fix (cached or freshly permitted) and does NOT enable upload sharing. */
    private fun centerOnUser(animateZoom: Boolean) {
        lifecycleScope.launch {
            val region = runCatching {
                RegionResolver.resolveRegion(this@CommunityMapActivity)
            }.getOrNull()
            val lat = region?.lat
            val lon = region?.lon
            if (lat != null && lon != null) {
                if (animateZoom) binding.map.controller.setZoom(NEAR_ME_ZOOM)
                binding.map.controller.animateTo(GeoPoint(lat, lon))
            }
        }
    }

    private fun showHint(resId: Int) {
        if (resId == 0) {
            binding.tvMapHint.visibility = View.GONE
        } else {
            binding.tvMapHint.setText(resId)
            binding.tvMapHint.visibility = View.VISIBLE
        }
    }

    /** Green (sparse) → amber → red (dense), with alpha rising with density. */
    private fun heatColor(fraction: Float): Int {
        val t = fraction.coerceIn(0f, 1f)
        val r = (255 * t).toInt()
        val g = (200 * (1f - t)).toInt()
        val alpha = (90 + 120 * t).toInt()
        return Color.argb(alpha, r, g, 40)
    }

    companion object {
        private const val DEFAULT_ZOOM = 4.0
        private const val NEAR_ME_ZOOM = 11.0
        private const val MARKER_ZOOM = 11.0 // at/above → individual markers; below → heat
        private const val MAP_DEBOUNCE_MS = 400L
    }
}
