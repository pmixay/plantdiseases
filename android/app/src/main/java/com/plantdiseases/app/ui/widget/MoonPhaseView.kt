package com.plantdiseases.app.ui.widget

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
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
 * Draws the Moon at a given point in its cycle: an off-centre-lit sphere with
 * a handful of fixed maria, animated between phases and idling with a subtle
 * libration wobble while visible.
 *
 * The terminator (the lit/dark boundary) is a great circle seen in
 * projection, which renders as a semi-ellipse sharing the disc's vertical
 * radius. Always drawn in "lit-on-right" geometry and mirrored for the
 * waning half — see [onDraw].
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
    private val strokeInset = 1.5f * density

    private val disc = RectF()
    private val term = RectF()
    private val litPath = Path()

    private val darkPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.moon_dark)
    }
    private val mariaPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.moon_mare)
        alpha = 26
    }
    private val edgePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = density
        color = context.getColor(R.color.moon_lit_lo)
        alpha = 90
    }
    private var litPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var glowPaint = Paint(Paint.ANTI_ALIAS_FLAG)

    /** (normalized x, normalized y, normalized radius) — fixed, deterministic. */
    private val maria = arrayOf(
        floatArrayOf(-0.30f, -0.28f, 0.26f),
        floatArrayOf(0.10f, -0.42f, 0.16f),
        floatArrayOf(0.26f, -0.10f, 0.22f),
        floatArrayOf(-0.12f, 0.14f, 0.30f),
        floatArrayOf(0.34f, 0.30f, 0.18f),
        floatArrayOf(-0.40f, 0.34f, 0.14f),
        floatArrayOf(0.02f, 0.48f, 0.12f),
    )

    private var phaseAnimator: ValueAnimator? = null
    private var librationAnimator: ValueAnimator? = null
    private var libration: Float = 0f

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        val r = (min(w, h) / 2f) - strokeInset
        if (r <= 0f) return
        val cx = w / 2f
        val cy = h / 2f
        litPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            shader = RadialGradient(
                cx - 0.28f * r,
                cy - 0.28f * r,
                1.35f * r,
                context.getColor(R.color.moon_lit_hi),
                context.getColor(R.color.moon_lit_lo),
                Shader.TileMode.CLAMP,
            )
        }
        glowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            shader = RadialGradient(
                cx,
                cy,
                1.22f * r,
                context.getColor(R.color.moon_glow),
                0x00000000,
                Shader.TileMode.CLAMP,
            )
        }
    }

    override fun onDraw(canvas: Canvas) {
        val r = (min(width, height) / 2f) - strokeInset
        if (r <= 0f) return
        val cx = width / 2f
        val cy = height / 2f
        disc.set(cx - r, cy - r, cx + r, cy + r)

        canvas.drawCircle(cx, cy, 1.22f * r, glowPaint)
        canvas.drawCircle(cx, cy, r, darkPaint)

        val save = canvas.save()
        if (cycle >= 0.5f) canvas.scale(-1f, 1f, cx, cy) // mirror for waning

        val f = cos(2.0 * PI * cycle).toFloat()
        val a = max(abs(f) * r, 0.01f)
        term.set(cx - a, cy - r, cx + a, cy + r)

        litPath.reset()
        litPath.arcTo(disc, -90f, 180f) // right limb: 12 → 3 → 6 o'clock
        litPath.arcTo(term, 90f, if (f >= 0f) -180f else 180f) // terminator back: 6 → 12 o'clock
        litPath.close()

        canvas.drawPath(litPath, litPaint)
        canvas.drawPath(litPath, edgePaint)
        canvas.save()
        canvas.clipPath(litPath)
        // Libration rotates only the maria layer, never the terminator itself —
        // a wobbling terminator would read as broken, not alive.
        canvas.rotate(libration, cx, cy)
        drawMaria(canvas, cx, cy, r)
        canvas.restore()
        canvas.restoreToCount(save)
    }

    private fun drawMaria(canvas: Canvas, cx: Float, cy: Float, r: Float) {
        maria.forEach { (nx, ny, nr) ->
            canvas.drawCircle(cx + nx * r, cy + ny * r, nr * r, mariaPaint)
        }
    }

    /** Sets the phase, optionally animating the shortest arc across the 0/1 seam. */
    fun setCycle(value: Float, animate: Boolean = false) {
        val target = ((value % 1f) + 1f) % 1f
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
        librationAnimator = ValueAnimator.ofFloat(-1f, 1f).apply {
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
    }
}
