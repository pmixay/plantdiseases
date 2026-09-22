package com.plantdiseases.app.ui.notifications

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.ui.community.MyScanRepliesActivity
import com.plantdiseases.app.ui.garden.PlantDetailActivity
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Deeplink routing for notification-centre rows. The vocabulary is a plain
 * "prefix:id" string, so the risk is a typo'd or future-version prefix
 * reaching a build that doesn't know it: that must resolve to null ("open
 * Home") rather than throw on a user's tap.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class NotificationRouterTest {

    private val ctx: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun communityPrefix_opensRepliesWithScanId() {
        val intent = NotificationRouter.intentFor(ctx, "community:abc123")
        assertThat(intent).isNotNull()
        assertThat(intent!!.component?.className)
            .isEqualTo(MyScanRepliesActivity::class.java.name)
        assertThat(intent.getStringExtra(MyScanRepliesActivity.EXTRA_SCAN_ID)).isEqualTo("abc123")
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
        // null / blank / no separator / unknown prefix / non-numeric plant id
        // all mean "just open Home" — none may throw.
        assertThat(NotificationRouter.intentFor(ctx, null)).isNull()
        assertThat(NotificationRouter.intentFor(ctx, "")).isNull()
        assertThat(NotificationRouter.intentFor(ctx, "community")).isNull()
        assertThat(NotificationRouter.intentFor(ctx, "wormhole:1")).isNull()
        assertThat(NotificationRouter.intentFor(ctx, "plant:not-a-number")).isNull()
    }

    @Test
    fun scanIdKeepsColons() {
        // limit=2 split: an id containing a colon must survive intact.
        val intent = NotificationRouter.intentFor(ctx, "community:cs:a:b")
        assertThat(intent!!.getStringExtra(MyScanRepliesActivity.EXTRA_SCAN_ID)).isEqualTo("cs:a:b")
    }
}
