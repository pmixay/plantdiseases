package com.plantdiseases.app.ui.gallery

import android.os.Looper
import androidx.recyclerview.widget.RecyclerView
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.testutil.awaitOnMainLooper
import kotlinx.coroutines.runBlocking
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/** The scan history follows the database: a scan saved while the screen is
 *  started shows up without a resume. Boots the real Hilt graph (the fragment
 *  injects from it) through the activity that hosts it. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class GalleryFragmentTest {

    private fun scan() = ScanEntity(
        imagePath = "/tmp/x.jpg",
        diseaseName = "Leaf spot",
        diseaseNameRu = "Пятнистость листьев",
        confidence = 0.9f,
        description = "d",
        descriptionRu = "д",
        treatment = "[]",
        treatmentRu = "[]",
        prevention = "[]",
        preventionRu = "[]",
        isHealthy = false,
    )

    @Test
    fun aWriteWhileStartedUpdatesTheGridWithoutOnResume() {
        // create + start only: the activity and its fragment are never resumed.
        val activity = Robolectric.buildActivity(MyScansActivity::class.java).create().start().get()
        shadowOf(Looper.getMainLooper()).idle()
        val fragment = activity.supportFragmentManager.findFragmentById(R.id.fragment_container) as GalleryFragment
        val grid = activity.findViewById<RecyclerView>(R.id.rv_gallery)

        awaitOnMainLooper { fragment.isAdded && !fragment.isResumed && grid.adapter != null }
        assertThat(grid.adapter?.itemCount).isEqualTo(0)

        runBlocking { fragment.database.scanDao().insert(scan()) }

        awaitOnMainLooper { grid.adapter?.itemCount == 1 }
    }
}
