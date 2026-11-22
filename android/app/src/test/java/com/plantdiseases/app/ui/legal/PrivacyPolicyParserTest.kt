package com.plantdiseases.app.ui.legal

import android.app.Application
import android.net.Uri
import android.text.style.URLSpan
import androidx.core.text.HtmlCompat
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class PrivacyPolicyParserTest {

    private val realBlocks: List<PolicyBlock> by lazy {
        val context = ApplicationProvider.getApplicationContext<Application>()
        val html = context.assets.open("privacy_policy.html").bufferedReader(Charsets.UTF_8).use { it.readText() }
        PrivacyPolicyParser.parse(html)
    }

    @Test
    fun realAsset_startsWithTitleAndUpdatedLine() {
        assertThat(realBlocks[0].kind).isEqualTo(PolicyBlockKind.TITLE)
        assertThat(realBlocks[0].en).isEqualTo("Privacy Policy")
        assertThat(realBlocks[0].ru).isEqualTo("Политика конфиденциальности")
        assertThat(realBlocks[1].kind).isEqualTo(PolicyBlockKind.UPDATED)
        assertThat(realBlocks[1].en).startsWith("Last updated")
    }

    @Test
    fun realAsset_hasSectionsParagraphsAndBullets() {
        val headings = realBlocks.filter { it.kind == PolicyBlockKind.HEADING }
        assertThat(headings.size).isAtLeast(7)
        assertThat(headings.map { it.en }).containsAtLeast("What we collect", "Retention", "Contact")
        assertThat(realBlocks.count { it.kind == PolicyBlockKind.BULLET }).isAtLeast(15)
        assertThat(realBlocks.count { it.kind == PolicyBlockKind.PARAGRAPH }).isAtLeast(6)
    }

    @Test
    fun realAsset_everyBlockHasDifferentRussianAndEnglishText() {
        realBlocks.forEach { block ->
            assertThat(block.ru).isNotEmpty()
            assertThat(block.en).isNotEmpty()
        }
        // The contact address is the one block that is the same in both languages.
        realBlocks.filterNot { it.en.contains("mailto:") }.forEach { block ->
            assertThat(block.ru).isNotEqualTo(block.en)
        }
    }

    @Test
    fun realAsset_inlineBoldSurvivesAndRendersAsStyleSpan() {
        val bullet = realBlocks.first { it.kind == PolicyBlockKind.BULLET }
        assertThat(bullet.en).startsWith("<b>")
        assertThat(bullet.ru).startsWith("<b>")
        val spanned = HtmlCompat.fromHtml(bullet.en, HtmlCompat.FROM_HTML_MODE_LEGACY)
        val styles = spanned.getSpans(0, spanned.length, android.text.style.StyleSpan::class.java)
        assertThat(styles.map { it.style }).contains(android.graphics.Typeface.BOLD)
    }

    @Test
    fun realAsset_mailtoLinkSurvives() {
        val contact = realBlocks.last()
        assertThat(contact.kind).isEqualTo(PolicyBlockKind.PARAGRAPH)
        assertThat(contact.en).isEqualTo(contact.ru)
        val spanned = HtmlCompat.fromHtml(contact.en, HtmlCompat.FROM_HTML_MODE_LEGACY)
        val links = spanned.getSpans(0, spanned.length, URLSpan::class.java)
        assertThat(links).hasLength(1)
        assertThat(Uri.parse(links[0].url).scheme).isEqualTo("mailto")
    }

    @Test
    fun realAsset_leavesNoScriptOrNavigationChromeInTheText() {
        realBlocks.forEach { block ->
            assertThat(block.en).doesNotContain("function")
            assertThat(block.en).doesNotContain("data-ru")
        }
    }

    @Test
    fun attributeValuesWithMarkupAndAngleBracketsDoNotBreakScanning() {
        val html = """
            <html><body><main>
              <h2 data-ru="Раздел" data-en="Section">Раздел</h2>
              <ul>
                <li data-ru="<b>Один</b> и > два" data-en="<b>One</b> &amp; two &gt; one">fallback</li>
              </ul>
              <p class="note updated" data-ru="Дата" data-en="Date">x</p>
              <p>Plain <i>inner</i> text</p>
            </main><p data-ru="outside" data-en="outside">outside</p></body></html>
        """.trimIndent()
        val blocks = PrivacyPolicyParser.parse(html)
        assertThat(blocks.map { it.kind }).containsExactly(
            PolicyBlockKind.HEADING,
            PolicyBlockKind.BULLET,
            PolicyBlockKind.UPDATED,
            PolicyBlockKind.PARAGRAPH,
        ).inOrder()
        assertThat(blocks[1].ru).isEqualTo("<b>Один</b> и > два")
        assertThat(blocks[1].en).isEqualTo("<b>One</b> & two > one")
        assertThat(blocks[3].ru).isEqualTo("Plain <i>inner</i> text")
        assertThat(blocks[3].en).isEqualTo(blocks[3].ru)
    }

    @Test
    fun emptyOrUnrelatedInput_givesNoBlocks() {
        assertThat(PrivacyPolicyParser.parse("")).isEmpty()
        assertThat(PrivacyPolicyParser.parse("<main><div>nothing</div></main>")).isEmpty()
    }
}
