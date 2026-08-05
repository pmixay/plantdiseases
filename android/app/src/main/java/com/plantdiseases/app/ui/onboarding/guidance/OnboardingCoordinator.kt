package com.plantdiseases.app.ui.onboarding.guidance

import android.graphics.Rect
import android.view.View
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.NestedScrollView
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.ui.common.SpotlightOverlay
import com.plantdiseases.app.util.OnboardingStateStore

/** Coordinates persistence, registered screen anchors, scrolling, and overlay controls. */
class OnboardingCoordinator(
    private val host: AppCompatActivity,
    private val store: OnboardingStateStore,
    private val isAnotherOverlayVisible: () -> Boolean = { false },
    private val undimmedViews: (OnboardingContext) -> List<View> = { emptyList() },
    private val navigateToContext: (OnboardingContext) -> Boolean = { false },
) : DefaultLifecycleObserver {

    private data class Anchor(
        val view: View,
        val available: () -> Boolean,
        val onTarget: () -> Unit,
        val pauseAfterTarget: Boolean,
        val onInlineAction: (() -> Unit)?,
    )

    private val anchors = mutableMapOf<OnboardingAnchorKey, Anchor>()
    private var context: OnboardingContext? = null
    private var overlay: SpotlightOverlay? = null
    private var scheduled = false
    private var showGeneration = 0

    private val backCallback = object : OnBackPressedCallback(false) {
        override fun handleOnBackPressed() = previous()
    }

    init {
        host.lifecycle.addObserver(this)
        host.onBackPressedDispatcher.addCallback(host, backCallback)
    }

    fun setContext(newContext: OnboardingContext?) {
        if (context == newContext) {
            schedule()
            return
        }
        context = newContext
        showGeneration++
        dismissOverlay(immediate = false)
        schedule()
    }

    fun registerAnchor(
        key: OnboardingAnchorKey,
        view: View,
        available: () -> Boolean = { view.isShown },
        pauseAfterTarget: Boolean = false,
        onTarget: () -> Unit = { view.performClick() },
        onInlineAction: (() -> Unit)? = null,
    ) {
        anchors[key] = Anchor(view, available, onTarget, pauseAfterTarget, onInlineAction)
        schedule()
    }

    fun unregisterAnchor(key: OnboardingAnchorKey, view: View? = null) {
        val registered = anchors[key] ?: return
        if (view == null || registered.view === view) anchors.remove(key)
    }

    fun resumeAfterExternalUi() {
        dismissOverlay(immediate = true)
        schedule()
    }

    fun moveTo(step: OnboardingStepId) {
        val catalogStep = OnboardingCatalog.step(step)
        store.moveTo(catalogStep.chapter, step)
        dismissOverlay(immediate = true)
        schedule()
    }

    fun replay() {
        store.replay()
        dismissOverlay(immediate = true)
    }

    override fun onResume(owner: LifecycleOwner) = schedule()

    override fun onPause(owner: LifecycleOwner) {
        showGeneration++
        dismissOverlay(immediate = true)
    }

    override fun onDestroy(owner: LifecycleOwner) {
        showGeneration++
        dismissOverlay(immediate = true)
        anchors.clear()
        backCallback.remove()
        host.lifecycle.removeObserver(this)
    }

    private fun schedule(delayMs: Long = SETTLE_DELAY_MS) {
        if (scheduled || host.isFinishing || host.isDestroyed) return
        scheduled = true
        host.window.decorView.postDelayed({
            scheduled = false
            tryShow()
        }, delayMs)
    }

    private fun tryShow() {
        val activeContext = context ?: return
        if (
            !host.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED) ||
            !host.hasWindowFocus() ||
            isAnotherOverlayVisible()
        ) {
            schedule()
            return
        }
        val chapter = activeChapterFor(activeContext)
        if (chapter == null) {
            dismissOverlay(immediate = false)
            return
        }
        val progress = store.progress(chapter)
        val step = OnboardingCatalog.step(progress.currentStep)
        if (step.context != activeContext) {
            dismissOverlay(immediate = false)
            return
        }
        val anchor = anchors[step.anchor] ?: run {
            schedule()
            return
        }
        if (!anchor.available() || !isReady(anchor.view)) {
            if (step.skipWhenUnavailable) {
                if (store.wasStepShown(chapter, step.id)) {
                    store.advance(chapter)
                } else {
                    store.omitAndAdvance(chapter, step.id)
                }
                tryShow()
            } else {
                schedule()
            }
            return
        }

        val generation = ++showGeneration
        scrollIntoView(anchor.view)
        host.window.decorView.postDelayed({
            if (generation != showGeneration || context != activeContext) return@postDelayed
            if (!anchor.available() || !anchor.view.isAttachedToWindow) {
                schedule()
                return@postDelayed
            }
            show(step, anchor)
        }, scrollDelay())
    }

    private fun show(step: OnboardingStep, anchor: Anchor) {
        store.markStepShown(step.chapter, step.id)
        val chapterSteps = visibleSteps(step)
        val index = chapterSteps.indexOfFirst { it.id == step.id }
        val callbacks = object : SpotlightOverlay.Callbacks {
            override fun onPrevious() = previous()
            override fun onNext() = next()
            override fun onSkipSection() = skipSection()
            override fun onSkipAll() = confirmSkipAll()
            override fun onTarget() {
                val current = currentStep() ?: return
                val currentAnchor = anchors[current.anchor] ?: return
                target(current, currentAnchor)
            }
            override fun onInlineAction() = inlineAction()
        }
        val currentOverlay = overlay ?: SpotlightOverlay.show(host, callbacks)?.also {
            overlay = it
            // ShellFragment installs its tab-level callback after the activity
            // coordinator. Re-adding ours here makes onboarding Back the topmost
            // enabled callback while the spotlight is visible.
            backCallback.remove()
            host.onBackPressedDispatcher.addCallback(host, backCallback)
            backCallback.isEnabled = true
        } ?: return
        currentOverlay.showStep(
            step = step,
            anchor = anchor.view,
            position = index + 1,
            count = chapterSteps.size,
            actionable = step.targetAction != OnboardingTargetAction.NONE,
            inlineActionable = anchor.onInlineAction != null,
            dimBackground = step.dimBackground,
            undimmedViews = undimmedViews(step.context),
        )
    }

    private fun next() {
        val step = currentStep() ?: return
        val anchor = anchors[step.anchor]
        store.advance(step.chapter)
        if (step.forwardUsesTarget && anchor != null) {
            dismissOverlay(immediate = false)
            anchor.onTarget()
            return
        }
        afterAdvance(step)
    }

    private fun previous() {
        val step = currentStep() ?: return
        val steps = OnboardingCatalog.steps(step.chapter)
        val index = steps.indexOfFirst { it.id == step.id }
        val previous = (index - 1 downTo 0)
            .asSequence()
            .map(steps::get)
            .filterNot { candidate -> store.wasStepOmitted(step.chapter, candidate.id) }
            .firstOrNull { candidate ->
                val candidateAnchor = anchors[candidate.anchor]
                !candidate.skipWhenUnavailable || candidateAnchor?.available() == true
            }
        if (previous == null) {
            confirmLeaveSection(step.chapter)
            return
        }
        store.moveTo(step.chapter, previous.id)
        showGeneration++
        if (previous.context == step.context) {
            schedule(SHORT_DELAY_MS)
        } else {
            dismissOverlay(immediate = false) {
                if (!navigateToContext(previous.context)) {
                    store.moveTo(step.chapter, step.id)
                    schedule(SHORT_DELAY_MS)
                }
            }
        }
    }

    private fun target(step: OnboardingStep, anchor: Anchor) {
        when (step.targetAction) {
            OnboardingTargetAction.NONE -> Unit
            OnboardingTargetAction.PAUSE -> {
                dismissOverlay(immediate = false)
                anchor.onTarget()
            }
            OnboardingTargetAction.ADVANCE -> {
                store.advance(step.chapter)
                if (anchor.pauseAfterTarget) dismissOverlay(immediate = false)
                anchor.onTarget()
                if (!anchor.pauseAfterTarget) afterAdvance(step)
            }
        }
    }

    private fun inlineAction() {
        val current = currentStep() ?: return
        val action = anchors[current.anchor]?.onInlineAction ?: return
        showGeneration++
        dismissOverlay(immediate = false, onEnd = action)
    }

    private fun afterAdvance(previousStep: OnboardingStep) {
        showGeneration++
        val progress = store.progress(previousStep.chapter)
        if (progress.status != OnboardingChapterStatus.PENDING) {
            dismissOverlay(immediate = false)
            return
        }
        val next = OnboardingCatalog.step(progress.currentStep)
        if (next.context != context) dismissOverlay(immediate = false)
        schedule(if (next.context == context) SHORT_DELAY_MS else SETTLE_DELAY_MS)
    }

    private fun skipSection() {
        val step = currentStep() ?: return
        store.skipChapter(step.chapter)
        dismissOverlay(immediate = false)
    }

    private fun confirmSkipAll() {
        dismissOverlay(immediate = false) {
            MaterialAlertDialogBuilder(host)
                .setTitle(R.string.guidance_skip_all_title)
                .setMessage(R.string.guidance_skip_all_body)
                .setPositiveButton(R.string.guidance_skip_all) { _, _ -> store.skipAll() }
                .setNegativeButton(R.string.cancel) { _, _ -> schedule(SHORT_DELAY_MS) }
                .setOnCancelListener { schedule(SHORT_DELAY_MS) }
                .show()
        }
    }

    private fun confirmLeaveSection(chapter: OnboardingChapter) {
        dismissOverlay(immediate = false) {
            MaterialAlertDialogBuilder(host)
                .setTitle(R.string.guidance_back_title)
                .setMessage(R.string.guidance_back_body)
                .setPositiveButton(R.string.guidance_continue) { _, _ -> schedule(SHORT_DELAY_MS) }
                .setNegativeButton(R.string.guidance_skip_section) { _, _ ->
                    store.skipChapter(chapter)
                }
                .setOnCancelListener { schedule(SHORT_DELAY_MS) }
                .show()
        }
    }

    private fun currentStep(): OnboardingStep? {
        val activeContext = context ?: return null
        val chapter = activeChapterFor(activeContext) ?: return null
        return OnboardingCatalog.step(store.progress(chapter).currentStep)
            .takeIf { it.context == activeContext }
    }

    private fun dismissOverlay(immediate: Boolean, onEnd: () -> Unit = {}) {
        val active = overlay
        overlay = null
        backCallback.isEnabled = false
        if (active == null) onEnd() else active.dismiss(immediate, onEnd)
    }

    private fun scrollIntoView(view: View) {
        var parent = view.parent
        while (parent is View) {
            if (parent is NestedScrollView) {
                val rect = Rect(0, 0, view.width, view.height)
                parent.offsetDescendantRectToMyCoords(view, rect)
                val targetY = (rect.top - 24.dp).coerceAtLeast(0)
                if (android.animation.ValueAnimator.areAnimatorsEnabled()) {
                    parent.smoothScrollTo(0, targetY)
                } else {
                    parent.scrollTo(0, targetY)
                }
                return
            }
            parent = parent.parent
        }
        view.requestRectangleOnScreen(Rect(0, 0, view.width, view.height), true)
    }

    private fun isReady(view: View): Boolean =
        view.isAttachedToWindow && view.width > 0 && view.height > 0

    private fun scrollDelay(): Long =
        if (android.animation.ValueAnimator.areAnimatorsEnabled()) SCROLL_DELAY_MS else SHORT_DELAY_MS

    private val Int.dp: Int
        get() = (this * host.resources.displayMetrics.density).toInt()

    private fun activeChapterFor(context: OnboardingContext): OnboardingChapter? =
        chaptersFor(context).firstOrNull(store::isEligible)

    private fun visibleSteps(current: OnboardingStep): List<OnboardingStep> =
        OnboardingCatalog.steps(current.chapter).filter { candidate ->
            if (store.wasStepOmitted(current.chapter, candidate.id)) return@filter false
            !candidate.conditional ||
                candidate.id == current.id ||
                store.wasStepShown(current.chapter, candidate.id) ||
                anchors[candidate.anchor]?.available() == true
        }

    companion object {
        private const val SETTLE_DELAY_MS = 500L
        private const val SCROLL_DELAY_MS = 420L
        private const val SHORT_DELAY_MS = 80L

        private fun chaptersFor(context: OnboardingContext): List<OnboardingChapter> = when (context) {
            OnboardingContext.HOME,
            OnboardingContext.CAMERA,
            -> listOf(OnboardingChapter.HOME_SCAN)
            OnboardingContext.RESULT -> listOf(
                OnboardingChapter.GARDEN_SCAN_ATTACH,
                OnboardingChapter.HOME_SCAN,
            )
            OnboardingContext.GARDEN -> listOf(OnboardingChapter.GARDEN)
            OnboardingContext.GARDEN_PLANT_DETAILS -> listOf(OnboardingChapter.GARDEN_PLANT_DETAILS)
            OnboardingContext.GALLERY -> listOf(OnboardingChapter.GALLERY)
            OnboardingContext.PROFILE -> listOf(OnboardingChapter.PROFILE)
            OnboardingContext.SPECIES_DETAILS -> listOf(OnboardingChapter.SPECIES_DETAILS)
        }
    }
}
