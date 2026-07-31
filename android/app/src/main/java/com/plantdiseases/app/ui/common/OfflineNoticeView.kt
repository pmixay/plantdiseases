package com.plantdiseases.app.ui.common

import android.content.Context
import android.graphics.Typeface
import android.util.AttributeSet
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import androidx.appcompat.widget.AppCompatTextView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.plantdiseases.app.R
import com.plantdiseases.app.util.ConnectivityObserver
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.launch

/**
 * The one offline affordance every network-backed screen shows: a slim amber
 * strip that appears while the device can't reach the server and disappears the
 * moment it can again.
 *
 * It only *states* the situation — there's no retry button and no offline
 * substitute for an online-only feature. The default copy says the screen is
 * showing cached content; screens with nothing cached to fall back on (symptom
 * checker, identify) override it in XML with
 * `android:text="@string/offline_notice_needs_connection"`.
 *
 * Drop it into a layout (top of the content, or pinned inside the app bar) and
 * call [observe] once the view is bound.
 */
class OfflineNoticeView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : AppCompatTextView(context, attrs, defStyleAttr) {

    init {
        // Only supply the default copy when the layout didn't pick a message.
        if (text.isNullOrEmpty()) setText(R.string.offline_notice_cached)
        gravity = Gravity.CENTER
        setBackgroundColor(context.getColor(R.color.warning_amber_bg))
        setTextColor(context.getColor(R.color.warning_amber))
        setTextSize(TypedValue.COMPLEX_UNIT_PX, resources.getDimension(R.dimen.text_body_sm))
        typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        val horizontal = resources.getDimensionPixelSize(R.dimen.content_gutter)
        val vertical = resources.getDimensionPixelSize(R.dimen.space_sm)
        setPadding(horizontal, vertical, horizontal, vertical)
        compoundDrawablePadding = resources.getDimensionPixelSize(R.dimen.space_sm)
        // ic_warning already carries the (day/night aware) amber tint.
        setCompoundDrawablesRelativeWithIntrinsicBounds(R.drawable.ic_warning, 0, 0, 0)
        // Appearing/disappearing mid-screen is a state change TalkBack should
        // read out, not something the user has to go hunting for.
        accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        // Hidden until the observer says otherwise — an unknown state must not
        // flash an offline claim on a perfectly online screen.
        visibility = View.GONE
    }

    /**
     * Show this notice while [connectivity] reports offline, hide it while
     * online. Collection is scoped to [owner]'s STARTED window, so an
     * offscreen screen (a background pager page, a paused activity) holds no
     * network callback.
     */
    fun observe(owner: LifecycleOwner, connectivity: ConnectivityObserver) {
        observe(owner, connectivity.isOnline)
    }

    /**
     * Flow-based seam kept internal so the visibility contract can be tested
     * independently of Android's ConnectivityManager.
     */
    internal fun observe(owner: LifecycleOwner, onlineStates: Flow<Boolean>) {
        owner.lifecycleScope.launch {
            owner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                onlineStates.collect { online ->
                    visibility = if (online) View.GONE else View.VISIBLE
                }
            }
        }
    }
}
