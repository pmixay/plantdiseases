package com.plantdiseases.app.data.repository

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ForumActivityItem
import com.plantdiseases.app.data.model.ModNotice
import org.junit.Test

class ForumNotificationSyncPlanTest {

    private fun answer(type: String, at: Long) =
        ForumActivityItem(type = type, postId = "p$at", postTitle = "Pyrus", nickname = "Ann", createdAt = at)

    private fun decision(at: Long) = ModNotice(actionId = "m$at", action = "remove", createdAt = at)

    @Test
    fun onlyEventsNewerThanTheWatermark_oldestFirst() {
        val events = ForumNotificationSync.Plan.events(
            activity = listOf(answer("answer_on_post", 300), answer("reply_to_answer", 100)),
            decisions = listOf(decision(200), decision(50)),
            watermark = 60,
        )

        assertThat(events.map { it.createdAt }).containsExactly(100L, 200L, 300L).inOrder()
    }

    @Test
    fun eachKindGetsItsTypeTitleAndLink() {
        val events = ForumNotificationSync.Plan.events(
            activity = listOf(
                answer("answer_on_post", 1),
                answer("reply_to_answer", 2),
                answer("answer_accepted", 3),
            ),
            decisions = listOf(decision(4)),
            watermark = 0,
        )

        assertThat(events.map { it.type }).containsExactly(
            ForumNotificationSync.TYPE_ANSWER,
            ForumNotificationSync.TYPE_ANSWER,
            ForumNotificationSync.TYPE_ACCEPTED,
            ForumNotificationSync.TYPE_DECISION,
        ).inOrder()
        assertThat(events.map { it.title }).containsExactly(
            R.string.forum_notif_answer_title,
            R.string.forum_notif_reply_title,
            R.string.forum_notif_accepted_title,
            R.string.forum_decision_title,
        ).inOrder()
        assertThat(events.first().deeplink).isEqualTo("forum_post:p1")
        assertThat(events.last().deeplink).isEqualTo("forum_decision:m4")
    }

    @Test
    fun answerEventsLinkToTheAnswerInThePost() {
        val item = ForumActivityItem(
            type = "reply_to_answer",
            postId = "p9",
            answerId = "a3",
            postTitle = "Pyrus",
            nickname = "Ann",
            createdAt = 9,
        )
        val event = ForumNotificationSync.Plan.events(listOf(item), emptyList(), watermark = 0).single()

        assertThat(event.deeplink).isEqualTo("forum_post:p9:a3")
        assertThat(event.postId).isEqualTo("p9")
        assertThat(event.answerId).isEqualTo("a3")
    }

    @Test
    fun nothingNew_nothingStored() {
        val events = ForumNotificationSync.Plan.events(listOf(answer("answer_on_post", 5)), emptyList(), watermark = 5)

        assertThat(events).isEmpty()
    }
}
