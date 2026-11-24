package com.plantdiseases.app.ui.notifications

import android.view.View
import androidx.recyclerview.widget.RecyclerView
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.NotificationEntity
import com.plantdiseases.app.testutil.awaitOnMainLooper
import kotlinx.coroutines.runBlocking
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** The notification list follows the database: a row recorded while the
 *  screen is started shows up without a resume (so without the Community sync
 *  an onResume would also start). Boots the real Hilt graph, as the screen
 *  injects from it. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class NotificationsActivityTest {

    @Test
    fun aWriteWhileStartedUpdatesTheListWithoutOnResume() {
        // create + start only: the activity is never resumed.
        val activity = Robolectric.buildActivity(NotificationsActivity::class.java).create().start().get()
        val list = activity.findViewById<RecyclerView>(R.id.rv_notifications)
        val emptyState = activity.findViewById<View>(R.id.state_layout)

        awaitOnMainLooper { emptyState.visibility == View.VISIBLE }
        assertThat(list.visibility).isEqualTo(View.GONE)

        runBlocking {
            activity.database.notificationDao().insert(
                NotificationEntity(type = "care", title = "Water the fig", body = "Today", createdAt = 1L),
            )
        }

        awaitOnMainLooper { list.visibility == View.VISIBLE }
        // One "New" heading plus the row.
        assertThat(list.adapter?.itemCount).isEqualTo(2)
        assertThat(emptyState.visibility).isEqualTo(View.GONE)
    }
}
