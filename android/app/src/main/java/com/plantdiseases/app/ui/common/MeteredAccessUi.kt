package com.plantdiseases.app.ui.common

import android.content.Context
import android.view.LayoutInflater
import android.widget.ImageView
import android.widget.TextView
import androidx.annotation.DrawableRes
import androidx.annotation.StringRes
import androidx.core.view.isVisible
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.R
import com.plantdiseases.app.billing.ProUi
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.ui.account.AccountActivity
import com.plantdiseases.app.ui.pro.ProActivity
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

/**
 * Shared, bilingual dialog for server-enforced feature budgets: an icon, the
 * title, one short line and when the allowance comes back ("Resets in
 * 5 h 12 min, at 03:00"). It never shows a "0 left" count. It offers PRO only
 * while [ProUi.ENABLED]; otherwise the wording is plan-neutral.
 */
object MeteredAccessUi {
    fun handles(error: Throwable): Boolean = meteredDialogPolicy(error) != null

    @StringRes
    fun title(error: Throwable): Int =
        meteredDialogPolicy(error)?.title ?: R.string.metered_quota_title

    @StringRes
    fun message(error: Throwable): Int =
        meteredDialogPolicy(error)?.message ?: R.string.unknown_error

    /** Returns false for unrelated failures so callers can keep existing UX. */
    fun showDialog(context: Context, error: Throwable): Boolean {
        val policy = meteredDialogPolicy(error) ?: return false
        val view = LayoutInflater.from(context).inflate(R.layout.dialog_metered_limit, null)
        view.findViewById<ImageView>(R.id.iv_metered_icon).setImageResource(policy.icon)
        view.findViewById<TextView>(R.id.tv_metered_title).setText(policy.title)
        view.findViewById<TextView>(R.id.tv_metered_body).setText(policy.message)
        val reset = policy.resetAt?.let {
            formatMeteredResetLine(context, it, Instant.now(), ZoneId.systemDefault())
        }
        view.findViewById<TextView>(R.id.tv_metered_reset).text = reset
        view.findViewById<android.view.View>(R.id.metered_reset_row).isVisible = reset != null
        val builder = MaterialAlertDialogBuilder(context).setView(view)
        if (policy.offerPro) {
            builder
                .setPositiveButton(R.string.metered_view_pro) { _, _ ->
                    context.startActivity(ProActivity.intent(context))
                }
                .setNegativeButton(R.string.metered_not_now, null)
        } else {
            // An active PRO user has no higher plan to buy. A standard OK
            // action is truthful and remains localized/accessibility-friendly.
            builder.setPositiveButton(android.R.string.ok, null)
            if (policy.offerSignIn && BuildConfig.ACCOUNT_UI_ENABLED) {
                builder.setNeutralButton(R.string.metered_sign_in) { _, _ ->
                    context.startActivity(AccountActivity.intent(context))
                }
            }
        }
        builder.show()
        return true
    }
}

internal data class MeteredDialogPolicy(
    @StringRes val title: Int,
    @StringRes val message: Int,
    val offerPro: Boolean,
    val remaining: Int?,
    val resetAt: String?,
    @DrawableRes val icon: Int = R.drawable.ic_clock,
    // The option has no allowance without an account: offer signing in.
    val offerSignIn: Boolean = false,
)

internal fun meteredDialogPolicy(
    error: Throwable,
    proOffered: Boolean = ProUi.ENABLED,
): MeteredDialogPolicy? = when {
    proOffered -> proMeteredDialogPolicy(error)
    error is ScanRepository.ProRequiredException ->
        // A zero allowance: today that is a guest asking for an account-only option.
        MeteredDialogPolicy(
            title = R.string.metered_pro_active_unavailable_title,
            message = R.string.metered_unavailable_body,
            offerPro = false,
            remaining = error.remaining?.takeIf { it >= 0 },
            // A zero allowance has nothing to wait for: no reset line.
            resetAt = null,
            icon = R.drawable.ic_lock,
            offerSignIn = true,
        )
    error is ScanRepository.QuotaExhaustedException ->
        MeteredDialogPolicy(
            title = R.string.metered_quota_title,
            message = R.string.metered_quota_body,
            offerPro = false,
            remaining = error.remaining.takeIf { it >= 0 },
            resetAt = error.resetAt.trim().takeIf(String::isNotEmpty),
        )
    else -> null
}

private fun proMeteredDialogPolicy(error: Throwable): MeteredDialogPolicy? = when (error) {
    is ScanRepository.ProRequiredException -> {
        val proPlan = error.plan.isProPlan()
        MeteredDialogPolicy(
            title = if (proPlan) {
                R.string.metered_pro_active_unavailable_title
            } else {
                R.string.metered_pro_required_title
            },
            message = if (proPlan) {
                R.string.metered_pro_required_active_body
            } else {
                R.string.metered_pro_required_body
            },
            offerPro = !proPlan,
            remaining = error.remaining?.takeIf { it >= 0 },
            resetAt = error.resetAt?.trim()?.takeIf(String::isNotEmpty),
        )
    }
    is ScanRepository.QuotaExhaustedException -> {
        val proPlan = error.plan.isProPlan()
        MeteredDialogPolicy(
            title = R.string.metered_quota_title,
            message = if (proPlan) {
                R.string.metered_quota_pro_body
            } else {
                R.string.metered_quota_free_body
            },
            offerPro = !proPlan,
            remaining = error.remaining.takeIf { it >= 0 },
            resetAt = error.resetAt.trim().takeIf(String::isNotEmpty),
        )
    }
    else -> null
}

internal fun formatMeteredResetAt(
    rawResetAt: String,
    locale: Locale,
    zoneId: ZoneId,
): String? {
    val instant = runCatching { Instant.parse(rawResetAt.trim()) }.getOrNull() ?: return null
    return runCatching {
        DateTimeFormatter
            .ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT)
            .withLocale(locale)
            .format(instant.atZone(zoneId))
    }.getOrNull()
}

/** "Resets in 5 h 12 min, at 03:00" for a server reset timestamp; null when
 *  the timestamp is unusable, so the dialog simply leaves the line out. */
internal fun formatMeteredResetLine(
    context: Context,
    rawResetAt: String,
    now: Instant,
    zoneId: ZoneId,
): String? {
    val reset = runCatching { Instant.parse(rawResetAt.trim()) }.getOrNull() ?: return null
    val minutes = Duration.between(now, reset).toMinutes().coerceAtLeast(0)
    val hours = (minutes / 60).toInt()
    val rest = (minutes % 60).toInt()
    val left = when {
        minutes < 1 -> context.getString(R.string.metered_duration_soon)
        hours == 0 -> context.getString(R.string.metered_duration_minutes, rest)
        rest == 0 -> context.getString(R.string.metered_duration_hours, hours)
        else -> context.getString(R.string.metered_duration_hours_minutes, hours, rest)
    }
    val locale = context.resources.configuration.locales[0]
    val at = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(locale).format(reset.atZone(zoneId))
    return context.getString(R.string.metered_reset_in_at, left, at)
}

private fun String?.isProPlan(): Boolean = this?.trim().equals("pro", ignoreCase = true)
