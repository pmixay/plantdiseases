package com.plantdiseases.app.ui.common

import android.transition.ChangeBounds
import android.transition.Fade
import android.transition.TransitionSet
import android.transition.TransitionManager
import android.view.View
import android.view.ViewGroup
import android.view.animation.DecelerateInterpolator

/** Shared motion for compact cards whose header stays visible while their body expands. */
object ExpandableSectionAnimator {
    private const val DURATION_MS = 300L

    fun update(
        container: ViewGroup,
        expanded: Boolean,
        chevron: View? = null,
        animate: Boolean = true,
        updateVisibility: () -> Unit,
    ) {
        // The transition root must also contain the card's siblings. Animating
        // only the card makes its body fade correctly, but everything below it
        // jumps straight into the vacated space because those views were never
        // part of ChangeBounds. Walk through single-child wrappers (card,
        // runtime slot, etc.) until reaching the first parent that owns sibling
        // sections, then animate that whole local layout together.
        val transitionRoot = surroundingSectionsRoot(container)
        val canAnimate = animate && transitionRoot.isLaidOut
        if (canAnimate) {
            TransitionManager.endTransitions(transitionRoot)
            TransitionManager.beginDelayedTransition(
                transitionRoot,
                TransitionSet().apply {
                    ordering = TransitionSet.ORDERING_TOGETHER
                    addTransition(ChangeBounds())
                    addTransition(Fade())
                    duration = DURATION_MS
                    interpolator = DecelerateInterpolator()
                },
            )
        }

        updateVisibility()
        chevron?.animate()?.cancel()
        if (canAnimate && chevron != null) {
            chevron.animate()
                .rotation(if (expanded) 180f else 0f)
                .setDuration(DURATION_MS)
                .setInterpolator(DecelerateInterpolator())
                .start()
        } else if (chevron != null) {
            chevron.rotation = if (expanded) 180f else 0f
        }
    }

    private fun surroundingSectionsRoot(container: ViewGroup): ViewGroup {
        var current: View = container
        var parent = current.parent as? ViewGroup
        while (parent != null) {
            if (parent.childCount > 1) return parent
            current = parent
            parent = current.parent as? ViewGroup
        }
        return container
    }
}
