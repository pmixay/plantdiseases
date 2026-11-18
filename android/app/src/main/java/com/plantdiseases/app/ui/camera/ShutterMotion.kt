package com.plantdiseases.app.ui.camera

import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.graphics.drawable.AnimatedVectorDrawable
import android.view.View
import android.view.animation.LinearInterpolator
import android.widget.ImageView

/**
 * The capture button's motion: an "iris" click on tap (the ring's hole closes
 * and reopens with a quarter turn while the button dips) and a busy state
 * (button disabled, ring dimmed, an arc spinning around it) for as long as a
 * capture is in flight. With system animations off, the click is skipped and
 * the busy arc stays still.
 *
 * [CameraFragment] owns one per view and routes every place that locks or
 * re-arms the shutter through [setBusy].
 */
internal class ShutterMotion(
    private val button: ImageView,
    private val arc: View,
    private val animationsEnabled: () -> Boolean = { ValueAnimator.areAnimatorsEnabled() },
) {

    private var spin: ObjectAnimator? = null

    var isBusy: Boolean = false
        private set

    fun click() {
        if (!animationsEnabled()) return
        (button.drawable as? AnimatedVectorDrawable)?.let {
            it.reset()
            it.start()
        }
        button.animate().cancel()
        button.animate()
            .scaleX(DIP_SCALE).scaleY(DIP_SCALE)
            .setDuration(DIP_DOWN_MS)
            .withEndAction {
                button.animate().scaleX(1f).scaleY(1f).setDuration(DIP_UP_MS).start()
            }
            .start()
    }

    fun setBusy(busy: Boolean) {
        isBusy = busy
        button.isEnabled = !busy
        arc.animate().cancel()
        spin?.cancel()
        spin = null
        if (busy) {
            button.imageAlpha = DIM_ALPHA
            arc.alpha = 1f
            arc.rotation = 0f
            arc.visibility = View.VISIBLE
            if (animationsEnabled()) {
                spin = ObjectAnimator.ofFloat(arc, View.ROTATION, 0f, FULL_TURN).apply {
                    duration = TURN_MS
                    repeatCount = ValueAnimator.INFINITE
                    interpolator = LinearInterpolator()
                    start()
                }
            }
            return
        }
        button.imageAlpha = FULL_ALPHA
        if (arc.visibility != View.VISIBLE) return
        if (!animationsEnabled()) {
            arc.visibility = View.GONE
            return
        }
        arc.animate()
            .alpha(0f)
            .setDuration(FADE_MS)
            .withEndAction { if (!isBusy) arc.visibility = View.GONE }
            .start()
    }

    /** Stops every animator; call before the views go away. */
    fun release() {
        spin?.cancel()
        spin = null
        button.animate().cancel()
        arc.animate().cancel()
    }

    internal val isSpinning: Boolean get() = spin?.isRunning == true

    private companion object {
        const val DIP_SCALE = 0.93f
        const val DIP_DOWN_MS = 150L
        const val DIP_UP_MS = 230L
        const val TURN_MS = 900L
        const val FADE_MS = 150L
        const val FULL_TURN = 360f
        const val FULL_ALPHA = 255
        const val DIM_ALPHA = 89 // about 35 %
    }
}
