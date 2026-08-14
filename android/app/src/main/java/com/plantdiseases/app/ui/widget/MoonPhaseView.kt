package com.plantdiseases.app.ui.widget

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.BlurMaskFilter
import android.graphics.Canvas
import android.graphics.ColorMatrix
import android.graphics.ColorMatrixColorFilter
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
 *
 * When a `moon_texture` drawable is present in the APK (a real lunar photo,
 * transparent outside the disc — provisioned out of band, like every other
 * binary asset in this repo, see [ensureTexture]), the lit face and a dimmed
 * "earthshine" copy across the whole disc are drawn from that photo instead
 * of the flat gradient + [maria] circles, which stays the fallback for
 * builds that don't have the asset.
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
    private val discClip = Path()
    private val terminatorPath = Path()

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

    // ── Photo texture (optional — see ensureTexture) ────────────────
    private var texture: Bitmap? = null
    private var textureLoadAttempted = false
    private val texturePaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG).apply {
        colorFilter = WARM_FILTER
    }
    private val earthshinePaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG).apply {
        colorFilter = WARM_FILTER
        alpha = 46 // ambient light reflected off the Earth, not full sunlight
    }
    private val terminatorGlowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        color = context.getColor(R.color.moon_terminator_glow)
    }

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
        terminatorGlowPaint.strokeWidth = r * 0.10f
        terminatorGlowPaint.maskFilter = BlurMaskFilter(r * 0.12f, BlurMaskFilter.Blur.NORMAL)
        ensureTexture()
    }

    /** Looks up an optional `moon_texture` drawable by NAME rather than a
     *  generated `R.drawable.moon_texture` reference, specifically so this
     *  class compiles and runs correctly whether or not that binary asset
     *  has actually been provisioned (it's supplied out of band, like every
     *  other binary asset in this repo — see CLAUDE.md). Resolves to 0, and
     *  this silently no-ops, until the file exists in res/drawable-nodpi/;
     *  the gradient + [maria] rendering stays fully functional either way. */
    private fun ensureTexture() {
        if (textureLoadAttempted) return
        textureLoadAttempted = true
        val resId = resources.getIdentifier("moon_texture", "drawable", context.packageName)
        if (resId == 0) return
        texture = runCatching { BitmapFactory.decodeResource(resources, resId) }.getOrNull()
    }

    override fun onDraw(canvas: Canvas) {
        val r = (min(width, height) / 2f) - strokeInset
        if (r <= 0f) return
        val cx = width / 2f
        val cy = height / 2f
        disc.set(cx - r, cy - r, cx + r, cy + r)

        canvas.drawCircle(cx, cy, 1.22f * r, glowPaint)

        val tex = texture
        val save = canvas.save()
        if (cycle >= 0.5f) canvas.scale(-1f, 1f, cx, cy) // mirror for waning
        // Mirroring, the dark-disc/earthshine layer, the lit crescent and the
        // terminator glow all share this ONE transform (mirror + libration
        // applied identically to both). With a real photo texture, drawing
        // the dark and lit halves under different transforms would show a
        // seam right at the terminator where the surface orientation jumps.

        if (tex != null) {
            discClip.reset()
            discClip.addCircle(cx, cy, r, Path.Direction.CW)
            canvas.save()
            canvas.clipPath(discClip)
            canvas.rotate(libration, cx, cy)
            canvas.drawBitmap(tex, null, disc, earthshinePaint)
            canvas.restore()
        } else {
            canvas.drawCircle(cx, cy, r, darkPaint)
        }

        val f = cos(2.0 * PI * cycle).toFloat()
        val a = max(abs(f) * r, 0.01f)
        term.set(cx - a, cy - r, cx + a, cy + r)

        litPath.reset()
        litPath.arcTo(disc, -90f, 180f) // right limb: 12 → 3 → 6 o'clock
        litPath.arcTo(term, 90f, if (f >= 0f) -180f else 180f) // terminator back: 6 → 12 o'clock
        litPath.close()

        if (tex != null) {
            canvas.save()
            canvas.clipPath(litPath)
            // Libration rotates the surface, never the terminator itself —
            // a wobbling terminator would read as broken, not alive.
            canvas.rotate(libration, cx, cy)
            canvas.drawBitmap(tex, null, disc, texturePaint)
            canvas.restore()

            // Soft warm glow along just the terminator edge — a real shadow
            // line isn't razor sharp. Only this one arc, not the outer limb,
            // which stays crisp (drawn separately by edgePaint below).
            terminatorPath.reset()
            terminatorPath.arcTo(term, 90f, if (f >= 0f) -180f else 180f)
            canvas.save()
            canvas.clipPath(discClip)
            canvas.drawPath(terminatorPath, terminatorGlowPaint)
            canvas.restore()
        } else {
            canvas.drawPath(litPath, litPaint)
            canvas.save()
            canvas.clipPath(litPath)
            canvas.rotate(libration, cx, cy)
            drawMaria(canvas, cx, cy, r)
            canvas.restore()
        }

        canvas.drawPath(litPath, edgePaint)
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
        texture?.recycle()
        texture = null
        textureLoadAttempted = false
        super.onDetachedFromWindow()
    }

    private companion object {
        const val PHASE_DURATION_MS = 520L
        const val IDLE_DURATION_MS = 9_000L

        /** Nudges a neutral photo toward the warm gold the reference art
         *  uses, rather than the grey a raw lunar plate actually is. */
        val WARM_FILTER = ColorMatrixColorFilter(
            ColorMatrix(
                floatArrayOf(
                    1.08f, 0f, 0f, 0f, 6f,
                    0f, 1.01f, 0f, 0f, 2f,
                    0f, 0f, 0.90f, 0f, -4f,
                    0f, 0f, 0f, 1f, 0f,
                ),
            ),
        )
    }
}
