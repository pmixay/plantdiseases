package com.plantdiseases.app.ui.community.forum

import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ForumAnswer
import com.plantdiseases.app.data.model.ForumPost
import com.plantdiseases.app.data.model.ForumSpace
import com.plantdiseases.app.data.model.ModAnswerActionRequest
import com.plantdiseases.app.data.model.ModPostActionRequest
import com.plantdiseases.app.data.model.ModSanctionRequest
import com.plantdiseases.app.data.model.StaffMe
import com.plantdiseases.app.data.repository.ForumRepository
import kotlinx.coroutines.launch

/**
 * Staff tools for one screen: which actions a role may take, the reason and
 * duration pickers, and the calls. Every action needs a reason code, which the
 * member it affects sees together with an Appeal button. The server checks
 * every role again; this only decides what to offer.
 */
class ModerationController(
    private val activity: AppCompatActivity,
    private val forum: ForumRepository,
    private val onDone: () -> Unit,
) {

    fun postMenu(post: ForumPost, staff: StaffMe, spaces: List<ForumSpace>) {
        val options = mutableListOf<Pair<Int, () -> Unit>>()
        if (staff.isModerator) {
            options += R.string.mod_remove_post to {
                withReason { reason -> postAction(post.id, "remove", reason) }
            }
            options += (if (post.locked) R.string.mod_unlock else R.string.mod_lock) to {
                postAction(post.id, if (post.locked) "unlock" else "lock", null)
            }
            options += (if (post.pinned) R.string.mod_unpin else R.string.mod_pin) to {
                postAction(post.id, if (post.pinned) "unpin" else "pin", null)
            }
            options += R.string.mod_move to { pickSpace(post, spaces) }
            post.author.authorToken?.let { token ->
                options += R.string.mod_sanction_author to { sanctionMenu(token, staff) }
            }
        }
        if (staff.isAdmin) {
            options += R.string.mod_exclude_training to { excludeFromTraining(post.id) }
        }
        show(R.string.forum_moderation_tools, options)
    }

    fun answerMenu(answer: ForumAnswer, staff: StaffMe) {
        val options = mutableListOf<Pair<Int, () -> Unit>>()
        if (staff.isModerator && !answer.isAi) {
            options += R.string.mod_remove_answer to {
                withReason { reason -> answerAction(answer.id, "remove", reason) }
            }
        }
        if (staff.isExpert && !answer.isAi && answer.diagnosis != null && answer.author?.isMe != true) {
            options += (if (answer.verified) R.string.mod_unverify else R.string.mod_verify) to {
                execute { forum.verifyAnswer(answer.id, !answer.verified) }
            }
        }
        if (staff.isModerator) {
            answer.author?.authorToken?.let { token ->
                options += R.string.mod_sanction_author to { sanctionMenu(token, staff) }
            }
        }
        show(R.string.forum_moderation_tools, options)
    }

    fun sanctionMenu(authorToken: String, staff: StaffMe) {
        val options = mutableListOf<Pair<Int, () -> Unit>>(
            R.string.mod_warn to { withReason { sanction(authorToken, "warn", it, null) } },
            R.string.mod_mute_day to { withReason { sanction(authorToken, "mute", it, HOURS_DAY) } },
            R.string.mod_mute_week to { withReason { sanction(authorToken, "mute", it, HOURS_WEEK) } },
            R.string.mod_unmute to { sanction(authorToken, "unmute", null, null) },
        )
        if (staff.isAdmin) {
            options += R.string.mod_ban to { withReason { sanction(authorToken, "ban", it, null) } }
            options += R.string.mod_unban to { sanction(authorToken, "unban", null, null) }
        }
        show(R.string.mod_sanction_author, options)
    }

    fun restore(targetType: String, targetId: String) {
        if (targetType == "answer") answerAction(targetId, "restore", null) else postAction(targetId, "restore", null)
    }

    fun keep(targetType: String, targetId: String) {
        execute { forum.dismissReports(targetType, targetId) }
    }

    fun remove(targetType: String, targetId: String) {
        withReason { reason ->
            if (targetType == "answer") answerAction(targetId, "remove", reason) else postAction(targetId, "remove", reason)
        }
    }

    fun clearRewardFreeze(authorToken: String) {
        MaterialAlertDialogBuilder(activity)
            .setTitle(R.string.mod_rewards_review)
            .setMessage(R.string.mod_rewards_review_message)
            .setPositiveButton(R.string.mod_rewards_clear) { _, _ ->
                execute { forum.setRewardFreeze(authorToken, frozen = false) }
            }
            .setNegativeButton(R.string.mod_rewards_keep, null)
            .show()
    }

    fun resolveAppeal(appealId: String) {
        MaterialAlertDialogBuilder(activity)
            .setTitle(R.string.mod_appeal_decide)
            .setPositiveButton(R.string.mod_appeal_overturn) { _, _ ->
                execute { forum.resolveAppeal(appealId, overturn = true) }
            }
            .setNegativeButton(R.string.mod_appeal_uphold) { _, _ ->
                execute { forum.resolveAppeal(appealId, overturn = false) }
            }
            .setNeutralButton(R.string.forum_cancel, null)
            .show()
    }

    // ── internals ─────────────────────────────────────────────────────────

    private fun show(title: Int, options: List<Pair<Int, () -> Unit>>) {
        if (options.isEmpty()) return
        MaterialAlertDialogBuilder(activity)
            .setTitle(title)
            .setItems(options.map { activity.getString(it.first) }.toTypedArray()) { _, which ->
                options[which].second()
            }
            .setNegativeButton(R.string.forum_cancel, null)
            .show()
    }

    private fun withReason(onPicked: (String) -> Unit) {
        val labels = ForumUi.MOD_REASONS.map { activity.getString(it.second) }.toTypedArray()
        MaterialAlertDialogBuilder(activity)
            .setTitle(R.string.mod_pick_reason)
            .setItems(labels) { _, which -> onPicked(ForumUi.MOD_REASONS[which].first) }
            .setNegativeButton(R.string.forum_cancel, null)
            .show()
    }

    private fun pickSpace(post: ForumPost, spaces: List<ForumSpace>) {
        val choices = spaces.filter { it.slug != post.space }
        if (choices.isEmpty()) return
        val labels = choices.map { ForumUi.spaceName(activity, it.slug, spaces) }.toTypedArray()
        MaterialAlertDialogBuilder(activity)
            .setTitle(R.string.mod_move)
            .setItems(labels) { _, which ->
                withReason { reason -> postAction(post.id, "move", reason, choices[which].slug) }
            }
            .setNegativeButton(R.string.forum_cancel, null)
            .show()
    }

    private fun postAction(postId: String, action: String, reason: String?, space: String? = null) {
        execute { forum.moderatePost(postId, ModPostActionRequest(action, reason, null, space)) }
    }

    private fun answerAction(answerId: String, action: String, reason: String?) {
        execute { forum.moderateAnswer(answerId, ModAnswerActionRequest(action, reason, null)) }
    }

    private fun sanction(authorToken: String, kind: String, reason: String?, hours: Int?) {
        execute { forum.sanction(authorToken, ModSanctionRequest(kind, reason, hours, null)) }
    }

    private fun excludeFromTraining(postId: String) {
        MaterialAlertDialogBuilder(activity)
            .setTitle(R.string.mod_exclude_training)
            .setMessage(R.string.mod_exclude_training_message)
            .setPositiveButton(R.string.mod_exclude_confirm) { _, _ ->
                execute { forum.excludeFromTraining(postId) }
            }
            .setNegativeButton(R.string.forum_cancel, null)
            .show()
    }

    private fun execute(call: suspend () -> Result<*>) {
        activity.lifecycleScope.launch {
            call()
                .onSuccess {
                    Toast.makeText(activity, R.string.mod_done, Toast.LENGTH_SHORT).show()
                    onDone()
                }
                .onFailure { error ->
                    Toast.makeText(activity, ForumUi.errorMessage(activity, error), Toast.LENGTH_LONG).show()
                }
        }
    }

    private companion object {
        const val HOURS_DAY = 24
        const val HOURS_WEEK = 168
    }
}
