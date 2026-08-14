package com.plantdiseases.app.ui.community

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.Drawable
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updateLayoutParams
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.bumptech.glide.request.target.CustomTarget
import com.bumptech.glide.request.transition.Transition
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityHeatmapResponse
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityCommunityMapBinding
import com.plantdiseases.app.ui.result.CommunityOpinionSheet
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.ConnectivityObserver
import com.plantdiseases.app.util.DiseaseCatalog
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.RegionResolver
import com.plantdiseases.app.util.ServerConfig
import com.plantdiseases.app.util.TelemetryIds
import com.plantdiseases.app.util.onReconnected
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import org.osmdroid.config.Configuration
import org.osmdroid.events.DelayedMapListener
import org.osmdroid.events.MapListener
import org.osmdroid.events.ScrollEvent
import org.osmdroid.events.ZoomEvent
import org.osmdroid.tileprovider.tilesource.TileSourceFactory
import org.osmdroid.util.GeoPoint
import org.osmdroid.views.CustomZoomButtonsController
import org.osmdroid.views.overlay.Marker
import org.osmdroid.views.overlay.Polygon
import java.io.File
import javax.inject.Inject

/**
 * Full-screen community scans map (osmdroid). Zoomed out it paints a
 * k-anonymized density heat (/api/community/heatmap); zoomed in it drops a
 * marker per scan (/api/community/map). Tapping a scan opens the existing
 * [CommunityOpinionSheet]; a cluster of scans at the same ~1 km point opens a
 * list of them. Viewing the (anonymized, aggregate) map needs no opt-in; the
 * "near me" FAB uses coarse location and never flips upload sharing on.
 */
@AndroidEntryPoint
class CommunityMapActivity : AppCompatActivity() {

    private lateinit var binding: ActivityCommunityMapBinding

    @Inject
    lateinit var scanRepository: ScanRepository

    @Inject
    lateinit var connectivity: ConnectivityObserver
    private val isRu: Boolean get() = LocaleHelper.isRussian(this)
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
        binding.offlineNotice.observe(this, connectivity.isOnline)
        binding.fabNearMe.setOnClickListener { onNearMe() }
        // Pick the markers back up as soon as the server is reachable — the
        // pans made while offline deliberately fetched nothing.
        onReconnected(connectivity.isOnline) { reload() }
        applyWindowInsets()

        binding.map.apply {
            setTileSource(TileSourceFactory.MAPNIK)
            setMultiTouchControls(true)
            // Hide osmdroid's built-in +/- buttons — they sit at the bottom and
            // collide with the app's bottom nav bar. Pinch/double-tap zoom stays.
            zoomController.setVisibility(CustomZoomButtonsController.Visibility.NEVER)
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
                // When the user is in the community, open straight to their area
                // (coarse location) instead of the world view; otherwise stay on
                // the world map. Zooming to the user re-triggers reload() via the
                // debounced map listener, so the markers near them load too.
                if (CommunityPrefs.uploadsEnabled(this@CommunityMapActivity)) {
                    centerOnUser(animateZoom = true)
                }
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
        // Every pan and zoom lands here through the debounced map listener.
        // Offline that would queue a doomed request — and pay its connect
        // timeout — for each gesture, so don't start one; the strip already
        // says why the map has no markers. onReconnected reloads once the
        // server is reachable again.
        if (!connectivity.lastKnownOnline) {
            binding.mapProgress.visibility = View.GONE
            return
        }
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

    /** Inset the toolbar below the status bar and lift the FAB above the nav
     *  bar. The map itself stays edge-to-edge (full-bleed); only its chrome
     *  must not hide under the system bars. */
    private fun applyWindowInsets() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val fabBaseMargin = resources.getDimensionPixelSize(R.dimen.space_lg)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            binding.appBar.updatePadding(top = bars.top)
            binding.fabNearMe.updateLayoutParams<ViewGroup.MarginLayoutParams> {
                bottomMargin = fabBaseMargin + bars.bottom
            }
            insets
        }
    }

    /** Zoom-aware markers. Scans are bucketed by a fixed SCREEN-pixel grid, so
     *  pins that would visually overlap at the current zoom collapse into one
     *  cluster; zooming in shrinks each cell in world terms, so clusters break
     *  apart and spread. A lone scan shows its actual thumbnail; a cluster
     *  shows a count badge. */
    private fun drawMarkers(items: List<CommunityScanItem>) {
        binding.map.overlays.clear()
        val located = items.filter { it.lat != null && it.lon != null }
        if (located.isEmpty()) {
            binding.map.invalidate()
            return
        }
        val projection = binding.map.projection
        val cellPx = (CLUSTER_CELL_DP * resources.displayMetrics.density).toInt().coerceAtLeast(1)
        val buckets = LinkedHashMap<Long, MutableList<CommunityScanItem>>()
        for (item in located) {
            val pt = projection.toPixels(GeoPoint(item.lat!!, item.lon!!), null)
            val gx = Math.floorDiv(pt.x, cellPx).toLong()
            val gy = Math.floorDiv(pt.y, cellPx).toLong()
            val key = (gx shl 32) xor (gy and 0xffffffffL)
            buckets.getOrPut(key) { mutableListOf() }.add(item)
        }
        for (group in buckets.values) {
            if (group.size == 1) addScanMarker(group.first()) else addClusterMarker(group)
        }
        binding.map.invalidate()
    }

    /** A single scan: disease-icon pin first, swapped for the real thumbnail
     *  once Glide loads it (so close-up the map shows the actual scans). */
    private fun addScanMarker(item: CommunityScanItem) {
        val marker = Marker(binding.map).apply {
            position = GeoPoint(item.lat!!, item.lon!!)
            setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_CENTER)
            icon = markerIcon(item.disease)
            setOnMarkerClickListener { _, _ ->
                openScan(item)
                true
            }
        }
        binding.map.overlays.add(marker)
        loadThumbInto(marker, item)
    }

    /** Several scans collapsed at this zoom: a count badge that lists the group
     *  on tap. (Pinch/double-tap zoom shrinks the grid cells so the pins also
     *  fan apart on their own.) */
    private fun addClusterMarker(group: List<CommunityScanItem>) {
        val avgLat = group.mapNotNull { it.lat }.average()
        val avgLon = group.mapNotNull { it.lon }.average()
        val marker = Marker(binding.map).apply {
            position = GeoPoint(avgLat, avgLon)
            setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_CENTER)
            icon = clusterIcon(group.size)
            setOnMarkerClickListener { _, _ ->
                openGroup(group)
                true
            }
        }
        binding.map.overlays.add(marker)
    }

    private fun loadThumbInto(marker: Marker, item: CommunityScanItem) {
        val thumb = item.thumbPath
        if (thumb.isNullOrBlank()) return
        val url = "${ServerConfig.baseUrl}api/community/thumb/$thumb"
        val sizePx = (MARKER_SIZE_DP * resources.displayMetrics.density).toInt().coerceAtLeast(1)
        Glide.with(this).asBitmap().load(url).circleCrop().override(sizePx, sizePx)
            .into(object : CustomTarget<Bitmap>() {
                override fun onResourceReady(resource: Bitmap, transition: Transition<in Bitmap>?) {
                    if (isFinishing || isDestroyed) return
                    marker.icon = ringedThumb(resource)
                    binding.map.invalidate()
                }

                override fun onLoadCleared(placeholder: Drawable?) = Unit
            })
    }

    private fun markerIcon(disease: String): Drawable? {
        val res = DiseaseCatalog.guideItemForClass(disease)?.iconRes ?: R.drawable.ic_plant_placeholder
        return ContextCompat.getDrawable(this, res)?.mutate()?.also {
            it.setTint(ContextCompat.getColor(this, R.color.disease_red))
        }
    }

    /** Wrap a (circle-cropped) thumbnail in a white ring so it reads as a pin
     *  against busy map tiles. */
    private fun ringedThumb(src: Bitmap): Drawable {
        val density = resources.displayMetrics.density
        val size = (MARKER_SIZE_DP * density).toInt().coerceAtLeast(1)
        val out = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(out)
        val c = size / 2f
        canvas.drawCircle(c, c, c, Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE })
        val inset = 2f * density
        val dst = RectF(inset, inset, size - inset, size - inset)
        canvas.drawBitmap(src, null, dst, Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG))
        return BitmapDrawable(resources, out)
    }

    /** A filled circle badge with the grouped-scan count. */
    private fun clusterIcon(count: Int): Drawable {
        val density = resources.displayMetrics.density
        val size = (MARKER_SIZE_DP * density).toInt().coerceAtLeast(1)
        val out = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(out)
        val c = size / 2f
        canvas.drawCircle(
            c,
            c,
            c - density,
            Paint(Paint.ANTI_ALIAS_FLAG).apply {
                color = ContextCompat.getColor(this@CommunityMapActivity, R.color.primary)
            },
        )
        canvas.drawCircle(
            c,
            c,
            c - density,
            Paint(Paint.ANTI_ALIAS_FLAG).apply {
                color = Color.WHITE
                style = Paint.Style.STROKE
                strokeWidth = 2f * density
            },
        )
        val label = if (count > 99) "99+" else count.toString()
        val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.WHITE
            textAlign = Paint.Align.CENTER
            isFakeBoldText = true
            textSize = (if (count > 99) 12f else 15f) * density
        }
        val baseline = c - (textPaint.descent() + textPaint.ascent()) / 2f
        canvas.drawText(label, c, baseline, textPaint)
        return BitmapDrawable(resources, out)
    }

    private fun openScan(item: CommunityScanItem) {
        CommunityOpinionSheet.show(
            supportFragmentManager,
            item,
            DiseaseLabels.displayName(item.disease, isRu) ?: item.disease,
        )
    }

    /** Optimistic like toggle for a row in the grouped-scans bottom sheet —
     *  same contract as [NearbyScansActivity]'s, just parameterised by which
     *  adapter instance owns the tapped row. */
    private fun toggleLike(item: CommunityScanItem, adapter: NearbyScansAdapter) {
        val next = !item.likedByMe
        val optimisticCount = (item.likeCount + if (next) 1 else -1).coerceAtLeast(0)
        adapter.updateLike(item.scanId, optimisticCount, next)
        lifecycleScope.launch {
            scanRepository.setCommunityLike(item.scanId, TelemetryIds.userPseudoId(this@CommunityMapActivity), next)
                .onSuccess { adapter.updateLike(item.scanId, it.count, it.liked) }
                .onFailure { adapter.updateLike(item.scanId, item.likeCount, item.likedByMe) }
        }
    }

    /** Bottom sheet listing the scans grouped at one point — reuses the feed
     *  adapter so each row opens the same opinion sheet. */
    private fun openGroup(group: List<CommunityScanItem>) {
        val dialog = BottomSheetDialog(this)
        val pad = (12 * resources.displayMetrics.density).toInt()
        val header = TextView(this).apply {
            text = getString(R.string.community_map_group_title, group.size)
            setPadding(pad + pad, pad + pad, pad, pad)
            setTextAppearance(R.style.TextAppearance_App_Title)
        }
        val list = RecyclerView(this).apply {
            layoutManager = LinearLayoutManager(this@CommunityMapActivity)
            setPadding(0, 0, 0, pad)
            clipToPadding = false
        }
        val adapter = NearbyScansAdapter(
            onOpen = { item ->
                dialog.dismiss()
                openScan(item)
            },
            onLike = { item -> toggleLike(item, list.adapter as NearbyScansAdapter) },
        )
        list.adapter = adapter
        adapter.submit(group)
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(header)
            addView(list)
        }
        dialog.setContentView(container)
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
        private const val MARKER_SIZE_DP = 40 // thumbnail + cluster-badge pin size

        // Screen-pixel grid: only pins whose icons would actually overlap merge.
        // Just over the marker size so near-but-distinct scans stay separate
        // (a larger value collapsed scans that weren't really close).
        private const val CLUSTER_CELL_DP = 46
    }
}
