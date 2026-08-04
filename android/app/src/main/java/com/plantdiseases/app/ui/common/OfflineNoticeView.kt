package com.plantdiseases.app.ui.common

import android.content.Context
import android.graphics.Typeface
import android.util.AttributeSet
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import androidx.annotation.StringRes
import androidx.appcompat.widget.AppCompatTextView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.plantdiseases.app.R
import com.plantdiseases.app.util.ConnectivityObserver
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

/**
 * The offline affordance for screens whose feature genuinely stops working
 * without a server: a slim amber strip that appears while the device can't
 * reach us and disappears the moment it can again.
 *
 * It only *states* the situation — no retry button, no offline stand-in for
 * an online-only feature. It is deliberately NOT on screens that keep working
 * offline: the guide is bundled, and screens that fall back to saved data mark
 * that data as possibly out of date inline instead of claiming the whole
 * screen is degraded.
 *
 * Copy is set per screen, in XML (`android:text`) or at runtime via
 * [setMessage] where it depends on device state — the scan camera says
 * something different depending on whether the on-device model is installed.
 *
 * Drop it into a layout (top of the content, or pinned inside the app bar)
 * and call [observe] once the view is bound.
 */
class OfflineNoticeView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : AppCompatTextView(context, attrs, defStyleAttr) {

    init {
        // Only supply the default copy when the layout didn't pick a message.
        if (text.isNullOrEmpty()) setText(R.string.offline_notice_needs_connection)
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

    /** Swap the message when it depends on runtime state. Call before [observe]. */
    fun setMessage(@StringRes message: Int) {
        setText(message)
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
     *
     * Going offline has to *hold* for [SHOW_DELAY_MS] before the strip paints;
     * coming back online hides it at once. `collectLatest` cancels the pending
     * wait as soon as the state changes, which is what makes a transient
     * offline reading invisible rather than a flash of amber. That covers, in
     * one mechanism:
     *  - returning from the background, where the shared flow replays its last
     *    value to this new subscriber a beat before the re-seeded state lands;
     *  - a Wi-Fi→mobile or VPN handover, where the network is briefly nobody's;
     *  - a probe that loses one round-trip on a tunnel waking up.
     */
    internal fun observe(owner: LifecycleOwner, onlineStates: Flow<Boolean>) {
        owner.lifecycleScope.launch {
            owner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                onlineStates.collectLatest { online ->
                    if (online) {
                        animate().cancel()
                        visibility = View.GONE
                    } else {
                        delay(SHOW_DELAY_MS)
                        if (visibility != View.VISIBLE) {
                            alpha = 0f
                            visibility = View.VISIBLE
                            animate().alpha(1f).setDuration(FADE_IN_MS).start()
                        }
                    }
                }
            }
        }
    }

    private companion object {
        /** How long offline must hold before the strip is allowed to paint. */
        const val SHOW_DELAY_MS = 600L
        const val FADE_IN_MS = 160L
    }
}
