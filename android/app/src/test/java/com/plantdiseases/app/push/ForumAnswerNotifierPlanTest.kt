package com.plantdiseases.app.push

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import com.plantdiseases.app.data.repository.ForumNotificationSync
import org.junit.Test

/** Which answer notifications pop up, and the limits that keep them from spamming. */
class ForumAnswerNotifierPlanTest {

    private val now = 1_000_000L

    private fun answer(post: String, at: Long, type: String = ForumNotificationSync.TYPE_ANSWER) =
        ForumNotificationSync.Event(
            type = type,
            title = R.string.forum_notif_answer_title,
            deeplink = ForumNotificationSync.postLink(post, "a$at"),
            createdAt = at,
            postId = post,
            answerId = "a$at",
            postTitle = "Pyrus",
        ) { "" }

    private fun decide(events: List<ForumNotificationSync.Event>, recent: List<Long> = emptyList(), first: Boolean = false) =
        ForumAnswerNotifier.Plan.decide(ForumNotificationSync.Imported(events, first), now, recent, enabled = true)

    @Test
    fun answers_on_one_post_share_one_notification_pointing_at_the_newest() {
        val decision = decide(listOf(answer("p1", now - 30), answer("p1", now - 10), answer("p1", now - 20)))

        assertThat(decision.alerts).hasSize(1)
        assertThat(decision.alerts.single().count).isEqualTo(3)
        assertThat(decision.alerts.single().newest.answerId).isEqualTo("a${now - 10}")
    }

    @Test
    fun at_most_three_alerts_an_hour_and_the_rest_fold_into_a_summary() {
        val events = (1..5).map { answer("p$it", now - it) }
        val earlier = listOf(now - 600L, now - 7_200L) // one inside the hour, one outside

        val decision = decide(events, recent = earlier)

        assertThat(decision.alerts.map { it.postId }).containsExactly("p1", "p2").inOrder()
        assertThat(decision.folded).isEqualTo(3)
    }

    @Test
    fun history_old_events_moderation_and_a_switched_off_user_never_pop_up() {
        val stale = answer("p1", now - ForumAnswerNotifier.FRESH_FOR_S - 1)
        val decisionEvent = ForumNotificationSync.Event(
            type = ForumNotificationSync.TYPE_DECISION,
            title = R.string.forum_decision_title,
            deeplink = "forum_decision:m1",
            createdAt = now,
        ) { "" }

        assertThat(decide(listOf(answer("p2", now)), first = true).alerts).isEmpty()
        assertThat(decide(listOf(stale, decisionEvent)).alerts).isEmpty()
        val off = ForumAnswerNotifier.Plan.decide(
            ForumNotificationSync.Imported(listOf(answer("p3", now)), firstImport = false),
            now,
            emptyList(),
            enabled = false,
        )
        assertThat(off.alerts).isEmpty()
        assertThat(off.folded).isEqualTo(0)
    }

    @Test
    fun accepted_answers_notify_too() {
        val decision = decide(listOf(answer("p1", now, ForumNotificationSync.TYPE_ACCEPTED)))
        assertThat(decision.alerts).hasSize(1)
    }
}
