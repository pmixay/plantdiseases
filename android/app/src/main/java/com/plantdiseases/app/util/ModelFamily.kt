package com.plantdiseases.app.util

import android.content.Context
import androidx.annotation.DrawableRes
import androidx.annotation.StringRes
import com.plantdiseases.app.R

/**
 * The three model tiers the user can pick on the scan screen.
 *
 * - [AQUA]    runs entirely on-device (TFLite), so it works with no server
 *             connection. Lighter/faster, slightly lower accuracy.
 * - [GAMMA]   the default server model — the balance of cost and accuracy.
 * - [FRONTIER] a high-capacity server model. Its server inference is gated
 *             off by default (no GPU host), so it is usually shown disabled;
 *             the picker only enables it when /api/capabilities reports it.
 *
 * The [id] string is the canonical wire value sent as ``?family=`` and read
 * from ``classes.json -> model_family`` server-side. Keep it in sync with the
 * server's ``KNOWN_FAMILIES``.
 */
enum class ModelFamily(
    val id: String,
    @DrawableRes val iconRes: Int,
    @StringRes val labelRes: Int,
    @StringRes val descriptionRes: Int,
    /** True when the model runs in the app rather than on the server. */
    val onDevice: Boolean,
) {
    AQUA(
        id = "aqua",
        iconRes = R.drawable.ic_family_aqua,
        labelRes = R.string.model_family_aqua,
        descriptionRes = R.string.model_family_aqua_desc,
        onDevice = true,
    ),
    GAMMA(
        id = "gamma",
        iconRes = R.drawable.ic_family_gamma,
        labelRes = R.string.model_family_gamma,
        descriptionRes = R.string.model_family_gamma_desc,
        onDevice = false,
    ),
    FRONTIER(
        id = "frontier",
        iconRes = R.drawable.ic_family_frontier,
        labelRes = R.string.model_family_frontier,
        descriptionRes = R.string.model_family_frontier_desc,
        onDevice = false,
    );

    companion object {
        val DEFAULT = GAMMA

        fun fromId(id: String?): ModelFamily =
            entries.firstOrNull { it.id.equals(id, ignoreCase = true) } ?: DEFAULT
    }
}

/**
 * Persists the user's selected model family + the last-known server
 * capability flags (so the picker can render disabled states even offline,
 * and Aqua stays selectable without a network round-trip).
 */
object ModelFamilyPrefs {
    private const val PREFS_NAME = "plantdiseases_model"
    private const val KEY_SELECTED = "selected_family"
    private const val KEY_FRONTIER_AVAILABLE = "frontier_available"

    fun selected(context: Context): ModelFamily {
        val chosen = ModelFamily.fromId(prefs(context).getString(KEY_SELECTED, null))
        // Never hand back a family that can't actually serve. Frontier needs
        // the server to enable it; Aqua needs its on-device model downloaded.
        // Fall back to the default so a scan can't silently fail.
        if (chosen == ModelFamily.FRONTIER && !frontierAvailable(context)) {
            return ModelFamily.DEFAULT
        }
        if (chosen == ModelFamily.AQUA && !AquaModelManager.isInstalled(context)) {
            return ModelFamily.DEFAULT
        }
        return chosen
    }

    fun setSelected(context: Context, family: ModelFamily) {
        prefs(context).edit().putString(KEY_SELECTED, family.id).apply()
    }

    /** Last-known frontier availability from /api/capabilities. Defaults to
     *  false so a never-synced client keeps frontier disabled. */
    fun frontierAvailable(context: Context): Boolean =
        prefs(context).getBoolean(KEY_FRONTIER_AVAILABLE, false)

    fun setFrontierAvailable(context: Context, available: Boolean) {
        prefs(context).edit().putBoolean(KEY_FRONTIER_AVAILABLE, available).apply()
    }

    private fun prefs(context: Context) = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
