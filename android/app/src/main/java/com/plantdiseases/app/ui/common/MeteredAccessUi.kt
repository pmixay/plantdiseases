package com.plantdiseases.app.ui.common

import android.content.Context
import androidx.annotation.StringRes
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.billing.ProUi
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.ui.pro.ProActivity
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

/**
 * Shared, bilingual dialog for server-enforced feature budgets. It offers PRO
 * only while [ProUi.ENABLED]; otherwise the wording is plan-neutral.
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
        val builder = MaterialAlertDialogBuilder(context)
            .setTitle(policy.title)
            .setMessage(dialogMessage(context, policy))
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
        }
        builder.show()
        return true
    }

    private fun dialogMessage(context: Context, policy: MeteredDialogPolicy): CharSequence {
        val details = buildList {
            policy.remaining?.let { remaining ->
                add(context.getString(R.string.metered_quota_remaining_detail, remaining))
            }
            policy.resetAt?.let { rawReset ->
                val locale = context.resources.configuration.locales[0]
                val formatted = formatMeteredResetAt(rawReset, locale, ZoneId.systemDefault())
                add(
                    formatted?.let {
                        context.getString(R.string.metered_quota_reset_detail, it)
                    } ?: context.getString(R.string.metered_quota_reset_unknown_detail),
                )
            }
        }
        return buildList {
            add(context.getString(policy.message))
            if (details.isNotEmpty()) add(details.joinToString("\n"))
        }.joinToString("\n\n")
    }
}

internal data class MeteredDialogPolicy(
    @StringRes val title: Int,
    @StringRes val message: Int,
    val offerPro: Boolean,
    val remaining: Int?,
    val resetAt: String?,
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
            resetAt = error.resetAt?.trim()?.takeIf(String::isNotEmpty),
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

private fun String?.isProPlan(): Boolean = this?.trim().equals("pro", ignoreCase = true)
