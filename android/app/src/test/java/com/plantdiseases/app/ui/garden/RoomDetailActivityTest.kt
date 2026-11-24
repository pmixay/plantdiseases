package com.plantdiseases.app.ui.garden

import android.content.Intent
import android.view.ViewGroup
import android.widget.TextView
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.testutil.awaitOnMainLooper
import kotlinx.coroutines.runBlocking
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** The room screen follows the database: a write while it is started shows up
 *  without a resume. Boots the real Hilt graph, as the screen injects from it. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RoomDetailActivityTest {

    @Test
    fun aWriteWhileStartedUpdatesTheRoomWithoutOnResume() {
        val intent = Intent().putExtra(RoomDetailActivity.EXTRA_ROOM_ID, RoomDetailActivity.UNASSIGNED_ID)
        // create + start only: the activity is never resumed.
        val activity = Robolectric.buildActivity(RoomDetailActivity::class.java, intent).create().start().get()
        val plants = activity.findViewById<ViewGroup>(R.id.room_detail_plants_container)
        val title = activity.findViewById<TextView>(R.id.room_detail_title)

        awaitOnMainLooper { title.text.isNotEmpty() }
        assertThat(plants.childCount).isEqualTo(0)

        runBlocking { activity.database.plantDao().insert(PlantEntity(name = "Fig")) }

        awaitOnMainLooper { plants.childCount == 1 }
    }
}
