package com.plantdiseases.app.ui.notifications

import android.app.Application
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.ui.community.forum.PostDetailActivity
import com.plantdiseases.app.ui.garden.PlantDetailActivity
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Deeplink routing for notification-centre rows. The vocabulary is a plain
 * "prefix:id" string, so the risk is a typo'd, retired or future-version
 * prefix reaching a build that doesn't know it: that must resolve to null
 * ("open Home") rather than throw on a user's tap.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class NotificationRouterTest {

    private val ctx: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun forumPostPrefix_opensThePost_andKeepsTheAnswer() {
        val plain = NotificationRouter.intentFor(ctx, "forum_post:p1")
        assertThat(plain!!.component?.className).isEqualTo(PostDetailActivity::class.java.name)
        assertThat(plain.getStringExtra("extra_forum_post_id")).isEqualTo("p1")
        assertThat(plain.getStringExtra("extra_forum_answer_id")).isNull()

        val answer = NotificationRouter.intentFor(ctx, "forum_post:p1:a9")
        assertThat(answer!!.getStringExtra("extra_forum_post_id")).isEqualTo("p1")
        assertThat(answer.getStringExtra("extra_forum_answer_id")).isEqualTo("a9")
    }

    @Test
    fun plantPrefix_opensPlantDetailWithId() {
        val intent = NotificationRouter.intentFor(ctx, "plant:42")
        assertThat(intent).isNotNull()
        assertThat(intent!!.component?.className)
            .isEqualTo(PlantDetailActivity::class.java.name)
        assertThat(intent.getLongExtra(PlantDetailActivity.EXTRA_PLANT_ID, -1L)).isEqualTo(42L)
    }

    @Test
    fun unusableDeeplinks_resolveToNullInsteadOfThrowing() {
        // null / blank / no separator / unknown or retired prefix / non-numeric
        // plant id all mean "just open Home" — none may throw. "community:" was
        // the retired second-opinion replies screen; old rows still carry it.
        assertThat(NotificationRouter.intentFor(ctx, null)).isNull()
        assertThat(NotificationRouter.intentFor(ctx, "")).isNull()
        assertThat(NotificationRouter.intentFor(ctx, "community")).isNull()
        assertThat(NotificationRouter.intentFor(ctx, "community:abc123")).isNull()
        assertThat(NotificationRouter.intentFor(ctx, "wormhole:1")).isNull()
        assertThat(NotificationRouter.intentFor(ctx, "plant:not-a-number")).isNull()
    }
}
