package com.plantdiseases.app.ui.community.forum

import android.content.Context
import android.text.SpannableString
import android.text.Spanned
import android.text.format.DateUtils
import android.text.style.BackgroundColorSpan
import androidx.annotation.ColorInt
import androidx.annotation.DrawableRes
import androidx.annotation.StringRes
import com.plantdiseases.app.R
import com.plantdiseases.app.auth.AuthenticationRequiredException
import com.plantdiseases.app.data.account.AccountApiException
import com.plantdiseases.app.data.model.ForumSpace
import com.plantdiseases.app.data.model.ModNotice
import com.plantdiseases.app.data.repository.ForumRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.LocaleHelper

/** Shared labels, icons and formatting for the community forum screens. */
object ForumUi {

    /** Diagnosis tags offered on answers, in display order. Matches the
     *  server's accepted labels (head classes plus out-of-head ones). */
    val DIAGNOSIS_KEYS = listOf(
        "pests",
        "powdery_mildew",
        "gray_mold_botrytis",
        "leaf_spot",
        "leaf_scorch_underwatering",
        "nutrient_deficiency",
        "viral_disease",
        "edema",
        "sunburn_or_scorch",
        "healthy",
        "not_a_plant",
        "other",
    )

    /** Fixed report reasons (no free text is ever sent). */
    val REPORT_REASONS = listOf(
        "spam_or_misleading" to R.string.forum_report_spam,
        "hate_or_harassment" to R.string.forum_report_harassment,
        "violence_or_threats" to R.string.forum_report_violence,
        "sexual_content" to R.string.forum_report_sexual,
        "illegal_or_dangerous" to R.string.forum_report_dangerous,
        "privacy_or_impersonation" to R.string.forum_report_privacy,
        "other_rules_violation" to R.string.forum_report_other,
    )

    /** Moderator reason codes, shown to the member the action affects. */
    val MOD_REASONS = listOf(
        "spam" to R.string.mod_reason_spam,
        "harassment" to R.string.mod_reason_harassment,
        "dangerous_advice" to R.string.mod_reason_dangerous_advice,
        "off_topic" to R.string.mod_reason_off_topic,
        "inappropriate_image" to R.string.mod_reason_inappropriate_image,
        "privacy" to R.string.mod_reason_privacy,
        "duplicate" to R.string.mod_reason_duplicate,
        "other_rules" to R.string.mod_reason_other,
    )

    /** "Post removed · Spam": what a moderator did and why. */
    fun modNoticeHeading(context: Context, notice: ModNotice): String {
        val reason = MOD_REASONS.firstOrNull { it.first == notice.reasonCode }?.second ?: R.string.mod_reason_reports
        return context.getString(R.string.mod_notice_heading, modActionText(context, notice), context.getString(reason))
    }

    private fun modActionText(context: Context, notice: ModNotice): String = context.getString(
        when (notice.action) {
            "remove" -> if (notice.targetType == "answer") R.string.mod_notice_answer_removed else R.string.mod_notice_post_removed
            "hide_auto" -> R.string.mod_notice_hidden
            "warn" -> R.string.mod_notice_warned
            "mute" -> R.string.mod_notice_muted
            "ban" -> R.string.mod_notice_ban
            "lock" -> R.string.mod_notice_locked
            "move" -> R.string.mod_notice_moved
            else -> R.string.mod_action_other
        },
    )

    fun isRu(context: Context): Boolean = LocaleHelper.isRussian(context)

    fun conditionLabel(context: Context, key: String?): String {
        if (key.isNullOrBlank()) return ""
        return when (key) {
            "other" -> context.getString(R.string.forum_label_other)
            "is_a_plant" -> context.getString(R.string.forum_label_is_a_plant)
            else -> DiseaseLabels.displayName(key, isRu(context)) ?: key.replace('_', ' ')
        }
    }

    fun spaceName(context: Context, slug: String, spaces: List<ForumSpace>): String {
        val space = spaces.firstOrNull { it.slug == slug }
        return when {
            space == null -> slug.replace('_', ' ').replaceFirstChar { it.uppercase() }
            isRu(context) -> space.nameRu.ifBlank { space.nameEn }
            else -> space.nameEn
        }
    }

    @DrawableRes
    fun spaceIcon(icon: String): Int = when (icon) {
        "bug" -> R.drawable.ic_pest
        "spores" -> R.drawable.ic_disease_powdery_mildew
        "flower" -> R.drawable.ic_healthy
        "sprout", "succulent", "fern", "palm" -> R.drawable.ic_garden
        else -> R.drawable.ic_leaf
    }

    @StringRes
    fun kindLabel(kind: String): Int = when (kind) {
        "scan" -> R.string.forum_kind_scan
        "recovery" -> R.string.forum_kind_recovery
        "tip" -> R.string.forum_kind_tip
        else -> R.string.forum_kind_question
    }

    fun relativeTime(epochSeconds: Long): CharSequence = DateUtils.getRelativeTimeSpanString(
        epochSeconds * 1000L,
        System.currentTimeMillis(),
        DateUtils.MINUTE_IN_MILLIS,
        DateUtils.FORMAT_ABBREV_RELATIVE,
    )

    /** Title shown for a post; scan posts may have none. */
    fun postTitle(context: Context, title: String, kind: String, aiDisease: String?): String = when {
        title.isNotBlank() -> title
        kind == "scan" && !aiDisease.isNullOrBlank() ->
            context.getString(R.string.forum_scan_title_fallback, conditionLabel(context, aiDisease))
        else -> context.getString(kindLabel(kind))
    }

    /** A localized sentence for any failure a forum call can produce. */
    fun errorMessage(context: Context, error: Throwable): String {
        val res = when (error) {
            is AuthenticationRequiredException -> R.string.community_sign_in_required
            is AccountApiException -> R.string.community_sign_in_required
            is ScanRepository.NoNetworkException -> R.string.forum_error_offline
            is ScanRepository.ServerTimeoutException -> R.string.forum_error_offline
            is ForumRepository.ForumException -> errorRes(error.code)
            else -> R.string.forum_error_generic
        }
        return context.getString(res)
    }

    @StringRes
    private fun errorRes(code: String): Int = when (code) {
        "muted" -> R.string.forum_error_muted
        "banned" -> R.string.forum_error_banned
        "level_required" -> R.string.forum_error_level_required
        "links_not_allowed_yet" -> R.string.forum_error_links
        "blocked_terms" -> R.string.forum_error_blocked_terms
        "image_not_allowed", "invalid_image" -> R.string.forum_error_image
        "daily_quota_reached", "report_limit" -> R.string.forum_error_quota
        "post_locked" -> R.string.forum_error_locked
        "post_not_found", "answer_not_found" -> R.string.forum_error_not_found
        "blocked_interaction" -> R.string.forum_error_blocked
        "self_like", "self_reaction", "self_accept", "self_target" -> R.string.forum_error_self
        "title_required" -> R.string.forum_error_title_required
        "body_required", "body_or_photo_required" -> R.string.forum_error_body_required
        "scan_already_posted" -> R.string.forum_error_scan_posted
        "scan_unknown", "scan_image_mismatch", "scan_taken", "scan_photo_required" -> R.string.forum_error_scan
        "account_deleted" -> R.string.forum_error_account_deleted
        "forbidden", "admin_required", "global_moderator_required" -> R.string.forum_error_forbidden
        "roles_unavailable" -> R.string.forum_error_roles_unavailable
        "already_appealed" -> R.string.forum_error_already_appealed
        "same_moderator" -> R.string.forum_error_same_moderator
        else -> R.string.forum_error_generic
    }

    /** Background-highlight every occurrence of each search term (case-insensitive). */
    fun highlight(text: String, terms: List<String>, @ColorInt color: Int): CharSequence {
        if (terms.isEmpty() || text.isEmpty()) return text
        val spannable = SpannableString(text)
        val lower = text.lowercase()
        for (term in terms) {
            if (term.length < 2) continue
            var index = lower.indexOf(term.lowercase())
            while (index >= 0) {
                spannable.setSpan(
                    BackgroundColorSpan(color),
                    index,
                    index + term.length,
                    Spanned.SPAN_EXCLUSIVE_EXCLUSIVE,
                )
                index = lower.indexOf(term.lowercase(), index + term.length)
            }
        }
        return spannable
    }

    @StringRes
    fun levelName(level: Int): Int = when (level) {
        2 -> R.string.forum_level_sprout
        3 -> R.string.forum_level_gardener
        4 -> R.string.forum_level_botanist
        5 -> R.string.forum_level_plant_doctor
        else -> R.string.forum_level_seedling
    }

    @StringRes
    fun achievementTitle(key: String): Int = when (key) {
        "first_aid" -> R.string.ach_first_aid
        "lifeline" -> R.string.ach_lifeline
        "rescue_ranger" -> R.string.ach_rescue_ranger
        "pest_control" -> R.string.ach_pest_control
        "fungus_detective" -> R.string.ach_fungus_detective
        "water_whisperer" -> R.string.ach_water_whisperer
        "nutrient_nerd" -> R.string.ach_nutrient_nerd
        "virus_hunter" -> R.string.ach_virus_hunter
        "model_teacher" -> R.string.ach_model_teacher
        "bilingual" -> R.string.ach_bilingual
        "welcome_wagon" -> R.string.ach_welcome_wagon
        "good_question" -> R.string.ach_good_question
        "closing_the_loop" -> R.string.ach_closing_the_loop
        "photo_journal" -> R.string.ach_photo_journal
        else -> R.string.ach_unknown
    }

    @StringRes
    fun achievementDescription(key: String): Int = when (key) {
        "first_aid" -> R.string.ach_first_aid_desc
        "lifeline" -> R.string.ach_lifeline_desc
        "rescue_ranger" -> R.string.ach_rescue_ranger_desc
        "pest_control" -> R.string.ach_pest_control_desc
        "fungus_detective" -> R.string.ach_fungus_detective_desc
        "water_whisperer" -> R.string.ach_water_whisperer_desc
        "nutrient_nerd" -> R.string.ach_nutrient_nerd_desc
        "virus_hunter" -> R.string.ach_virus_hunter_desc
        "model_teacher" -> R.string.ach_model_teacher_desc
        "bilingual" -> R.string.ach_bilingual_desc
        "welcome_wagon" -> R.string.ach_welcome_wagon_desc
        "good_question" -> R.string.ach_good_question_desc
        "closing_the_loop" -> R.string.ach_closing_the_loop_desc
        "photo_journal" -> R.string.ach_photo_journal_desc
        else -> R.string.ach_unknown
    }

    @DrawableRes
    fun achievementIcon(key: String): Int = when (key) {
        "first_aid" -> R.drawable.ic_first_aid
        "lifeline" -> R.drawable.ic_clock
        "rescue_ranger" -> R.drawable.ic_healthy
        "pest_control" -> R.drawable.ic_pest
        "fungus_detective" -> R.drawable.ic_disease_powdery_mildew
        "water_whisperer" -> R.drawable.ic_watering
        "nutrient_nerd" -> R.drawable.ic_fertilize
        "virus_hunter" -> R.drawable.ic_disease_viral
        "model_teacher" -> R.drawable.ic_sparkle
        "bilingual" -> R.drawable.ic_translate
        "welcome_wagon" -> R.drawable.ic_community
        "good_question" -> R.drawable.ic_question
        "closing_the_loop" -> R.drawable.ic_check
        "photo_journal" -> R.drawable.ic_gallery
        else -> R.drawable.ic_trophy
    }
}
