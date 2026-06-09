package com.plantdiseases.app.util

import android.graphics.Rect
import android.view.TouchDelegate
import android.view.View

/**
 * Expand this view's *touchable* area to at least [minSizeDp] in each
 * dimension WITHOUT changing its visible size, by installing a
 * [TouchDelegate] on its immediate parent. Use for small icon buttons that
 * would otherwise fall below the 48dp accessibility minimum.
 *
 * Caveats:
 *  - A parent hosts a single TouchDelegate, so only delegate one child per
 *    parent (our call sites each have a single small control in their row).
 *  - The delegate can only capture touches the parent itself receives, so the
 *    parent must be at least [minSizeDp] tall/wide for the full expansion to
 *    take effect; otherwise it expands as far as the parent allows.
 */
fun View.expandTouchTarget(minSizeDp: Int = 48) {
    val parentView = this.parent as? View ?: return
    parentView.post {
        if (!isAttachedToWindow) return@post
        val rect = Rect()
        getHitRect(rect)
        val minPx = (minSizeDp * resources.displayMetrics.density).toInt()
        val extraW = ((minPx - rect.width()) / 2).coerceAtLeast(0)
        val extraH = ((minPx - rect.height()) / 2).coerceAtLeast(0)
        if (extraW == 0 && extraH == 0) return@post
        rect.inset(-extraW, -extraH)
        parentView.touchDelegate = TouchDelegate(rect, this)
    }
}
