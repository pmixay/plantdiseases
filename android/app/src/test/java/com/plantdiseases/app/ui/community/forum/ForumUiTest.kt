package com.plantdiseases.app.ui.community.forum

import android.app.Application
import android.content.Context
import android.text.Spanned
import android.text.style.BackgroundColorSpan
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import com.plantdiseases.app.data.repository.ForumRepository
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class ForumUiTest {

    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun highlightMarksEveryCaseInsensitiveMatch() {
        val text = ForumUi.highlight("White fuzz on white leaves", listOf("white", "x"), 0xFFFF00)
        val spanned = text as Spanned
        val spans = spanned.getSpans(0, text.length, BackgroundColorSpan::class.java)
        assertThat(spans.map { spanned.getSpanStart(it) }).containsExactly(0, 14)
    }

    @Test
    fun highlightLeavesTextAloneWithoutTerms() {
        assertThat(ForumUi.highlight("plain", emptyList(), 0)).isEqualTo("plain")
    }

    @Test
    fun serverCodesMapToSpecificMessages() {
        fun message(code: String) = ForumUi.errorMessage(context, ForumRepository.ForumException(403, code))
        assertThat(message("muted")).isEqualTo(context.getString(R.string.forum_error_muted))
        assertThat(message("level_required")).isEqualTo(context.getString(R.string.forum_error_level_required))
        assertThat(message("links_not_allowed_yet")).isEqualTo(context.getString(R.string.forum_error_links))
        assertThat(message("something_new")).isEqualTo(context.getString(R.string.forum_error_generic))
    }

    @Test
    fun everyAchievementHasItsOwnTitleAndIcon() {
        val keys = listOf(
            "first_aid", "lifeline", "rescue_ranger", "pest_control", "fungus_detective",
            "water_whisperer", "nutrient_nerd", "virus_hunter", "model_teacher", "bilingual",
            "welcome_wagon", "good_question", "closing_the_loop", "photo_journal",
        )
        val titles = keys.map { ForumUi.achievementTitle(it) }
        assertThat(titles).doesNotContain(R.string.ach_unknown)
        assertThat(titles.toSet()).hasSize(keys.size)
        assertThat(keys.map { ForumUi.achievementDescription(it) }.toSet()).hasSize(keys.size)
    }

    @Test
    fun scanPostsWithoutTitleShowTheVerdict() {
        val title = ForumUi.postTitle(context, "", "scan", "other")
        assertThat(title).isEqualTo(
            context.getString(R.string.forum_scan_title_fallback, context.getString(R.string.forum_label_other)),
        )
        assertThat(ForumUi.postTitle(context, "Spots", "scan", "pests")).isEqualTo("Spots")
    }
}
