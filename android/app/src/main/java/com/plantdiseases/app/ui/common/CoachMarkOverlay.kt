package com.plantdiseases.app.ui.common

import android.animation.AnimatorSet
import android.animation.ObjectAnimator
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.ViewTreeObserver
import android.widget.FrameLayout
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.doOnLayout
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import com.plantdiseases.app.databinding.ViewCoachMarkBinding
import kotlin.math.roundToInt

/**
 * A small, dependency-free coach mark anchored to an existing view.
 *
 * It is attached to the activity content dynamically, which lets feature
 * screens use it without reserving permanent space or coupling their layouts
 * to onboarding. The host lifecycle owns it: the pulse is paused while the
 * host is stopped and the overlay is torn down when the host is destroyed.
 * Back dismisses it too, and the anchor's original scale is restored after the
 * optional pulse.
 */
class CoachMarkOverlay private constructor(
    private val host: AppCompatActivity,
    private val anchor: View,
    title: CharSequence,
    body: CharSequence,
    dismissText: CharSequence,
    private val placement: Placement,
    pulseAnchor: Boolean,
    private val onDismissed: () -> Unit,
) {

    enum class Placement {
        ABOVE,
        BELOW,
    }

    private val parent = host.findViewById<ViewGroup>(android.R.id.content)
    private val binding = ViewCoachMarkBinding.inflate(LayoutInflater.from(host), parent, false)
    private val originalScaleX = anchor.scaleX
    private val originalScaleY = anchor.scaleY
    private var pulseAnimator: AnimatorSet? = null
    private var dismissed = false
    private var dismissAnimationStarted = false
    private var removalComplete = false

    private val backCallback = object : androidx.activity.OnBackPressedCallback(true) {
        override fun handleOnBackPressed() = dismiss()
    }

    /**
     * The anchor keeps moving after the overlay is laid out — MainActivity's
     * splash exit slides the bottom bar back up, and a translation never
     * triggers a layout pass — so the bubble is re-placed before every draw
     * instead of once.
     */
    private val repositionListener = ViewTreeObserver.OnPreDrawListener {
        positionBubble()
        true
    }

    /** Keeps the pulse off the vsync queue while the host is stopped (Home
     *  key), and takes the overlay down with the host. */
    private val lifecycleObserver = object : DefaultLifecycleObserver {
        override fun onStart(owner: LifecycleOwner) {
            pulseAnimator?.resume()
        }

        override fun onStop(owner: LifecycleOwner) {
            pulseAnimator?.pause()
        }

        override fun onDestroy(owner: LifecycleOwner) = dismiss(immediate = true)
    }

    init {
        binding.coachTitle.text = title
        binding.coachBody.text = body
        binding.coachDismiss.text = dismissText
        ViewCompat.setAccessibilityHeading(binding.coachTitle, true)
        ViewCompat.setAccessibilityPaneTitle(binding.root, title)

        binding.root.setOnClickListener { dismiss() }
        // Do not let a tap inside the bubble fall through to the full-screen
        // dismiss listener; only the explicit button dismisses from within it.
        binding.coachBubble.setOnClickListener { }
        binding.coachDismiss.setOnClickListener { dismiss() }

        val parentWidth = parent.width.takeIf { it > 0 }
            ?: host.resources.displayMetrics.widthPixels
        binding.coachBubble.layoutParams = binding.coachBubble.layoutParams.apply {
            width = minOf(
                COACH_WIDTH_DP.dp,
                (parentWidth - HORIZONTAL_MARGIN_DP.dp * 2).coerceAtLeast(0),
            )
        }
        parent.addView(
            binding.root,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            ),
        )
        host.onBackPressedDispatcher.addCallback(host, backCallback)

        binding.root.viewTreeObserver.addOnPreDrawListener(repositionListener)
        binding.root.doOnLayout {
            binding.root.announceForAccessibility("$title. $body")
        }
        if (pulseAnchor) startPulse()
        host.lifecycle.addObserver(lifecycleObserver)
    }

    fun dismiss(immediate: Boolean = false) {
        if (removalComplete) return
        if (!dismissed) {
            dismissed = true
            backCallback.remove()
            pulseAnimator?.cancel()
            pulseAnimator = null
            anchor.animate().cancel()
            anchor.scaleX = originalScaleX
            anchor.scaleY = originalScaleY
        }

        if (immediate) {
            binding.root.animate().cancel()
            finishDismiss()
            return
        }
        if (dismissAnimationStarted) return
        dismissAnimationStarted = true
        binding.root.animate()
            .alpha(0f)
            .setDuration(DISMISS_DURATION_MS)
            .withEndAction(::finishDismiss)
            .start()
    }

    private fun finishDismiss() {
        if (removalComplete) return
        removalComplete = true
        // Both before removeView: detaching the root swaps its ViewTreeObserver
        // for a fresh floating one, which would strand the pre-draw listener in
        // the window's observer.
        binding.root.viewTreeObserver.takeIf { it.isAlive }
            ?.removeOnPreDrawListener(repositionListener)
        host.lifecycle.removeObserver(lifecycleObserver)
        binding.root.animate().setListener(null)
        (binding.root.parent as? ViewGroup)?.removeView(binding.root)
        onDismissed()
    }

    private fun positionBubble() {
        val horizontalMargin = HORIZONTAL_MARGIN_DP.dp
        val gap = ANCHOR_GAP_DP.dp

        val rootLocation = IntArray(2)
        val anchorLocation = IntArray(2)
        binding.root.getLocationOnScreen(rootLocation)
        anchor.getLocationOnScreen(anchorLocation)

        // getLocationOnScreen reports the *scaled* corner, which the pulse
        // moves every frame. Scaling leaves the centre where it is, so measure
        // from there and place against the anchor's resting bounds.
        val centerX = anchorLocation[0] - rootLocation[0] + anchor.width * anchor.scaleX / 2f
        val centerY = anchorLocation[1] - rootLocation[1] + anchor.height * anchor.scaleY / 2f
        val anchorLeft = (centerX - anchor.width / 2f).roundToInt()
        val anchorTop = (centerY - anchor.height / 2f).roundToInt()
        val anchorRight = anchorLeft + anchor.width
        val anchorBottom = anchorTop + anchor.height
        val bubbleWidth = binding.coachBubble.width
        val bubbleHeight = binding.coachBubble.height

        val centeredX = (anchorLeft + anchorRight - bubbleWidth) / 2
        val x = centeredX.coerceIn(
            horizontalMargin,
            (binding.root.width - horizontalMargin - bubbleWidth).coerceAtLeast(horizontalMargin),
        )
        val preferredY = when (placement) {
            Placement.ABOVE -> anchorTop - bubbleHeight - gap
            Placement.BELOW -> anchorBottom + gap
        }
        val alternateY = when (placement) {
            Placement.ABOVE -> anchorBottom + gap
            Placement.BELOW -> anchorTop - bubbleHeight - gap
        }
        val y = preferredY
            .takeIf { it >= gap && it + bubbleHeight <= binding.root.height - gap }
            ?: alternateY.coerceIn(gap, (binding.root.height - bubbleHeight - gap).coerceAtLeast(gap))

        binding.coachBubble.translationX = x.toFloat()
        binding.coachBubble.translationY = y.toFloat()
    }

    private fun startPulse() {
        val pulseX = ObjectAnimator.ofFloat(anchor, View.SCALE_X, originalScaleX, originalScaleX * PULSE_SCALE)
        val pulseY = ObjectAnimator.ofFloat(anchor, View.SCALE_Y, originalScaleY, originalScaleY * PULSE_SCALE)
        listOf(pulseX, pulseY).forEach {
            it.duration = PULSE_DURATION_MS
            it.repeatCount = ObjectAnimator.INFINITE
            it.repeatMode = ObjectAnimator.REVERSE
        }
        pulseAnimator = AnimatorSet().apply {
            playTogether(pulseX, pulseY)
            start()
        }
    }

    private val Int.dp: Int
        get() = (this * host.resources.displayMetrics.density).toInt()

    companion object {
        private const val COACH_WIDTH_DP = 300
        private const val HORIZONTAL_MARGIN_DP = 16
        private const val ANCHOR_GAP_DP = 12
        private const val PULSE_SCALE = 1.14f
        private const val PULSE_DURATION_MS = 650L
        private const val DISMISS_DURATION_MS = 140L

        fun show(
            host: AppCompatActivity,
            anchor: View,
            title: CharSequence,
            body: CharSequence,
            dismissText: CharSequence,
            placement: Placement,
            pulseAnchor: Boolean,
            onDismissed: () -> Unit = {},
        ): CoachMarkOverlay? {
            if (!anchor.isAttachedToWindow || host.isFinishing || host.isDestroyed) return null
            return CoachMarkOverlay(
                host = host,
                anchor = anchor,
                title = title,
                body = body,
                dismissText = dismissText,
                placement = placement,
                pulseAnchor = pulseAnchor,
                onDismissed = onDismissed,
            )
        }
    }
}
