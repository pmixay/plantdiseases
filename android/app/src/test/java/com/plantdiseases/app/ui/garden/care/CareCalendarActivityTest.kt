package com.plantdiseases.app.ui.garden.care

import android.widget.TextView
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.PlantReminderEntity
import com.plantdiseases.app.testutil.awaitOnMainLooper
import kotlinx.coroutines.runBlocking
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** The calendar follows the database: a reminder that comes due while the
 *  screen is started changes its Today card without a resume. Boots the real
 *  Hilt graph, as the screen injects from it. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class CareCalendarActivityTest {

    @Test
    fun aWriteWhileStartedUpdatesTheTodayCardWithoutOnResume() {
        // create + start only: the activity is never resumed.
        val activity = Robolectric.buildActivity(CareCalendarActivity::class.java).create().start().get()
        val subtitle = activity.findViewById<TextView>(R.id.care_calendar_today_subtitle)
        val caughtUp = activity.getString(R.string.calendar_all_caught_up)

        awaitOnMainLooper { subtitle.text.toString() == caughtUp }

        runBlocking {
            val db = activity.database
            val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
            db.plantReminderDao().upsert(
                PlantReminderEntity(
                    plantId = plantId,
                    type = "watering",
                    intervalDays = 1,
                    enabled = true,
                    // Started three days ago, due daily: a task is due today.
                    createdAt = System.currentTimeMillis() - THREE_DAYS_MS,
                ),
            )
        }

        awaitOnMainLooper { subtitle.text.toString() != caughtUp }
        assertThat(subtitle.text.toString()).isNotEmpty()
    }

    private companion object {
        const val THREE_DAYS_MS = 3L * 24 * 60 * 60 * 1000
    }
}
