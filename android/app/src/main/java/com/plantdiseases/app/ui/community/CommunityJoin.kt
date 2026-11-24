package com.plantdiseases.app.ui.community

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.RenderEffect
import android.graphics.Shader
import android.view.LayoutInflater
import android.view.View
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.push.PushManager
import com.plantdiseases.app.util.CommunityPrefs

/**
 * Joining the community. Membership means location access plus sharing: a
 * member's accepted server scans are published to the community (photo,
 * diagnosis, approximate area) while they are signed in. Features that show
 * scans by area (the community map, nearby scans on a result and on a species
 * page, Home's popular-near-you list) require it, so they call [ensureMember], which explains what joining shares, asks for
 * coarse location and turns sharing on. There is no separate sharing switch;
 * leaving (the map's menu) turns it off.
 *
 * Construct it while the activity is being created (a property initializer):
 * it registers the permission launcher.
 */
class CommunityJoin(private val activity: ComponentActivity) {

    private var pending: ((Boolean) -> Unit)? = null

    private val permission = activity.registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        val callback = pending
        pending = null
        if (granted) {
            join(activity)
        } else {
            Toast.makeText(activity, R.string.community_join_needs_location, Toast.LENGTH_LONG).show()
        }
        callback?.invoke(granted)
    }

    /** Calls [onResult] with true once the user is a member (at once when
     *  they already are), false when they decline or deny location. */
    fun ensureMember(onResult: (joined: Boolean) -> Unit) {
        if (isMember(activity)) {
            onResult(true)
            return
        }
        val view = LayoutInflater.from(activity).inflate(R.layout.dialog_metered_limit, null)
        view.findViewById<ImageView>(R.id.iv_metered_icon).setImageResource(R.drawable.ic_map)
        view.findViewById<TextView>(R.id.tv_metered_title).setText(R.string.community_join_title)
        view.findViewById<TextView>(R.id.tv_metered_body).setText(R.string.community_join_body)
        var answered = false
        MaterialAlertDialogBuilder(activity)
            .setView(view)
            .setPositiveButton(R.string.community_join_cta) { _, _ ->
                answered = true
                if (hasLocation(activity)) {
                    join(activity)
                    onResult(true)
                } else {
                    pending = onResult
                    permission.launch(Manifest.permission.ACCESS_COARSE_LOCATION)
                }
            }
            .setNegativeButton(R.string.cancel) { _, _ ->
                answered = true
                onResult(false)
            }
            .setOnDismissListener { if (!answered) onResult(false) }
            .show()
    }

    companion object {
        fun hasLocation(context: Context): Boolean =
            ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_COARSE_LOCATION) ==
                PackageManager.PERMISSION_GRANTED

        /** A member has sharing on and location granted. */
        fun isMember(context: Context): Boolean =
            CommunityPrefs.uploadsEnabled(context) && hasLocation(context)

        fun join(context: Context) {
            CommunityPrefs.enable(context)
            PushManager.registerIfEnabled(context)
        }
    }
}

/**
 * A members-only strip of nearby scans, as non-members see it: [strip] blurred
 * under [joinButton], with no taps, scrolling or screen-reader focus on its
 * cells; [overlay] (a clickable view laid over the strip) takes a tap anywhere
 * on it, so it can ask to join like the button. Members see it plain.
 */
fun lockNearbyPreview(strip: View, overlay: View, joinButton: View, locked: Boolean) {
    val radius = NEARBY_PREVIEW_BLUR_DP * strip.resources.displayMetrics.density
    strip.setRenderEffect(
        if (locked) RenderEffect.createBlurEffect(radius, radius, Shader.TileMode.CLAMP) else null,
    )
    strip.importantForAccessibility = if (locked) {
        View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
    } else {
        View.IMPORTANT_FOR_ACCESSIBILITY_AUTO
    }
    overlay.visibility = if (locked) View.VISIBLE else View.GONE
    joinButton.visibility = if (locked) View.VISIBLE else View.GONE
}

private const val NEARBY_PREVIEW_BLUR_DP = 12f
