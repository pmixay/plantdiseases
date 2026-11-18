package com.plantdiseases.app.ui.community.forum

import android.app.Activity
import android.view.View
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding

/**
 * Edge-to-edge for the forum screens: the top bar's gradient runs under the
 * status bar, and the content (including the answer composer) stays above the
 * navigation bar and the keyboard.
 */
object ForumInsets {
    fun apply(activity: Activity, topBar: View, content: View) {
        WindowCompat.setDecorFitsSystemWindows(activity.window, false)
        val topStart = topBar.paddingTop
        val bottomStart = content.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(topBar) { view, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout(),
            )
            view.updatePadding(top = topStart + bars.top, left = bars.left, right = bars.right)
            insets
        }
        ViewCompat.setOnApplyWindowInsetsListener(content) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            view.updatePadding(bottom = bottomStart + maxOf(bars.bottom, ime.bottom))
            insets
        }
    }
}
