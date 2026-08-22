package com.plantdiseases.app.ui.garden.care

import android.content.Context
import android.view.ContextThemeWrapper
import android.widget.LinearLayout
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.Calendar

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class CareCalendarViewTest {

    @Test
    fun rapidNextClicks_advanceFromTheLatestRequestedMonth() {
        val base = ApplicationProvider.getApplicationContext<Context>()
        val view = CareCalendarView(ContextThemeWrapper(base, R.style.Theme_PlantDiseases))
        val january = Calendar.getInstance().apply {
            set(2026, Calendar.JANUARY, 1, 0, 0, 0)
            set(Calendar.MILLISECOND, 0)
        }
        view.showMonth(january, emptyMap(), selectedEpochDay = null)
        val requestedMonths = mutableListOf<Int>()
        view.onMonthChanged = { requestedMonths += it.get(Calendar.MONTH) }
        val header = view.getChildAt(0) as LinearLayout
        val nextButton = header.getChildAt(2)

        nextButton.performClick()
        nextButton.performClick()

        assertThat(requestedMonths).containsExactly(Calendar.FEBRUARY, Calendar.MARCH).inOrder()
    }
}
