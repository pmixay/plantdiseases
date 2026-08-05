package com.plantdiseases.app.ui.crop

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import androidx.core.content.ContextCompat
import com.plantdiseases.app.R
import kotlin.math.hypot
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * A hand-rolled crop view — the replacement for the deprecated
 * android-image-cropper library. It shows the source bitmap letterbox-fit to
 * fill the available viewport, overlays a free-form crop rectangle (8 drag
 * handles + a movable interior) with rule-of-thirds guides and an
 * outside-the-crop scrim, and returns the cropped region at full source
 * resolution via [getCroppedBitmap].
 *
 * Rotation is baked into a [workingBitmap] derived from the pristine
 * [sourceBitmap], so export needs no post-rotate and at most two bitmaps are
 * ever alive. Coordinate math mirrors the repo's existing letterbox-fit
 * (ZoomableImageView) and view↔pixel mapping (HeatmapOverlayView) patterns.
 */
class CropImageView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {

    private enum class Handle { TL, TR, BL, BR, T, B, L, R, INTERIOR, NONE }

    /** Pristine, EXIF-corrected decode. Never mutated. */
    private var sourceBitmap: Bitmap? = null

    /** [sourceBitmap] rotated by [rotationDegrees]; what we draw and crop from. */
    private var workingBitmap: Bitmap? = null
    private var rotationDegrees = 0

    /** Displayed image bounds (view coords, includes letterbox offset). */
    private val imageRect = RectF()

    /** Crop window (view coords), always kept ⊆ [imageRect]. */
    private val cropRect = RectF()
    private var fitScale = 1f
    private var cropInitialized = false

    /** Normalized (0..1 of imageRect) crop rect to apply once we have a size. */
    private var pendingRestore: RectF? = null

    private var activeHandle = Handle.NONE
    private var lastX = 0f
    private var lastY = 0f

    private val density = resources.displayMetrics.density
    private val handleTouchRadius = 24f * density
    private val minCropPx = 56f * density
    private val handleLen = 18f * density

    // Paints built once — onDraw allocates nothing.
    private val bitmapPaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val scrimPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = 0x99000000.toInt()
    }
    private val gridPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = density
        color = 0x66FFFFFF
    }
    private val borderPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 2f * density
        color = Color.WHITE
    }
    private val handlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 3f * density
        strokeCap = Paint.Cap.ROUND
        color = ContextCompat.getColor(context, R.color.primary)
    }

    // Reused scrim bands so onDraw stays allocation-free.
    private val scrimTop = RectF()
    private val scrimBottom = RectF()
    private val scrimLeft = RectF()
    private val scrimRight = RectF()

    // ── Public API ──────────────────────────────────────────────────

    fun setBitmap(bmp: Bitmap) {
        sourceBitmap = bmp
        rebuildWorking()
        if (width > 0 && height > 0) {
            recomputeFit()
            applyPendingOrInit()
        }
        invalidate()
    }

    /** Rotate the photo 90° clockwise and reset the crop to the full image. */
    fun rotate90() {
        if (sourceBitmap == null) return
        rotationDegrees = (rotationDegrees + 90) % 360
        rebuildWorking()
        pendingRestore = null
        cropInitialized = false
        if (width > 0 && height > 0) {
            recomputeFit()
            applyPendingOrInit()
        }
        invalidate()
    }

    /** Crop [workingBitmap] to the current window, at source resolution. */
    fun getCroppedBitmap(): Bitmap? {
        val bmp = workingBitmap ?: return null
        if (imageRect.isEmpty || fitScale <= 0f) return null
        val bw = bmp.width
        val bh = bmp.height
        val pxL = ((cropRect.left - imageRect.left) / fitScale).roundToInt().coerceIn(0, bw)
        val pxT = ((cropRect.top - imageRect.top) / fitScale).roundToInt().coerceIn(0, bh)
        val pxR = ((cropRect.right - imageRect.left) / fitScale).roundToInt().coerceIn(0, bw)
        val pxB = ((cropRect.bottom - imageRect.top) / fitScale).roundToInt().coerceIn(0, bh)
        val w = (pxR - pxL).coerceAtLeast(1)
        val h = (pxB - pxT).coerceAtLeast(1)
        val out = Bitmap.createBitmap(bmp, pxL, pxT, w, h)
        // createBitmap can hand back the SAME instance when the rect covers the
        // whole bitmap — copy so the caller can recycle the result freely
        // without harming workingBitmap.
        return if (out === bmp) out.copy(bmp.config ?: Bitmap.Config.ARGB_8888, false) else out
    }

    fun rotationValue(): Int = rotationDegrees

    /** Crop rect as fractions (left, top, right, bottom) of the image. */
    fun cropRectNormalized(): FloatArray {
        if (imageRect.isEmpty) return floatArrayOf(0f, 0f, 1f, 1f)
        return floatArrayOf(
            ((cropRect.left - imageRect.left) / imageRect.width()).coerceIn(0f, 1f),
            ((cropRect.top - imageRect.top) / imageRect.height()).coerceIn(0f, 1f),
            ((cropRect.right - imageRect.left) / imageRect.width()).coerceIn(0f, 1f),
            ((cropRect.bottom - imageRect.top) / imageRect.height()).coerceIn(0f, 1f),
        )
    }

    /** Restore rotation + normalized crop (call before [setBitmap] on recreate). */
    fun restoreState(rotation: Int, normalized: FloatArray) {
        rotationDegrees = ((rotation % 360) + 360) % 360
        if (normalized.size == 4) {
            pendingRestore = RectF(normalized[0], normalized[1], normalized[2], normalized[3])
        }
    }

    fun recycleBitmaps() {
        workingBitmap?.let { if (it !== sourceBitmap) it.recycle() }
        sourceBitmap?.recycle()
        workingBitmap = null
        sourceBitmap = null
    }

    // ── Internals ───────────────────────────────────────────────────

    private fun rebuildWorking() {
        val src = sourceBitmap ?: return
        val old = workingBitmap
        workingBitmap = if (rotationDegrees == 0) {
            src
        } else {
            val m = Matrix().apply { postRotate(rotationDegrees.toFloat()) }
            Bitmap.createBitmap(src, 0, 0, src.width, src.height, m, true)
        }
        if (old != null && old !== src && old !== workingBitmap) old.recycle()
    }

    private fun recomputeFit() {
        val bmp = workingBitmap ?: return
        val vw = (width - paddingLeft - paddingRight).toFloat()
        val vh = (height - paddingTop - paddingBottom).toFloat()
        if (vw <= 0f || vh <= 0f) return
        val bw = bmp.width.toFloat()
        val bh = bmp.height.toFloat()
        fitScale = min(vw / bw, vh / bh)
        val dispW = bw * fitScale
        val dispH = bh * fitScale
        val offX = paddingLeft + (vw - dispW) / 2f
        val offY = paddingTop + (vh - dispH) / 2f
        imageRect.set(offX, offY, offX + dispW, offY + dispH)
    }

    private fun applyPendingOrInit() {
        if (imageRect.isEmpty) return
        val restore = pendingRestore
        when {
            restore != null -> {
                cropRect.set(
                    imageRect.left + restore.left * imageRect.width(),
                    imageRect.top + restore.top * imageRect.height(),
                    imageRect.left + restore.right * imageRect.width(),
                    imageRect.top + restore.bottom * imageRect.height(),
                )
                pendingRestore = null
                cropInitialized = true
                clampCropWithinImage()
            }
            !cropInitialized -> {
                cropRect.set(imageRect)
                cropInitialized = true
            }
            else -> clampCropWithinImage()
        }
    }

    private fun clampCropWithinImage() {
        val minW = min(minCropPx, imageRect.width())
        val minH = min(minCropPx, imageRect.height())
        cropRect.left = cropRect.left.coerceIn(imageRect.left, imageRect.right - minW)
        cropRect.top = cropRect.top.coerceIn(imageRect.top, imageRect.bottom - minH)
        cropRect.right = cropRect.right.coerceIn(cropRect.left + minW, imageRect.right)
        cropRect.bottom = cropRect.bottom.coerceIn(cropRect.top + minH, imageRect.bottom)
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        if (workingBitmap != null) {
            recomputeFit()
            applyPendingOrInit()
            invalidate()
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val bmp = workingBitmap ?: return
        if (imageRect.isEmpty) return

        canvas.drawBitmap(bmp, null, imageRect, bitmapPaint)

        // Dim only the region outside the crop window.
        scrimTop.set(imageRect.left, imageRect.top, imageRect.right, cropRect.top)
        scrimBottom.set(imageRect.left, cropRect.bottom, imageRect.right, imageRect.bottom)
        scrimLeft.set(imageRect.left, cropRect.top, cropRect.left, cropRect.bottom)
        scrimRight.set(cropRect.right, cropRect.top, imageRect.right, cropRect.bottom)
        canvas.drawRect(scrimTop, scrimPaint)
        canvas.drawRect(scrimBottom, scrimPaint)
        canvas.drawRect(scrimLeft, scrimPaint)
        canvas.drawRect(scrimRight, scrimPaint)

        // Rule-of-thirds guides inside the crop.
        val w3 = cropRect.width() / 3f
        val h3 = cropRect.height() / 3f
        canvas.drawLine(cropRect.left + w3, cropRect.top, cropRect.left + w3, cropRect.bottom, gridPaint)
        canvas.drawLine(cropRect.left + 2f * w3, cropRect.top, cropRect.left + 2f * w3, cropRect.bottom, gridPaint)
        canvas.drawLine(cropRect.left, cropRect.top + h3, cropRect.right, cropRect.top + h3, gridPaint)
        canvas.drawLine(cropRect.left, cropRect.top + 2f * h3, cropRect.right, cropRect.top + 2f * h3, gridPaint)

        canvas.drawRect(cropRect, borderPaint)
        drawHandles(canvas)
    }

    private fun drawHandles(canvas: Canvas) {
        val l = cropRect.left
        val t = cropRect.top
        val r = cropRect.right
        val b = cropRect.bottom
        val len = min(handleLen, min(cropRect.width(), cropRect.height()) / 3f)
        // Corner brackets.
        canvas.drawLine(l, t, l + len, t, handlePaint)
        canvas.drawLine(l, t, l, t + len, handlePaint)
        canvas.drawLine(r, t, r - len, t, handlePaint)
        canvas.drawLine(r, t, r, t + len, handlePaint)
        canvas.drawLine(l, b, l + len, b, handlePaint)
        canvas.drawLine(l, b, l, b - len, handlePaint)
        canvas.drawLine(r, b, r - len, b, handlePaint)
        canvas.drawLine(r, b, r, b - len, handlePaint)
        // Edge ticks.
        val cx = cropRect.centerX()
        val cy = cropRect.centerY()
        canvas.drawLine(cx - len / 2f, t, cx + len / 2f, t, handlePaint)
        canvas.drawLine(cx - len / 2f, b, cx + len / 2f, b, handlePaint)
        canvas.drawLine(l, cy - len / 2f, l, cy + len / 2f, handlePaint)
        canvas.drawLine(r, cy - len / 2f, r, cy + len / 2f, handlePaint)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                activeHandle = hitTest(event.x, event.y)
                lastX = event.x
                lastY = event.y
                if (activeHandle == Handle.NONE) return false
                parent?.requestDisallowInterceptTouchEvent(true)
                return true
            }
            MotionEvent.ACTION_MOVE -> {
                if (activeHandle == Handle.NONE) return false
                applyDrag(activeHandle, event.x - lastX, event.y - lastY)
                lastX = event.x
                lastY = event.y
                invalidate()
                return true
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                val handled = activeHandle != Handle.NONE
                activeHandle = Handle.NONE
                parent?.requestDisallowInterceptTouchEvent(false)
                if (handled) performClick()
                return handled
            }
        }
        return super.onTouchEvent(event)
    }

    override fun performClick(): Boolean {
        super.performClick()
        return true
    }

    private fun hitTest(x: Float, y: Float): Handle {
        val r = handleTouchRadius
        fun near(px: Float, py: Float) = hypot((x - px).toDouble(), (y - py).toDouble()) <= r
        if (near(cropRect.left, cropRect.top)) return Handle.TL
        if (near(cropRect.right, cropRect.top)) return Handle.TR
        if (near(cropRect.left, cropRect.bottom)) return Handle.BL
        if (near(cropRect.right, cropRect.bottom)) return Handle.BR
        val cx = cropRect.centerX()
        val cy = cropRect.centerY()
        if (near(cx, cropRect.top)) return Handle.T
        if (near(cx, cropRect.bottom)) return Handle.B
        if (near(cropRect.left, cy)) return Handle.L
        if (near(cropRect.right, cy)) return Handle.R
        if (cropRect.contains(x, y)) return Handle.INTERIOR
        return Handle.NONE
    }

    private fun applyDrag(handle: Handle, dx: Float, dy: Float) {
        val minW = min(minCropPx, imageRect.width())
        val minH = min(minCropPx, imageRect.height())
        when (handle) {
            Handle.INTERIOR -> {
                var ndx = dx
                var ndy = dy
                if (cropRect.left + ndx < imageRect.left) ndx = imageRect.left - cropRect.left
                if (cropRect.right + ndx > imageRect.right) ndx = imageRect.right - cropRect.right
                if (cropRect.top + ndy < imageRect.top) ndy = imageRect.top - cropRect.top
                if (cropRect.bottom + ndy > imageRect.bottom) ndy = imageRect.bottom - cropRect.bottom
                cropRect.offset(ndx, ndy)
            }
            Handle.L -> cropRect.left = (cropRect.left + dx).coerceIn(imageRect.left, cropRect.right - minW)
            Handle.R -> cropRect.right = (cropRect.right + dx).coerceIn(cropRect.left + minW, imageRect.right)
            Handle.T -> cropRect.top = (cropRect.top + dy).coerceIn(imageRect.top, cropRect.bottom - minH)
            Handle.B -> cropRect.bottom = (cropRect.bottom + dy).coerceIn(cropRect.top + minH, imageRect.bottom)
            Handle.TL -> {
                cropRect.left = (cropRect.left + dx).coerceIn(imageRect.left, cropRect.right - minW)
                cropRect.top = (cropRect.top + dy).coerceIn(imageRect.top, cropRect.bottom - minH)
            }
            Handle.TR -> {
                cropRect.right = (cropRect.right + dx).coerceIn(cropRect.left + minW, imageRect.right)
                cropRect.top = (cropRect.top + dy).coerceIn(imageRect.top, cropRect.bottom - minH)
            }
            Handle.BL -> {
                cropRect.left = (cropRect.left + dx).coerceIn(imageRect.left, cropRect.right - minW)
                cropRect.bottom = (cropRect.bottom + dy).coerceIn(cropRect.top + minH, imageRect.bottom)
            }
            Handle.BR -> {
                cropRect.right = (cropRect.right + dx).coerceIn(cropRect.left + minW, imageRect.right)
                cropRect.bottom = (cropRect.bottom + dy).coerceIn(cropRect.top + minH, imageRect.bottom)
            }
            Handle.NONE -> Unit
        }
    }
}
