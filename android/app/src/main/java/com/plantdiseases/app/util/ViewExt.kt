package com.plantdiseases.app.util

import android.graphics.Rect
import android.view.TouchDelegate
import android.view.View
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.doOnAttach
import androidx.core.view.updatePadding

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

/**
 * Pad this view's top by the status-bar inset, added to whatever top padding
 * it already has. For a page whose own content has to clear the status bar
 * without a toolbar above it reserving that space — every shell tab now
 * renders its own page content edge-to-edge (see MainActivity.applyTabChrome).
 *
 * onViewCreated runs before the view is attached, so requesting insets right
 * away no-ops and a tab switched to later never gets an inset pass; request
 * once attached instead.
 */
fun View.applyStatusBarTopInset() {
    val baseTop = paddingTop
    ViewCompat.setOnApplyWindowInsetsListener(this) { v, insets ->
        val top = insets.getInsets(WindowInsetsCompat.Type.systemBars()).top
        v.updatePadding(top = baseTop + top)
        insets
    }
    doOnAttach { ViewCompat.requestApplyInsets(it) }
}
