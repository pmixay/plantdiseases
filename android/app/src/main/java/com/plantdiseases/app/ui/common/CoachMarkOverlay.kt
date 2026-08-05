package com.plantdiseases.app.ui.common

import android.animation.AnimatorSet
import android.animation.ObjectAnimator
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.doOnLayout
import com.plantdiseases.app.databinding.ViewCoachMarkBinding

/**
 * A small, dependency-free coach mark anchored to an existing view.
 *
 * It is attached to the activity content dynamically, which lets feature
 * screens use it without reserving permanent space or coupling their layouts
 * to onboarding. The activity lifecycle and Back dispatcher both dismiss it,
 * and the anchor's original scale is restored after the optional pulse.
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

        binding.root.doOnLayout {
            positionBubble()
            binding.root.announceForAccessibility("$title. $body")
        }
        if (pulseAnchor) startPulse()
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

        val anchorLeft = anchorLocation[0] - rootLocation[0]
        val anchorTop = anchorLocation[1] - rootLocation[1]
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
