package com.plantdiseases.app.ui.legal

import android.app.Application
import android.content.Context
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.core.widget.NestedScrollView
import androidx.test.core.app.ApplicationProvider
import com.google.android.material.chip.ChipGroup
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import com.plantdiseases.app.util.LocaleHelper
import org.junit.After
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class PrivacyPolicyActivityTest {

    private val context: Context = ApplicationProvider.getApplicationContext()

    @After
    fun resetLanguage() {
        LocaleHelper.setLocale(context, "en")
    }

    private fun open(language: String): PrivacyPolicyActivity {
        LocaleHelper.setLocale(context, language)
        return Robolectric.buildActivity(PrivacyPolicyActivity::class.java).setup().get()
    }

    private fun texts(activity: PrivacyPolicyActivity): List<TextView> {
        val content = activity.findViewById<ViewGroup>(R.id.policy_content)
        return (0 until content.childCount).map { content.getChildAt(it) as TextView }
    }

    @Test
    fun englishApp_showsEnglishPolicyInNativeViews() {
        val activity = open("en")
        val views = texts(activity)
        assertThat(views.first().text.toString()).isEqualTo("Privacy Policy")
        assertThat(views.map { it.text.toString() }).contains("What we collect")
        assertThat(views.size).isAtLeast(30)
        assertThat(activity.findViewById<ChipGroup>(R.id.language_group).checkedChipId)
            .isEqualTo(R.id.chip_lang_en)
        assertThat(activity.findViewById<View>(R.id.policy_scroll)).isInstanceOf(NestedScrollView::class.java)
    }

    @Test
    fun russianApp_showsRussianPolicy() {
        val activity = open("ru")
        assertThat(texts(activity).first().text.toString()).isEqualTo("Политика конфиденциальности")
        assertThat(activity.findViewById<ChipGroup>(R.id.language_group).checkedChipId)
            .isEqualTo(R.id.chip_lang_ru)
    }

    @Test
    fun chips_switchLanguageWithoutChangingTheAppLanguage() {
        val activity = open("en")
        activity.findViewById<View>(R.id.chip_lang_ru).performClick()
        assertThat(texts(activity).first().text.toString()).isEqualTo("Политика конфиденциальности")
        assertThat(LocaleHelper.getSavedLanguage(context)).isEqualTo("en")
        activity.findViewById<View>(R.id.chip_lang_en).performClick()
        assertThat(texts(activity).first().text.toString()).isEqualTo("Privacy Policy")
    }

    @Test
    fun contactLink_isClickable() {
        val activity = open("en")
        val last = texts(activity).last()
        assertThat(last.text.toString()).contains("@")
        assertThat(last.movementMethod).isNotNull()
        val spanned = last.text as android.text.Spanned
        val clickable = spanned.getSpans(0, spanned.length, android.text.style.ClickableSpan::class.java)
        assertThat(clickable).hasLength(1)
    }

    @Test
    fun bulletsCarryAHangingIndentSpan() {
        val activity = open("en")
        val bulletViews = texts(activity).filter {
            (it.text as android.text.Spanned)
                .getSpans(0, it.text.length, android.text.style.BulletSpan::class.java).isNotEmpty()
        }
        assertThat(bulletViews.size).isAtLeast(15)
    }
}
