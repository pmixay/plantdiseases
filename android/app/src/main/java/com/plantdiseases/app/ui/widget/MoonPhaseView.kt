package com.plantdiseases.app.ui.widget

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View
import android.view.animation.LinearInterpolator
import android.view.animation.PathInterpolator
import com.plantdiseases.app.R
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min

/**
 * Draws the Moon at a given point in its cycle: a lit sphere with fixed maria and
 * craters, limb darkening toward the edge, animated between phases and idling with
 * a subtle libration wobble while visible.
 *
 * The terminator (the lit/dark boundary) is a great circle seen in projection, so it
 * renders as a semi-ellipse sharing the disc's vertical radius. It is always built in
 * "lit-on-right" geometry and the resulting *path* is mirrored for the waning half.
 */
class MoonPhaseView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0,
) : View(context, attrs, defStyle) {

    /** 0.0 = new, 0.25 = first quarter, 0.5 = full, 0.75 = last quarter. Always in [0,1). */
    var cycle: Float = 0.5f
        private set

    private val density = resources.displayMetrics.density

    private val disc = RectF()
    private val term = RectF()
    private val blob = RectF()
    private val litPath = Path()
    private val mirror = Matrix()

    private val darkPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.moon_dark)
    }
    private val marePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.moon_mare)
        alpha = 38
    }
    private val craterFloorPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.moon_mare)
        alpha = 54
    }
    private val craterShadowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        color = Color.BLACK
        alpha = 46
    }
    private val craterRimPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        color = context.getColor(R.color.moon_lit_hi)
        alpha = 120
    }
    private val edgePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = density
        color = context.getColor(R.color.moon_lit_lo)
        alpha = 90
    }
    private var litPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var limbPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var glowPaint = Paint(Paint.ANTI_ALIAS_FLAG)

    /** (x, y, radius), all normalized to the disc radius. Fixed and deterministic. */
    private val maria = arrayOf(
        floatArrayOf(-0.30f, -0.28f, 0.26f),
        floatArrayOf(0.10f, -0.42f, 0.16f),
        floatArrayOf(0.26f, -0.10f, 0.22f),
        floatArrayOf(-0.12f, 0.14f, 0.30f),
        floatArrayOf(0.34f, 0.30f, 0.18f),
        floatArrayOf(-0.40f, 0.34f, 0.14f),
        floatArrayOf(0.02f, 0.48f, 0.12f),
    )

    /** Craters, drawn over the maria with a lit far wall and a shadowed near wall. */
    private val craters = arrayOf(
        floatArrayOf(-0.10f, -0.62f, 0.115f),
        floatArrayOf(0.46f, -0.44f, 0.085f),
        floatArrayOf(-0.56f, -0.08f, 0.075f),
        floatArrayOf(0.58f, 0.16f, 0.065f),
        floatArrayOf(-0.30f, 0.56f, 0.095f),
        floatArrayOf(0.14f, 0.66f, 0.070f),
        floatArrayOf(0.36f, 0.52f, 0.055f),
        floatArrayOf(-0.62f, 0.40f, 0.050f),
        floatArrayOf(0.04f, -0.16f, 0.045f),
        floatArrayOf(-0.44f, -0.46f, 0.040f),
    )

    private var phaseAnimator: ValueAnimator? = null
    private var librationAnimator: ValueAnimator? = null
    private var libration: Float = 0f

    /**
     * Disc radius. Divided by GLOW_SCALE so the halo drawn at GLOW_SCALE * r still fits
     * inside the view bounds. The previous radius put the halo outside the clip in a
     * 64dp box, which cut the glow off flat on all four sides.
     */
    private fun radius(w: Int, h: Int): Float = (min(w, h) / 2f) / GLOW_SCALE

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        val r = radius(w, h)
        if (r <= 0f) return
        val cx = w / 2f
        val cy = h / 2f
        litPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            shader = RadialGradient(
                cx - 0.28f * r, cy - 0.28f * r, 1.35f * r,
                context.getColor(R.color.moon_lit_hi), context.getColor(R.color.moon_lit_lo),
                Shader.TileMode.CLAMP,
            )
        }
        // Limb darkening: transparent across the middle, dark at the edge. This is the
        // single cheapest cue that turns a flat disc into a sphere.
        limbPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            shader = RadialGradient(
                cx, cy, r,
                intArrayOf(0x00000000, 0x00000000, 0x33000000, 0x66000000),
                floatArrayOf(0f, 0.55f, 0.87f, 1f),
                Shader.TileMode.CLAMP,
            )
        }
        glowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            shader = RadialGradient(
                cx, cy, GLOW_SCALE * r,
                context.getColor(R.color.moon_glow), 0x00000000,
                Shader.TileMode.CLAMP,
            )
        }
    }

    override fun onDraw(canvas: Canvas) {
        val r = radius(width, height)
        if (r <= 0f) return
        val cx = width / 2f
        val cy = height / 2f
        disc.set(cx - r, cy - r, cx + r, cy + r)

        canvas.drawCircle(cx, cy, GLOW_SCALE * r, glowPaint)
        canvas.drawCircle(cx, cy, r, darkPaint)

        buildLitPath(cx, cy, r)

        val save = canvas.save()
        canvas.clipPath(litPath)
        canvas.drawCircle(cx, cy, r, litPaint)
        // Surface detail rotates with the libration wobble; the terminator never does.
        // A wobbling terminator reads as broken, not alive.
        canvas.save()
        canvas.rotate(libration, cx, cy)
        drawSurface(canvas, cx, cy, r)
        canvas.restore()
        canvas.drawCircle(cx, cy, r, limbPaint)
        canvas.restoreToCount(save)

        canvas.drawPath(litPath, edgePaint)
    }

    /**
     * Builds the lit region in "lit-on-right" geometry and mirrors the finished *path*
     * for the waning half. Mirroring the canvas instead (the old approach) also mirrors
     * the surface markings, so the whole moon flips horizontally the moment the cycle
     * crosses 0.5.
     */
    private fun buildLitPath(cx: Float, cy: Float, r: Float) {
        val f = cos(2.0 * PI * cycle).toFloat()
        val a = max(abs(f) * r, 0.01f)
        term.set(cx - a, cy - r, cx + a, cy + r)

        litPath.reset()
        litPath.arcTo(disc, -90f, 180f)                          // right limb: 12 -> 3 -> 6
        litPath.arcTo(term, 90f, if (f >= 0f) -180f else 180f)   // terminator back: 6 -> 12
        litPath.close()

        if (cycle >= 0.5f) {
            mirror.setScale(-1f, 1f, cx, cy)
            litPath.transform(mirror)
        }
    }

    private fun drawSurface(canvas: Canvas, cx: Float, cy: Float, r: Float) {
        maria.forEach { (nx, ny, nr) ->
            canvas.drawCircle(cx + nx * r, cy + ny * r, nr * r, marePaint)
        }
        // Sunlight always arrives from +x in this unmirrored geometry, so the inside of a
        // depression is lit on its far (left) wall and shadowed on its near (right) wall.
        // Drawing both arcs is what makes a grey circle read as a pit instead of a dot.
        craters.forEach { (nx, ny, nr) ->
            val ccx = cx + nx * r
            val ccy = cy + ny * r
            val cr = nr * r
            canvas.drawCircle(ccx, ccy, cr, craterFloorPaint)
            val stroke = max(cr * 0.34f, 0.8f * density)
            blob.set(ccx - cr, ccy - cr, ccx + cr, ccy + cr)
            blob.inset(stroke / 2f, stroke / 2f)
            craterRimPaint.strokeWidth = stroke
            craterShadowPaint.strokeWidth = stroke
            canvas.drawArc(blob, 90f, 180f, false, craterRimPaint)     // far wall, lit
            canvas.drawArc(blob, -90f, 180f, false, craterShadowPaint) // near wall, shadowed
        }
    }

    /** Sets the phase, optionally animating the shortest arc across the 0/1 seam. */
    fun setCycle(value: Float, animate: Boolean = false) {
        val target = normalize(value)
        phaseAnimator?.cancel()
        phaseAnimator = null
        if (!animate || !isAttachedToWindow || width == 0) {
            cycle = target
            invalidate()
            return
        }
        val end = when {
            target - cycle > 0.5f -> target - 1f
            cycle - target > 0.5f -> target + 1f
            else -> target
        }
        phaseAnimator = ValueAnimator.ofFloat(cycle, end).apply {
            duration = PHASE_DURATION_MS
            interpolator = PathInterpolator(0.2f, 0f, 0f, 1f)
            addUpdateListener {
                cycle = normalize(it.animatedValue as Float)
                invalidate()
            }
            start()
        }
    }

    private fun normalize(value: Float): Float = ((value % 1f) + 1f) % 1f

    private fun startIdle() {
        if (librationAnimator != null) return
        librationAnimator = ValueAnimator.ofFloat(-1.6f, 1.6f).apply {
            duration = IDLE_DURATION_MS
            repeatCount = ValueAnimator.INFINITE
            repeatMode = ValueAnimator.REVERSE
            interpolator = LinearInterpolator()
            addUpdateListener {
                libration = it.animatedValue as Float
                invalidate()
            }
            start()
        }
    }

    private fun stopIdle() {
        librationAnimator?.cancel()
        librationAnimator = null
    }

    override fun onVisibilityAggregated(isVisible: Boolean) {
        super.onVisibilityAggregated(isVisible)
        if (isVisible) {
            startIdle()
        } else {
            stopIdle()
            phaseAnimator?.cancel()
            phaseAnimator = null
        }
    }

    override fun onDetachedFromWindow() {
        stopIdle()
        phaseAnimator?.cancel()
        phaseAnimator = null
        super.onDetachedFromWindow()
    }

    private companion object {
        const val PHASE_DURATION_MS = 520L
        const val IDLE_DURATION_MS = 9_000L
        const val GLOW_SCALE = 1.22f
    }
}
