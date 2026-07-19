package com.plantdiseases.app.ui.common

import android.content.Context
import android.util.AttributeSet
import androidx.appcompat.widget.AppCompatTextView
import com.plantdiseases.app.util.ConnectivityMonitor
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * A banner TextView that shows itself while the device is offline and hides
 * itself the moment connectivity comes back — no wiring required at the call
 * site beyond placing it in a layout.
 *
 * Drives itself from [ConnectivityMonitor] (the app-wide signal) while
 * attached to a window, so every placement reacts to the same state at the
 * same moment. Text/colour stay in the layout XML: the shell banner says
 * "offline — showing saved content" while the Scan overlay keeps its own
 * copy, but both flip together.
 */
class OfflineBannerView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = android.R.attr.textViewStyle,
) : AppCompatTextView(context, attrs, defStyleAttr) {

    /** Screen-level gate: the host can park the banner while another surface
     *  already communicates offline state (MainActivity suppresses the shell
     *  banner on the Scan tab, which shows its own on-preview overlay). */
    var suppressed: Boolean = false
        set(value) {
            field = value
            render(ConnectivityMonitor.isOnline.value)
        }

    private var scope: CoroutineScope? = null

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (isInEditMode) return
        ConnectivityMonitor.start(context.applicationContext)
        // Own scope rather than a lifecycleScope: the view outlives fragment
        // view lifecycles inside ViewPager2 pages, and attach/detach is the
        // exact window in which it can render anyway.
        scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate).also { sc ->
            sc.launch { ConnectivityMonitor.isOnline.collect { render(it) } }
        }
    }

    override fun onDetachedFromWindow() {
        scope?.cancel()
        scope = null
        super.onDetachedFromWindow()
    }

    private fun render(online: Boolean) {
        visibility = if (!online && !suppressed) VISIBLE else GONE
    }
}
