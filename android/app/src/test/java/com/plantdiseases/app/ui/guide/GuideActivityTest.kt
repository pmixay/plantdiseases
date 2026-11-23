package com.plantdiseases.app.ui.guide

import android.app.Application
import android.content.Context
import androidx.recyclerview.widget.RecyclerView
import androidx.test.core.app.ApplicationProvider
import com.google.android.material.appbar.MaterialToolbar
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import com.plantdiseases.app.data.GuideDataProvider
import com.plantdiseases.app.data.model.GuideCategory
import com.plantdiseases.app.util.LocaleHelper
import org.junit.After
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** The guide screen opens on its own (no tab shell) and lists the articles in
 *  the app's language. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class GuideActivityTest {

    private val context: Context = ApplicationProvider.getApplicationContext()

    @After
    fun resetLanguage() {
        LocaleHelper.setLocale(context, "en")
    }

    private fun open(language: String): GuideActivity {
        LocaleHelper.setLocale(context, language)
        return Robolectric.buildActivity(GuideActivity::class.java).setup().get()
    }

    @Test
    fun opensTheDiseaseArticlesUnderATitledHeader() {
        val activity = open("en")

        val toolbar = activity.findViewById<MaterialToolbar>(R.id.guide_toolbar)
        assertThat(toolbar.title).isEqualTo(activity.getString(R.string.tab_guide))
        val list = activity.findViewById<RecyclerView>(R.id.rv_guide)
        assertThat(list.adapter?.itemCount)
            .isEqualTo(GuideDataProvider.getByCategory(GuideCategory.COMMON_DISEASES).size)
    }

    @Test
    fun backArrowClosesTheScreen() {
        val activity = open("en")

        val toolbar = activity.findViewById<MaterialToolbar>(R.id.guide_toolbar)
        // The navigation button is the toolbar's first ImageButton child.
        (0 until toolbar.childCount).map(toolbar::getChildAt)
            .first { it is android.widget.ImageButton }
            .performClick()

        assertThat(activity.isFinishing).isTrue()
    }

    @Test
    fun russianArticlesComeFromTheRussianResources() {
        val activity = open("ru")

        val first = GuideDataProvider.getByCategory(GuideCategory.COMMON_DISEASES).first()
        assertThat(activity.getString(first.title)).isEqualTo("Мучнистая роса")
    }
}
