package com.plantdiseases.app.ui.garden

import android.content.Context
import android.content.Intent
import android.graphics.Matrix
import android.os.Bundle
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.widget.FrameLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.recyclerview.widget.RecyclerView
import androidx.viewpager2.widget.ViewPager2
import com.bumptech.glide.Glide
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.ActivityPhotoViewerBinding
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.expandTouchTarget

/**
 * Full-screen swipeable photo viewer with pinch-to-zoom + pan.
 *
 * Pure-Kotlin / programmatic UI on top of `ViewPager2`. Pinch and pan
 * are implemented per-page so each photo zooms independently — when
 * the user pinches one photo to 2× and swipes, the next photo
 * arrives at its own 1× scale, not the previous page's residual
 * transform.
 *
 * The viewer is launched in two modes:
 *   * single photo — pass [EXTRA_URIS] with one entry. Swipe is
 *     disabled, page indicator is hidden.
 *   * gallery     — pass [EXTRA_URIS] (chronological) plus
 *     [EXTRA_START_INDEX] to land on the right page. Swipe is enabled,
 *     page indicator ("3 / 12") is shown.
 *
 * Photo sources are loaded via Glide so they accept both
 * ``file://`` paths from the scan store AND ``content://`` URIs from
 * picked journal photos with no special-casing.
 */
class PhotoViewerActivity : AppCompatActivity() {

    private lateinit var binding: ActivityPhotoViewerBinding
    private lateinit var pager: ViewPager2
    private lateinit var indicator: TextView
    private lateinit var uris: List<String>

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val passed = intent.getStringArrayListExtra(EXTRA_URIS) ?: arrayListOf()
        if (passed.isEmpty()) {
            finish()
            return
        }
        uris = passed
        val start = intent.getIntExtra(EXTRA_START_INDEX, 0).coerceIn(0, uris.size - 1)

        binding = ActivityPhotoViewerBinding.inflate(layoutInflater)
        setContentView(binding.root)

        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(0, bars.top, 0, bars.bottom)
            insets
        }

        pager = binding.photoPager
        pager.adapter = PhotoAdapter(uris)
        pager.isUserInputEnabled = uris.size > 1

        indicator = binding.photoIndicator
        binding.photoClose.setOnClickListener { finish() }
        // Accessibility: grow the 28dp close icon's hit area to 48dp without
        // changing its visible size.
        binding.photoClose.expandTouchTarget()

        pager.registerOnPageChangeCallback(object : ViewPager2.OnPageChangeCallback() {
            override fun onPageSelected(position: Int) {
                updateIndicator(position)
            }
        })
        pager.setCurrentItem(start, false)
        updateIndicator(start)
    }

    private fun updateIndicator(position: Int) {
        if (uris.size > 1) {
            indicator.visibility = View.VISIBLE
            indicator.text = getString(R.string.photo_viewer_indicator, position + 1, uris.size)
        } else {
            indicator.visibility = View.GONE
        }
    }

    // ── Adapter ──────────────────────────────────────────────────────

    private inner class PhotoAdapter(
        private val items: List<String>,
    ) : RecyclerView.Adapter<PhotoAdapter.PageHolder>() {

        override fun onCreateViewHolder(parent: android.view.ViewGroup, viewType: Int): PageHolder {
            val container = FrameLayout(parent.context).apply {
                layoutParams = ViewPager2_PAGE_PARAMS
            }
            val image = ZoomableImageView(parent.context).apply {
                layoutParams = FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.MATCH_PARENT,
                )
            }
            container.addView(image)
            return PageHolder(container, image)
        }

        override fun getItemCount(): Int = items.size

        override fun onBindViewHolder(holder: PageHolder, position: Int) {
            holder.image.reset()
            Glide.with(holder.image.context)
                .load(items[position])
                .into(holder.image)
        }

        inner class PageHolder(
            container: FrameLayout,
            val image: ZoomableImageView,
        ) : RecyclerView.ViewHolder(container)
    }

    companion object {
        const val EXTRA_URIS = "extra_uris"
        const val EXTRA_START_INDEX = "extra_start_index"

        fun open(context: Context, uris: List<String>, startIndex: Int = 0) {
            if (uris.isEmpty()) return
            val intent = Intent(context, PhotoViewerActivity::class.java)
                .putStringArrayListExtra(EXTRA_URIS, ArrayList(uris))
                .putExtra(EXTRA_START_INDEX, startIndex.coerceIn(0, uris.size - 1))
            context.startActivity(intent)
        }

        private val ViewPager2_PAGE_PARAMS = RecyclerView.LayoutParams(
            RecyclerView.LayoutParams.MATCH_PARENT,
            RecyclerView.LayoutParams.MATCH_PARENT,
        )
    }
}

/**
 * ImageView that supports pinch-to-zoom + pan via matrix transforms.
 * Reset on each rebind so paging into a new photo starts at 1×.
 */
class ZoomableImageView(context: Context) : androidx.appcompat.widget.AppCompatImageView(context) {

    private val matrixCurrent = Matrix()
    private var scale = 1f
    private var lastTouchX = 0f
    private var lastTouchY = 0f
    private val scaleDetector: ScaleGestureDetector

    init {
        scaleType = ScaleType.MATRIX
        scaleDetector = ScaleGestureDetector(
            context,
            object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
                override fun onScale(detector: ScaleGestureDetector): Boolean {
                    val factor = detector.scaleFactor
                    val newScale = (scale * factor).coerceIn(1f, 4f)
                    val realFactor = newScale / scale
                    scale = newScale
                    matrixCurrent.postScale(realFactor, realFactor, detector.focusX, detector.focusY)
                    imageMatrix = matrixCurrent
                    return true
                }
            }
        )
    }

    /** Reset transforms — called by the adapter on rebind so paging
     *  into a new photo starts at 1× regardless of the previous page's
     *  zoom level. */
    fun reset() {
        scale = 1f
        matrixCurrent.reset()
        // Letterbox-fit on the first layout pass.
        post {
            val d = drawable ?: return@post
            val vw = width.toFloat()
            val vh = height.toFloat()
            val dw = d.intrinsicWidth.toFloat().coerceAtLeast(1f)
            val dh = d.intrinsicHeight.toFloat().coerceAtLeast(1f)
            val baseScale = minOf(vw / dw, vh / dh)
            val tx = (vw - dw * baseScale) / 2f
            val ty = (vh - dh * baseScale) / 2f
            matrixCurrent.reset()
            matrixCurrent.postScale(baseScale, baseScale)
            matrixCurrent.postTranslate(tx, ty)
            imageMatrix = matrixCurrent
        }
    }

    override fun setImageDrawable(drawable: android.graphics.drawable.Drawable?) {
        super.setImageDrawable(drawable)
        // Glide delivers the bitmap asynchronously AFTER the adapter's reset()
        // ran (when drawable was still null), so the first letterbox-fit was
        // skipped and the photo drew top-left at intrinsic size until touched.
        // Re-fit when the drawable actually arrives (unless mid-zoom).
        if (drawable != null && scale <= 1.01f) reset()
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        scaleDetector.onTouchEvent(event)
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                lastTouchX = event.x
                lastTouchY = event.y
                // Claim the gesture from the pager once the user starts
                // zooming so a horizontal drag at >1x scale pans the
                // image instead of paging.
                parent?.requestDisallowInterceptTouchEvent(scale > 1.01f)
            }
            MotionEvent.ACTION_MOVE -> {
                if (scale > 1.01f && event.pointerCount == 1) {
                    val dx = event.x - lastTouchX
                    val dy = event.y - lastTouchY
                    matrixCurrent.postTranslate(dx, dy)
                    imageMatrix = matrixCurrent
                    lastTouchX = event.x
                    lastTouchY = event.y
                    parent?.requestDisallowInterceptTouchEvent(true)
                }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                parent?.requestDisallowInterceptTouchEvent(false)
                if (scale <= 1.01f) {
                    // Snap back to letterbox so any drift from sub-1×
                    // pinches doesn't leave the photo half-off-screen.
                    reset()
                }
            }
        }
        return true
    }
}
