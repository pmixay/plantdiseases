package com.plantdiseases.app.ui.camera

import android.animation.ValueAnimator
import android.graphics.drawable.AnimatedVectorDrawable
import android.widget.ImageView

/**
 * The capture button's motion: an "iris" click on tap (the ring's hole closes
 * and reopens with a quarter turn while the button dips) and a lock state
 * (button disabled) for as long as a capture is in flight. There is
 * deliberately no busy indicator: the cropper opens as soon as the file is
 * written. With system animations off, the click is skipped.
 *
 * [CameraFragment] owns one per view and routes every place that locks or
 * re-arms the shutter through [setBusy].
 */
internal class ShutterMotion(
    private val button: ImageView,
    private val animationsEnabled: () -> Boolean = { ValueAnimator.areAnimatorsEnabled() },
) {

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
    }

    /** Stops every animator; call before the views go away. */
    fun release() {
        button.animate().cancel()
    }

    private companion object {
        const val DIP_SCALE = 0.93f
        const val DIP_DOWN_MS = 150L
        const val DIP_UP_MS = 230L
    }
}
