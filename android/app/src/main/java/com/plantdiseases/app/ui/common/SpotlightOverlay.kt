package com.plantdiseases.app.ui.common

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.drawable.ColorDrawable
import android.text.SpannableString
import android.text.Spanned
import android.text.TextPaint
import android.text.method.LinkMovementMethod
import android.text.style.ClickableSpan
import android.util.AttributeSet
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.ViewTreeObserver
import android.view.accessibility.AccessibilityEvent
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.widget.NestedScrollView
import androidx.interpolator.view.animation.FastOutSlowInInterpolator
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.R
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingStep
import kotlin.math.roundToInt

/**
 * Activity-scoped interactive spotlight. One instance morphs between anchors,
 * while its full-screen touch layer blocks everything except the controls and
 * explicitly actionable cutouts.
 */
class SpotlightOverlay private constructor(
    private val host: AppCompatActivity,
    private val callbacks: Callbacks,
) {

    interface Callbacks {
        fun onPrevious()
        fun onNext()
        fun onSkipSection()
        fun onSkipAll()
        fun onTarget()
        fun onInlineAction()
    }

    private val parent = host.findViewById<ViewGroup>(android.R.id.content)
    private val root = SpotlightTouchLayout(host)
    private val scrim = SpotlightScrimView(host)
    private val bubble = MaterialCardView(host)
    private val title = TextView(host)
    private val body = TextView(host)
    private val counter = TextView(host)
    private val previous = ImageButton(host)
    private val next = ImageButton(host)
    private val skipSection = MaterialButton(host)
    private val skipAll = MaterialButton(host)
    private val underlyingAccessibility = mutableMapOf<View, Int>()
    private var anchor: View? = null
    private var step: OnboardingStep? = null
    private var dismissed = false
    private var geometryAnimator: ValueAnimator? = null
    private var bubbleAnimator: ValueAnimator? = null
    private var currentBubbleX = 0f
    private var currentBubbleY = 0f
    private var undimmedViews: List<View> = emptyList()
    private var transitioning = false
    private var transitionGeneration = 0
    private var pendingTransitionPreDraw: ViewTreeObserver.OnPreDrawListener? = null
    private val transitionInterpolator = FastOutSlowInInterpolator()

    private val preDrawListener = ViewTreeObserver.OnPreDrawListener {
        if (!transitioning && geometryAnimator?.isRunning != true) followAnchor()
        true
    }

    private val lifecycleObserver = object : DefaultLifecycleObserver {
        override fun onDestroy(owner: LifecycleOwner) = dismiss(immediate = true)
    }

    init {
        buildViewTree()
        root.onCutoutTap = { if (!transitioning) callbacks.onTarget() }
        root.setOnClickListener {
            if (!transitioning && root.cutoutActionable) callbacks.onTarget()
        }
        previous.setOnClickListener { if (!transitioning) callbacks.onPrevious() }
        next.setOnClickListener { if (!transitioning) callbacks.onNext() }
        skipSection.setOnClickListener { if (!transitioning) callbacks.onSkipSection() }
        skipAll.setOnClickListener { if (!transitioning) callbacks.onSkipAll() }

        for (index in 0 until parent.childCount) {
            parent.getChildAt(index).let { child ->
                underlyingAccessibility[child] = child.importantForAccessibility
                child.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
            }
        }
        parent.addView(
            root,
            ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            ),
        )
        root.viewTreeObserver.addOnPreDrawListener(preDrawListener)
        host.lifecycle.addObserver(lifecycleObserver)
        root.alpha = 0f
        root.animate().alpha(1f).setDuration(entranceDuration()).start()
    }

    fun showStep(
        step: OnboardingStep,
        anchor: View,
        position: Int,
        count: Int,
        actionable: Boolean,
        inlineActionable: Boolean,
        dimBackground: Boolean,
        undimmedViews: List<View> = emptyList(),
    ) {
        if (dismissed || !anchor.isAttachedToWindow) return
        val firstStep = this.step == null
        val presentation = StepPresentation(
            step = step,
            anchor = anchor,
            position = position,
            count = count,
            actionable = actionable,
            dimBackground = dimBackground,
            undimmedViews = undimmedViews,
            title = host.getString(step.titleRes),
            body = bodyPresentation(step, inlineActionable),
        )
        if (firstStep || !animationsEnabled()) {
            applyStep(presentation, animateScrim = false)
            bubble.alpha = 1f
            announceStep(presentation)
            if (firstStep) {
                root.post {
                    if (!dismissed && this.step?.id == presentation.step.id) positionAtAnchor()
                }
            } else {
                positionAtAnchor()
            }
        } else {
            transitionTo(presentation)
        }
    }

    fun dismiss(immediate: Boolean = false, onEnd: () -> Unit = {}) {
        if (dismissed) {
            onEnd()
            return
        }
        dismissed = true
        transitionGeneration++
        transitioning = false
        geometryAnimator?.cancel()
        bubbleAnimator?.cancel()
        bubble.animate().cancel()
        removePendingTransitionPreDraw()
        root.viewTreeObserver.takeIf { it.isAlive }?.removeOnPreDrawListener(preDrawListener)
        host.lifecycle.removeObserver(lifecycleObserver)
        if (immediate || !animationsEnabled()) {
            (root.parent as? ViewGroup)?.removeView(root)
            restoreUnderlyingAccessibility()
            onEnd()
        } else {
            root.animate().alpha(0f).setDuration(DISMISS_MS).withEndAction {
                (root.parent as? ViewGroup)?.removeView(root)
                restoreUnderlyingAccessibility()
                onEnd()
            }.start()
        }
    }

    private fun buildViewTree() {
        root.isClickable = true
        root.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_YES
        root.addView(
            scrim,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            ),
        )

        bubble.radius = 20.dp.toFloat()
        bubble.cardElevation = 8.dp.toFloat()
        bubble.setCardBackgroundColor(resolveColor(com.google.android.material.R.attr.colorSurface))
        bubble.contentDescription = host.getString(R.string.guidance_title_home_scan)
        ViewCompat.setAccessibilityPaneTitle(bubble, host.getString(R.string.guidance_title_home_scan))

        val content = LinearLayout(host).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(20.dp, 18.dp, 20.dp, 14.dp)
        }
        val copyScroll = NestedScrollView(host).apply {
            isFillViewport = false
            overScrollMode = View.OVER_SCROLL_IF_CONTENT_SCROLLS
        }
        val copyColumn = LinearLayout(host).apply {
            orientation = LinearLayout.VERTICAL
        }
        title.apply {
            setTextColor(resolveColor(com.google.android.material.R.attr.colorOnSurface))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            ViewCompat.setAccessibilityHeading(this, true)
        }
        body.apply {
            setTextColor(resolveColor(com.google.android.material.R.attr.colorOnSurfaceVariant))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setLineSpacing(3.dp.toFloat(), 1f)
        }
        copyColumn.addView(title, matchWrap())
        copyColumn.addView(body, matchWrap().apply { topMargin = 8.dp })
        copyScroll.addView(copyColumn, matchWrap())
        content.addView(
            copyScroll,
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, 1f),
        )

        val navigation = LinearLayout(host).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        styleIconButton(previous, R.drawable.ic_back, R.string.guidance_previous)
        styleIconButton(next, R.drawable.ic_back, R.string.guidance_next)
        next.rotation = 180f
        counter.apply {
            gravity = Gravity.CENTER
            setTextColor(resolveColor(com.google.android.material.R.attr.colorOnSurfaceVariant))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        }
        navigation.addView(previous, LinearLayout.LayoutParams(48.dp, 48.dp))
        navigation.addView(counter, LinearLayout.LayoutParams(0, 48.dp, 1f))
        navigation.addView(next, LinearLayout.LayoutParams(48.dp, 48.dp))
        content.addView(navigation, matchWrap().apply { topMargin = 8.dp })

        val actions = LinearLayout(host).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        styleTextButton(skipSection, R.string.guidance_skip_section)
        styleTextButton(skipAll, R.string.guidance_skip_all)
        actions.addView(
            skipSection,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                marginEnd = 4.dp
            },
        )
        actions.addView(
            skipAll,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                marginStart = 4.dp
            },
        )
        content.addView(actions, matchWrap())

        bubble.addView(content)
        root.bubble = bubble
        root.addView(
            bubble,
            FrameLayout.LayoutParams(
                minOf(360.dp, host.resources.displayMetrics.widthPixels - 32.dp),
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ),
        )
    }

    private fun restoreUnderlyingAccessibility() {
        underlyingAccessibility.forEach { (view, importance) ->
            view.importantForAccessibility = importance
        }
        underlyingAccessibility.clear()
    }

    private fun styleIconButton(button: ImageButton, icon: Int, description: Int) {
        button.setImageResource(icon)
        button.setColorFilter(resolveColor(com.google.android.material.R.attr.colorPrimary))
        button.contentDescription = host.getString(description)
        button.background = ColorDrawable(Color.TRANSPARENT)
        button.scaleType = android.widget.ImageView.ScaleType.CENTER
        button.isFocusable = true
    }

    private fun styleTextButton(button: MaterialButton, text: Int) {
        button.setText(text)
        button.minHeight = 48.dp
        button.minimumHeight = 48.dp
        button.minWidth = 0
        button.minimumWidth = 0
        button.insetTop = 0
        button.insetBottom = 0
        button.maxLines = 2
        button.setPadding(8.dp, 0, 8.dp, 0)
        button.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
    }

    private data class BodyPresentation(
        val text: CharSequence,
        val plainText: String,
        val hasLink: Boolean,
    )

    private data class StepPresentation(
        val step: OnboardingStep,
        val anchor: View,
        val position: Int,
        val count: Int,
        val actionable: Boolean,
        val dimBackground: Boolean,
        val undimmedViews: List<View>,
        val title: String,
        val body: BodyPresentation,
    )

    private fun applyStep(presentation: StepPresentation, animateScrim: Boolean) {
        step = presentation.step
        anchor = presentation.anchor
        undimmedViews = presentation.undimmedViews
        updateScrimVisibility(presentation.dimBackground, animateScrim)
        root.cutoutActionable = presentation.actionable
        root.isClickable = presentation.actionable
        root.contentDescription = if (presentation.actionable) {
            host.getString(R.string.guidance_activate_target)
        } else {
            null
        }
        counter.text = host.getString(
            R.string.guidance_step_counter,
            presentation.position,
            presentation.count,
        )
        title.text = presentation.title
        applyBody(presentation.body)
    }

    private fun announceStep(presentation: StepPresentation) {
        updateAccessibility(presentation.title, presentation.body.plainText)
    }

    private fun transitionTo(presentation: StepPresentation) {
        val generation = ++transitionGeneration
        transitioning = true
        root.cutoutActionable = false
        root.isClickable = false
        bubble.animate().cancel()
        bubble.animate()
            .alpha(0f)
            .setDuration(CARD_FADE_OUT_MS)
            .setInterpolator(transitionInterpolator)
            .withEndAction {
                if (dismissed || generation != transitionGeneration) return@withEndAction
                applyStep(presentation, animateScrim = true)
                afterNextLayout {
                    if (dismissed || generation != transitionGeneration) return@afterNextLayout
                    morphToAnchor {
                        if (dismissed || generation != transitionGeneration) return@morphToAnchor
                        announceStep(presentation)
                        bubble.animate().cancel()
                        bubble.animate()
                            .alpha(1f)
                            .setDuration(CARD_FADE_IN_MS)
                            .setInterpolator(transitionInterpolator)
                            .withEndAction {
                                if (generation == transitionGeneration) transitioning = false
                            }
                            .start()
                    }
                }
            }
            .start()
    }

    private fun afterNextLayout(action: () -> Unit) {
        removePendingTransitionPreDraw()
        val listener = object : ViewTreeObserver.OnPreDrawListener {
            override fun onPreDraw(): Boolean {
                removePendingTransitionPreDraw()
                action()
                return true
            }
        }
        pendingTransitionPreDraw = listener
        root.viewTreeObserver.addOnPreDrawListener(listener)
        root.requestLayout()
        root.invalidate()
    }

    private fun removePendingTransitionPreDraw() {
        val listener = pendingTransitionPreDraw ?: return
        root.viewTreeObserver.takeIf { it.isAlive }?.removeOnPreDrawListener(listener)
        pendingTransitionPreDraw = null
    }

    private fun bodyPresentation(step: OnboardingStep, inlineActionable: Boolean): BodyPresentation {
        val linkRes = step.inlineLinkTextRes
        if (linkRes == null) {
            val plain = host.getString(step.bodyRes)
            return BodyPresentation(plain, plain, hasLink = false)
        }
        val linkText = host.getString(linkRes)
        val plain = host.getString(step.bodyRes, linkText)
        if (!inlineActionable) return BodyPresentation(plain, plain, hasLink = false)
        val start = plain.indexOf(linkText)
        if (start < 0) return BodyPresentation(plain, plain, hasLink = false)
        val styled = SpannableString(plain).apply {
            setSpan(
                object : ClickableSpan() {
                    override fun onClick(widget: View) = callbacks.onInlineAction()

                    override fun updateDrawState(ds: TextPaint) {
                        ds.color = resolveColor(com.google.android.material.R.attr.colorPrimary)
                        ds.isUnderlineText = true
                    }
                },
                start,
                start + linkText.length,
                Spanned.SPAN_EXCLUSIVE_EXCLUSIVE,
            )
        }
        return BodyPresentation(styled, plain, hasLink = true)
    }

    private fun applyBody(presentation: BodyPresentation) {
        body.text = presentation.text
        body.movementMethod = if (presentation.hasLink) LinkMovementMethod.getInstance() else null
        body.highlightColor = Color.TRANSPARENT
    }

    private fun updateScrimVisibility(dimBackground: Boolean, animate: Boolean) {
        val targetAlpha = if (dimBackground) 1f else 0f
        scrim.animate().cancel()
        if (animate && animationsEnabled()) {
            scrim.animate()
                .alpha(targetAlpha)
                .setDuration(MORPH_MS)
                .setInterpolator(transitionInterpolator)
                .start()
        } else {
            scrim.alpha = targetAlpha
        }
    }

    private fun updateAccessibility(newTitle: String, newBody: String) {
        bubble.contentDescription = "$newTitle. $newBody. ${counter.text}"
        ViewCompat.setAccessibilityPaneTitle(bubble, newTitle)
        bubble.sendAccessibilityEvent(AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED)
    }

    private fun positionAtAnchor() {
        if (dismissed) return
        val targetRect = anchorRect() ?: return
        geometryAnimator?.cancel()
        bubbleAnimator?.cancel()
        updateUndimmedCutouts()
        val bubblePosition = bubblePosition(targetRect)
        scrim.cutout = targetRect
        moveBubble(bubblePosition.first, bubblePosition.second)
    }

    private fun morphToAnchor(onEnd: () -> Unit) {
        val targetRect = anchorRect()
        if (targetRect == null) {
            onEnd()
            return
        }
        updateUndimmedCutouts()
        val bubblePosition = bubblePosition(targetRect)
        val startRect = if (scrim.cutout.isEmpty) targetRect else RectF(scrim.cutout)
        val startX = currentBubbleX
        val startY = currentBubbleY
        geometryAnimator?.cancel()
        bubbleAnimator?.cancel()
        val duration = duration()
        if (duration == 0L) {
            scrim.cutout = targetRect
            moveBubble(bubblePosition.first, bubblePosition.second)
            onEnd()
            return
        }
        geometryAnimator = ValueAnimator.ofFloat(0f, 1f).apply {
            this.duration = duration
            interpolator = transitionInterpolator
            var cancelled = false
            addUpdateListener { animator ->
                val fraction = animator.animatedFraction
                scrim.cutout = RectF(
                    lerp(startRect.left, targetRect.left, fraction),
                    lerp(startRect.top, targetRect.top, fraction),
                    lerp(startRect.right, targetRect.right, fraction),
                    lerp(startRect.bottom, targetRect.bottom, fraction),
                )
            }
            addListener(object : AnimatorListenerAdapter() {
                override fun onAnimationCancel(animation: Animator) {
                    cancelled = true
                }

                override fun onAnimationEnd(animation: Animator) {
                    if (cancelled) return
                    scrim.cutout = targetRect
                    moveBubble(bubblePosition.first, bubblePosition.second)
                    onEnd()
                }
            })
            start()
        }
        bubbleAnimator = ValueAnimator.ofFloat(0f, 1f).apply {
            this.duration = duration
            interpolator = transitionInterpolator
            addUpdateListener { animator ->
                val fraction = animator.animatedFraction
                moveBubble(
                    lerp(startX, bubblePosition.first, fraction),
                    lerp(startY, bubblePosition.second, fraction),
                )
            }
            start()
        }
    }

    private fun followAnchor() {
        val targetRect = anchorRect() ?: return
        updateUndimmedCutouts()
        if (!approximatelyEqual(scrim.cutout, targetRect)) scrim.cutout = targetRect
        val position = bubblePosition(targetRect)
        moveBubble(position.first, position.second)
    }

    private fun anchorRect(): RectF? {
        val target = anchor?.takeIf { it.isAttachedToWindow && it.width > 0 && it.height > 0 } ?: return null
        val rootLocation = IntArray(2)
        val targetLocation = IntArray(2)
        root.getLocationOnScreen(rootLocation)
        target.getLocationOnScreen(targetLocation)
        return RectF(
            (targetLocation[0] - rootLocation[0] - SPOTLIGHT_PADDING_DP.dp).toFloat(),
            (targetLocation[1] - rootLocation[1] - SPOTLIGHT_PADDING_DP.dp).toFloat(),
            (targetLocation[0] - rootLocation[0] + target.width + SPOTLIGHT_PADDING_DP.dp).toFloat(),
            (targetLocation[1] - rootLocation[1] + target.height + SPOTLIGHT_PADDING_DP.dp).toFloat(),
        )
    }

    private fun bubblePosition(cutout: RectF): Pair<Float, Float> {
        val horizontalMargin = 16.dp
        val verticalMargin = 12.dp
        val bars = ViewCompat.getRootWindowInsets(root)?.getInsets(
            WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout(),
        )
        val safeTop = (bars?.top ?: 0) + verticalMargin
        val safeBottom = root.height - (bars?.bottom ?: 0) - verticalMargin
        val maxHeight = (safeBottom - safeTop).coerceAtLeast(1)
        val params = bubble.layoutParams
        if (bubble.measuredHeight > maxHeight && params.height != maxHeight) {
            params.height = maxHeight
            bubble.layoutParams = params
        }
        val width = bubble.measuredWidth.coerceAtLeast(1)
        val height = bubble.measuredHeight.coerceAtMost(maxHeight).coerceAtLeast(1)
        val x = (cutout.centerX() - width / 2f).coerceIn(
            horizontalMargin.toFloat(),
            (root.width - horizontalMargin - width).coerceAtLeast(horizontalMargin).toFloat(),
        )
        val above = cutout.top - height - 12.dp
        val below = cutout.bottom + 12.dp
        val y = when {
            below + height <= safeBottom -> below
            above >= safeTop -> above
            else -> safeTop.toFloat()
        }
        return x to y
    }

    private fun updateUndimmedCutouts() {
        scrim.extraCutouts = undimmedViews.mapNotNull { view ->
            viewRect(view, padding = 0)
        }
    }

    private fun viewRect(view: View, padding: Int): RectF? {
        if (!view.isAttachedToWindow || view.width <= 0 || view.height <= 0) return null
        val rootLocation = IntArray(2)
        val viewLocation = IntArray(2)
        root.getLocationOnScreen(rootLocation)
        view.getLocationOnScreen(viewLocation)
        return RectF(
            (viewLocation[0] - rootLocation[0] - padding).toFloat(),
            (viewLocation[1] - rootLocation[1] - padding).toFloat(),
            (viewLocation[0] - rootLocation[0] + view.width + padding).toFloat(),
            (viewLocation[1] - rootLocation[1] + view.height + padding).toFloat(),
        )
    }

    private fun moveBubble(x: Float, y: Float) {
        currentBubbleX = x
        currentBubbleY = y
        bubble.translationX = x
        bubble.translationY = y
    }

    private fun resolveColor(attribute: Int): Int {
        val value = TypedValue()
        host.theme.resolveAttribute(attribute, value, true)
        return if (value.resourceId != 0) ContextCompat.getColor(host, value.resourceId) else value.data
    }

    private fun matchWrap() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT,
    )

    private fun animationsEnabled(): Boolean = ValueAnimator.areAnimatorsEnabled()

    private fun duration(): Long = if (animationsEnabled()) MORPH_MS else 0L

    private fun entranceDuration(): Long = if (animationsEnabled()) ENTRANCE_MS else 0L

    private val Int.dp: Int
        get() = (this * host.resources.displayMetrics.density).roundToInt()

    companion object {
        private const val SPOTLIGHT_PADDING_DP = 8
        private const val MORPH_MS = 650L
        private const val CARD_FADE_OUT_MS = 180L
        private const val CARD_FADE_IN_MS = 180L
        private const val ENTRANCE_MS = 180L
        private const val DISMISS_MS = 180L

        fun show(host: AppCompatActivity, callbacks: Callbacks): SpotlightOverlay? {
            if (host.isFinishing || host.isDestroyed) return null
            return SpotlightOverlay(host, callbacks)
        }

        private fun lerp(start: Float, end: Float, fraction: Float) = start + (end - start) * fraction

        private fun approximatelyEqual(first: RectF, second: RectF): Boolean =
            kotlin.math.abs(first.left - second.left) < 1f &&
                kotlin.math.abs(first.top - second.top) < 1f &&
                kotlin.math.abs(first.right - second.right) < 1f &&
                kotlin.math.abs(first.bottom - second.bottom) < 1f
    }
}

private class SpotlightTouchLayout(context: Context) : FrameLayout(context) {
    var bubble: View? = null
    var cutoutActionable: Boolean = false
    var onCutoutTap: (() -> Unit)? = null
    private var downInCutout = false

    override fun onInterceptTouchEvent(event: MotionEvent): Boolean = !isInsideBubble(event)

    private fun isInsideBubble(event: MotionEvent): Boolean {
        val bubble = bubble ?: return false
        val insideHorizontally = event.x >= bubble.translationX &&
            event.x <= bubble.translationX + bubble.width
        val insideVertically = event.y >= bubble.translationY &&
            event.y <= bubble.translationY + bubble.height
        return insideHorizontally && insideVertically
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        val cutout = (getChildAt(0) as? SpotlightScrimView)?.cutout ?: return true
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> downInCutout = cutoutActionable && cutout.contains(event.x, event.y)
            MotionEvent.ACTION_UP -> {
                if (downInCutout && cutout.contains(event.x, event.y)) onCutoutTap?.invoke()
                downInCutout = false
            }
            MotionEvent.ACTION_CANCEL -> downInCutout = false
        }
        return true
    }
}

private class SpotlightScrimView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {
    private val scrimPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xAD000000.toInt() }
    private val outlinePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(190, 255, 255, 255)
        style = Paint.Style.STROKE
        strokeWidth = 1.5f * resources.displayMetrics.density
    }
    private val path = Path()
    var cutout: RectF = RectF()
        set(value) {
            field = RectF(value)
            invalidate()
        }
    var extraCutouts: List<RectF> = emptyList()
        set(value) {
            field = value.map { RectF(it) }
            invalidate()
        }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        path.reset()
        path.fillType = Path.FillType.EVEN_ODD
        path.addRect(0f, 0f, width.toFloat(), height.toFloat(), Path.Direction.CW)
        if (!cutout.isEmpty) {
            path.addRoundRect(cutout, 14.dp, 14.dp, Path.Direction.CW)
        }
        extraCutouts.forEach { extra ->
            path.addRect(extra, Path.Direction.CW)
        }
        canvas.drawPath(path, scrimPaint)
        if (!cutout.isEmpty) canvas.drawRoundRect(cutout, 14.dp, 14.dp, outlinePaint)
    }

    private val Int.dp: Float
        get() = this * resources.displayMetrics.density
}
