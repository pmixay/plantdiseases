package com.plantdiseases.app.util

import android.content.Context
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.widget.FrameLayout
import androidx.viewpager2.widget.ViewPager2
import kotlin.math.absoluteValue
import kotlin.math.sign

/**
 * Wrapper for a scrollable view that scrolls the same way as its parent
 * [ViewPager2] — on Home, the horizontal carousels sitting on the shell's
 * horizontal page axis.
 *
 * Without this, the two fight: a drag starting on a carousel is claimed by
 * whichever view sees it first, so either the carousel never scrolls or the
 * page never turns once the carousel is at its end. This routes each gesture to
 * exactly one of them — the child keeps a parallel drag while it still has room
 * to scroll that way, and hands it to the pager the moment it doesn't.
 * Perpendicular drags always go to the parents so the page can still scroll
 * vertically through a carousel.
 *
 * The scrollable view must be the immediate and only child. Adapted from the
 * ViewPager2 sample of the same name (AOSP, Apache 2.0).
 */
class NestedScrollableHost @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : FrameLayout(context, attrs) {

    private val touchSlop = ViewConfiguration.get(context).scaledTouchSlop
    private var initialX = 0f
    private var initialY = 0f

    private val parentViewPager: ViewPager2?
        get() {
            var v: View? = parent as? View
            while (v != null && v !is ViewPager2) v = v.parent as? View
            return v as? ViewPager2
        }

    private val child: View? get() = if (childCount > 0) getChildAt(0) else null

    private fun canChildScroll(orientation: Int, delta: Float): Boolean {
        val direction = -delta.sign.toInt()
        return when (orientation) {
            ViewPager2.ORIENTATION_HORIZONTAL -> child?.canScrollHorizontally(direction) ?: false
            ViewPager2.ORIENTATION_VERTICAL -> child?.canScrollVertically(direction) ?: false
            else -> throw IllegalArgumentException("Unknown orientation $orientation")
        }
    }

    override fun onInterceptTouchEvent(e: MotionEvent): Boolean {
        handleInterceptTouchEvent(e)
        return super.onInterceptTouchEvent(e)
    }

    private fun handleInterceptTouchEvent(e: MotionEvent) {
        val orientation = parentViewPager?.orientation ?: return

        // Nothing to arbitrate if the child can't scroll the pager's way at all.
        if (!canChildScroll(orientation, -1f) && !canChildScroll(orientation, 1f)) return

        if (e.action == MotionEvent.ACTION_DOWN) {
            initialX = e.x
            initialY = e.y
            // Claim the gesture until ACTION_MOVE says which way it's going.
            parent.requestDisallowInterceptTouchEvent(true)
        } else if (e.action == MotionEvent.ACTION_MOVE) {
            val dx = e.x - initialX
            val dy = e.y - initialY
            val isVpHorizontal = orientation == ViewPager2.ORIENTATION_HORIZONTAL

            // ViewPager2's touch slop is 2x the child's, so scale the axis it
            // owns to compare the two on even terms.
            val scaledDx = dx.absoluteValue * if (isVpHorizontal) .5f else 1f
            val scaledDy = dy.absoluteValue * if (isVpHorizontal) 1f else .5f

            if (scaledDx > touchSlop || scaledDy > touchSlop) {
                if (isVpHorizontal == (scaledDy > scaledDx)) {
                    // Perpendicular to the pager — let the parents have it.
                    parent.requestDisallowInterceptTouchEvent(false)
                } else {
                    // Parallel: the child keeps it only while it can still
                    // scroll that way, otherwise the pager turns the page.
                    parent.requestDisallowInterceptTouchEvent(
                        canChildScroll(orientation, if (isVpHorizontal) dx else dy),
                    )
                }
            }
        }
    }
}
